"""Episode execution for the Stage 1 constrained point-mass profiles.

This runner intentionally advances only the shared point-mass state.  It does
not import a legacy X8 or Crazyflie runner, and it does not add the planned
Stage 2 smooth entry/exit policy to the explicitly scheduled Stage 1 commands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral

import numpy as np

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S, SAMPLE_COUNT

from .point_mass import PointMassState, advance_point_mass
from .point_mass_profile import (
    PointMassCommand,
    point_mass_commands_from_profile,
    point_mass_limits_from_profile,
    sample_initial_point_mass_state,
    validate_point_mass_profile,
)
from .records import MotionEpisode, MotionEvent

_EPSILON = 1e-12
_DIAGNOSTIC_NAMES = (
    "actual_horizontal_normal_acceleration_mps2",
    "actual_horizontal_speed_mps",
    "actual_horizontal_tangential_acceleration_mps2",
    "actual_track_turn_rate_rad_s",
    "actual_vertical_acceleration_mps2",
    "actual_vertical_speed_mps",
    "command_index",
    "target_horizontal_speed_mps",
    "target_track_turn_rate_rad_s",
    "target_vertical_speed_mps",
    "track_heading_rad",
)


@dataclass(frozen=True, slots=True)
class _ScheduledPointMassCommand:
    """A validated command mapped to its exact observation-window interval."""

    index: int
    command: PointMassCommand
    start_s: float
    end_s: float


def _validate_seed(seed: int) -> int:
    """Keep the runner's deterministic seed contract aligned with profile sampling."""
    if isinstance(seed, bool) or not isinstance(seed, Integral) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be a uint32 integer")
    return int(seed)


def _schedule_commands(
    commands: tuple[PointMassCommand, ...],
) -> tuple[_ScheduledPointMassCommand, ...]:
    """Make all profile durations explicit, including the final observation tail."""
    scheduled: list[_ScheduledPointMassCommand] = []
    start_s = 0.0
    for index, command in enumerate(commands):
        end_s = DURATION_S if command.duration_s is None else start_s + command.duration_s
        if end_s <= start_s or end_s > DURATION_S + _EPSILON:
            raise ValueError("point-mass command schedule does not fit the observation window")
        scheduled.append(
            _ScheduledPointMassCommand(
                index=index,
                command=command,
                start_s=start_s,
                end_s=min(end_s, DURATION_S),
            )
        )
        start_s = end_s
    if not scheduled or not math.isclose(start_s, DURATION_S, rel_tol=0.0, abs_tol=_EPSILON):
        raise ValueError("point-mass commands must cover the complete observation window")
    return tuple(scheduled)


def _command_at_time(
    commands: tuple[_ScheduledPointMassCommand, ...], time_s: float
) -> _ScheduledPointMassCommand:
    """Use the next command at an exact boundary, matching forward integration."""
    if not 0.0 <= time_s <= DURATION_S + _EPSILON:
        raise ValueError("time must stay within the observation window")
    for command in commands:
        if time_s < command.end_s - _EPSILON:
            return command
    return commands[-1]


def _velocity_from_state(state: PointMassState) -> np.ndarray:
    """Build the ENU velocity from track variables without introducing body yaw."""
    velocity = np.array(
        [
            state.horizontal_speed_mps * math.cos(state.track_heading_rad),
            state.horizontal_speed_mps * math.sin(state.track_heading_rad),
            state.vertical_speed_mps,
        ],
        dtype=float,
    )
    assert velocity.shape == (3,)
    assert np.all(np.isfinite(velocity))
    return velocity


def _append_sample(
    *,
    sample_index: int,
    state: PointMassState,
    command: _ScheduledPointMassCommand,
    positions: np.ndarray,
    velocities: np.ndarray,
    diagnostics: dict[str, np.ndarray],
) -> None:
    """Record actual state and active target separately in evaluation diagnostics."""
    positions[sample_index] = state.position_enu_m
    velocities[sample_index] = _velocity_from_state(state)
    target = command.command.target
    diagnostics["target_horizontal_speed_mps"][sample_index] = target.horizontal_speed_mps
    diagnostics["target_vertical_speed_mps"][sample_index] = target.vertical_speed_mps
    diagnostics["target_track_turn_rate_rad_s"][sample_index] = target.track_turn_rate_rad_s
    diagnostics["actual_horizontal_speed_mps"][sample_index] = state.horizontal_speed_mps
    diagnostics["actual_vertical_speed_mps"][sample_index] = state.vertical_speed_mps
    diagnostics["actual_track_turn_rate_rad_s"][sample_index] = state.track_turn_rate_rad_s
    diagnostics["track_heading_rad"][sample_index] = state.track_heading_rad
    diagnostics["actual_horizontal_normal_acceleration_mps2"][sample_index] = (
        state.horizontal_speed_mps * state.track_turn_rate_rad_s
    )
    diagnostics["command_index"][sample_index] = command.index


def _derive_output_accelerations(diagnostics: dict[str, np.ndarray]) -> None:
    """Store output-grid derivatives, not hidden integration-state accelerations."""
    horizontal_speed = diagnostics["actual_horizontal_speed_mps"]
    vertical_speed = diagnostics["actual_vertical_speed_mps"]
    horizontal_acceleration = np.empty(SAMPLE_COUNT, dtype=float)
    vertical_acceleration = np.empty(SAMPLE_COUNT, dtype=float)
    horizontal_acceleration[0] = 0.0
    vertical_acceleration[0] = 0.0
    horizontal_acceleration[1:] = np.diff(horizontal_speed) / OUTPUT_DT_S
    vertical_acceleration[1:] = np.diff(vertical_speed) / OUTPUT_DT_S
    diagnostics["actual_horizontal_tangential_acceleration_mps2"] = horizontal_acceleration
    diagnostics["actual_vertical_acceleration_mps2"] = vertical_acceleration
    assert all(values.shape == (SAMPLE_COUNT,) for values in diagnostics.values())
    assert all(np.all(np.isfinite(values)) for values in diagnostics.values())


def _event_records(
    commands: tuple[_ScheduledPointMassCommand, ...],
) -> tuple[MotionEvent, ...]:
    """Separate completed configured holds from the open observation-window tail."""
    completed = tuple(
        MotionEvent(
            event_id=f"point_mass_command_{command.index}_{command.command.command_id}",
            start_s=command.start_s,
            end_s=command.end_s,
            trigger_kind="configured_duration",
            completed=True,
            completion_reason="configured_duration_elapsed",
        )
        for command in commands[:-1]
    )
    tail = commands[-1]
    return completed + (
        MotionEvent(
            event_id=f"point_mass_window_{tail.index}_{tail.command.command_id}",
            start_s=tail.start_s,
            end_s=DURATION_S,
            trigger_kind="observation_window",
            completed=False,
            completion_reason="episode_window_cut_off",
        ),
    )


def run_point_mass_episode(seed: int, profile: dict) -> MotionEpisode:
    """Generate one deterministic 60-second, 5 Hz constrained point-mass episode."""
    normalized_seed = _validate_seed(seed)
    validate_point_mass_profile(profile)
    limits = point_mass_limits_from_profile(profile)
    integration_dt_s = float(profile["motion"]["integration_dt_s"])
    commands = _schedule_commands(point_mass_commands_from_profile(profile))
    state = sample_initial_point_mass_state(normalized_seed, profile)

    steps = np.arange(SAMPLE_COUNT, dtype=int)
    time_s = steps.astype(float) * OUTPUT_DT_S
    positions = np.empty((SAMPLE_COUNT, 3), dtype=float)
    velocities = np.empty((SAMPLE_COUNT, 3), dtype=float)
    diagnostics = {
        name: np.empty(SAMPLE_COUNT, dtype=float)
        for name in _DIAGNOSTIC_NAMES
        if name
        not in {
            "actual_horizontal_tangential_acceleration_mps2",
            "actual_vertical_acceleration_mps2",
        }
    }
    _append_sample(
        sample_index=0,
        state=state,
        command=_command_at_time(commands, 0.0),
        positions=positions,
        velocities=velocities,
        diagnostics=diagnostics,
    )

    now_s = 0.0
    for sample_index in range(1, SAMPLE_COUNT):
        output_end_s = float(time_s[sample_index])
        while now_s < output_end_s - _EPSILON:
            active = _command_at_time(commands, now_s)
            segment_end_s = min(output_end_s, active.end_s)
            dt_s = min(integration_dt_s, segment_end_s - now_s)
            if dt_s <= _EPSILON:
                now_s = segment_end_s
                continue
            state = advance_point_mass(state, active.command.target, limits, dt_s)
            now_s += dt_s
            if math.isclose(now_s, segment_end_s, rel_tol=0.0, abs_tol=_EPSILON):
                now_s = segment_end_s
        if not math.isclose(now_s, output_end_s, rel_tol=0.0, abs_tol=_EPSILON):
            raise AssertionError("point-mass integration did not reach an output timestamp")
        _append_sample(
            sample_index=sample_index,
            state=state,
            command=_command_at_time(commands, now_s),
            positions=positions,
            velocities=velocities,
            diagnostics=diagnostics,
        )

    _derive_output_accelerations(diagnostics)
    assert positions.shape == (SAMPLE_COUNT, 3)
    assert velocities.shape == (SAMPLE_COUNT, 3)
    assert np.all(np.isfinite(positions))
    assert np.all(np.isfinite(velocities))
    assert math.isclose(float(time_s[-1]), DURATION_S, rel_tol=0.0, abs_tol=_EPSILON)
    return MotionEpisode(
        episode_id=f"{profile['model_id']}-{normalized_seed:08d}",
        status="complete",
        failure_reason=None,
        seed=normalized_seed,
        steps=steps,
        t_s=time_s,
        position_enu_m=positions,
        velocity_enu_mps=velocities,
        diagnostic_arrays=diagnostics,
        event_records=_event_records(commands),
    )


__all__ = ["run_point_mass_episode"]
