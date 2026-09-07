"""Compact, bounded kinematic motion for approved VTOL/helicopter v1 profiles.

This is a simulation-design engine.  It does not reproduce rotor, actuator,
attitude, or aircraft-device dynamics from the cited sources.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from numbers import Integral

import numpy as np

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S, SAMPLE_COUNT

from .bounded_motion_scenario import (
    MotionLimits,
    MotionScenario,
    PhaseMotionScenario,
    ScheduledMotionCommand,
    build_motion_scenario,
    build_phase_motion_scenario,
    parse_motion_limits,
)
from .records import MotionEpisode, MotionEvent
from .vtol_phase_motion import PhaseMotionRuntime

_EPSILON = 1e-12
_DIAGNOSTIC_NAMES = (
    "target_speed_mps",
    "target_vertical_speed_mps",
    "target_turn_rate_deg_s",
    "actual_speed_mps",
    "track_heading_deg",
    "actual_turn_rate_deg_s",
    "horizontal_accel_mps2",
    "vertical_accel_mps2",
    "mode_index",
    "segment_index",
)


def _seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("seed must be a uint32 integer")
    normalized = int(value)
    if not 0 <= normalized <= int(np.iinfo(np.uint32).max):
        raise ValueError("seed must be a uint32 integer")
    return normalized


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _wrap_angle(angle_rad: float) -> float:
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _heading_degrees(angle_rad: float) -> float:
    return math.degrees(_wrap_angle(angle_rad))


def _move_toward(
    current: float,
    target: float,
    *,
    response_s: float,
    magnitude_limit: float,
    dt_s: float,
) -> float:
    """First-order target response with a finite per-step saturation."""
    requested_rate = (target - current) / response_s
    bounded_rate = _clamp(requested_rate, -magnitude_limit, magnitude_limit)
    delta = bounded_rate * dt_s
    if abs(delta) > abs(target - current):
        delta = target - current
    return current + delta


def _velocity(speed_mps: float, heading_rad: float) -> np.ndarray:
    return np.array(
        [speed_mps * math.cos(heading_rad), speed_mps * math.sin(heading_rad)], dtype=float
    )


def _command_at(
    commands: tuple[ScheduledMotionCommand, ...], time_s: float
) -> ScheduledMotionCommand:
    for command in commands:
        if time_s < command.end_s - _EPSILON:
            return command
    return commands[-1]


def _effective_mode_label(
    command: ScheduledMotionCommand,
    *,
    speed_mps: float,
    vertical_speed_mps: float,
    turn_rate_rad_s: float,
    scenario: MotionScenario | PhaseMotionScenario,
) -> str:
    """Avoid assigning a steady label before the continuous response reaches it."""
    label = command.mode.mode_class
    if (
        label == "cruise"
        and scenario.cruise_min_speed_mps is not None
        and speed_mps < scenario.cruise_min_speed_mps - _EPSILON
    ):
        return "transition"
    if (
        label == "hover"
        and math.hypot(speed_mps, vertical_speed_mps) > scenario.hover_speed_tolerance_mps
    ):
        return "decelerating"
    hover_turn_rate_tolerance = getattr(scenario, "hover_turn_rate_tolerance_rad_s", None)
    if (
        label == "hover"
        and hover_turn_rate_tolerance is not None
        and abs(turn_rate_rad_s) > hover_turn_rate_tolerance
    ):
        return "decelerating"
    return label


def _target_reached(
    command: ScheduledMotionCommand,
    *,
    speed_mps: float,
    vertical_speed_mps: float,
    turn_rate_rad_s: float,
    scenario: MotionScenario,
) -> bool:
    if abs(speed_mps - command.target_speed_mps) > scenario.target_tolerance_speed_mps:
        return False
    if (
        abs(vertical_speed_mps - command.target_vertical_speed_mps)
        > scenario.target_tolerance_vertical_speed_mps
    ):
        return False
    if (
        abs(turn_rate_rad_s - command.target_turn_rate_rad_s)
        > scenario.target_tolerance_turn_rate_rad_s
    ):
        return False
    return _effective_mode_label(
        command,
        speed_mps=speed_mps,
        vertical_speed_mps=vertical_speed_mps,
        turn_rate_rad_s=turn_rate_rad_s,
        scenario=scenario,
    ) == command.mode.mode_class


def _horizontal_update(
    *,
    speed_mps: float,
    heading_rad: float,
    turn_rate_rad_s: float,
    target_speed_mps: float,
    target_turn_rate_rad_s: float,
    limits: MotionLimits,
) -> tuple[float, float, float, np.ndarray, np.ndarray, float]:
    """Advance horizontal velocity while bounding its full vector change.

    Tangential speed response and normal turning draw from the same acceleration
    budget.  The final vector-ball projection makes the bound exact even when
    both commands change in a single step.
    """
    old_velocity = _velocity(speed_mps, heading_rad)
    tangential_accel = _clamp(
        (target_speed_mps - speed_mps) / limits.speed_response_s,
        -limits.horizontal_accel_max_mps2,
        limits.horizontal_accel_max_mps2,
    )
    if abs(tangential_accel * limits.dt_s) > abs(target_speed_mps - speed_mps):
        tangential_accel = (target_speed_mps - speed_mps) / limits.dt_s
    candidate_speed = _clamp(
        speed_mps + tangential_accel * limits.dt_s,
        0.0,
        limits.horizontal_speed_max_mps,
    )
    response_turn_rate = _move_toward(
        turn_rate_rad_s,
        target_turn_rate_rad_s,
        response_s=limits.turn_response_s,
        magnitude_limit=limits.turn_rate_max_rad_s / limits.turn_response_s,
        dt_s=limits.dt_s,
    )
    response_turn_rate = _clamp(
        response_turn_rate, -limits.turn_rate_max_rad_s, limits.turn_rate_max_rad_s
    )
    speed_for_turn = max(0.5 * (speed_mps + candidate_speed), _EPSILON)
    available_normal_accel = math.sqrt(
        max(0.0, limits.horizontal_accel_max_mps2**2 - tangential_accel**2)
    )
    turn_accel_limit = available_normal_accel / speed_for_turn
    if speed_for_turn <= _EPSILON:
        turn_accel_limit = limits.turn_rate_max_rad_s
    candidate_turn_rate = _clamp(
        response_turn_rate,
        -min(limits.turn_rate_max_rad_s, turn_accel_limit),
        min(limits.turn_rate_max_rad_s, turn_accel_limit),
    )
    candidate_heading = heading_rad + candidate_turn_rate * limits.dt_s
    candidate_velocity = _velocity(candidate_speed, candidate_heading)
    velocity_change = candidate_velocity - old_velocity
    maximum_change = limits.horizontal_accel_max_mps2 * limits.dt_s
    change_norm = float(np.linalg.norm(velocity_change))
    if change_norm > maximum_change:
        candidate_velocity = old_velocity + velocity_change * (maximum_change / change_norm)
    new_speed = float(np.linalg.norm(candidate_velocity))
    if new_speed > _EPSILON:
        new_heading = math.atan2(float(candidate_velocity[1]), float(candidate_velocity[0]))
        actual_turn_rate = _wrap_angle(new_heading - heading_rad) / limits.dt_s
    else:
        new_heading = candidate_heading
        actual_turn_rate = candidate_turn_rate
    if not np.all(np.isfinite(candidate_velocity)):
        raise FloatingPointError("bounded horizontal velocity became non-finite")
    actual_accel = float(np.linalg.norm(candidate_velocity - old_velocity) / limits.dt_s)
    if actual_accel > limits.horizontal_accel_max_mps2 + 1e-10:
        raise FloatingPointError("bounded horizontal acceleration exceeded its configured limit")
    return (
        new_speed,
        new_heading,
        actual_turn_rate,
        old_velocity,
        candidate_velocity,
        actual_accel,
    )


def _vertical_update(
    *,
    vertical_speed_mps: float,
    target_vertical_speed_mps: float,
    limits: MotionLimits,
) -> tuple[float, float]:
    vertical_accel = _clamp(
        (target_vertical_speed_mps - vertical_speed_mps) / limits.vertical_response_s,
        -limits.vertical_accel_max_mps2,
        limits.vertical_accel_max_mps2,
    )
    if abs(vertical_accel * limits.dt_s) > abs(target_vertical_speed_mps - vertical_speed_mps):
        vertical_accel = (target_vertical_speed_mps - vertical_speed_mps) / limits.dt_s
    new_vertical_speed = _clamp(
        vertical_speed_mps + vertical_accel * limits.dt_s,
        -limits.vertical_speed_max_mps,
        limits.vertical_speed_max_mps,
    )
    actual_accel = abs(new_vertical_speed - vertical_speed_mps) / limits.dt_s
    if actual_accel > limits.vertical_accel_max_mps2 + 1e-10:
        raise FloatingPointError("bounded vertical acceleration exceeded its configured limit")
    return new_vertical_speed, actual_accel


def _append_sample(
    *,
    sample_index: int,
    position_enu_m: np.ndarray,
    horizontal_velocity_mps: np.ndarray,
    vertical_speed_mps: float,
    speed_mps: float,
    heading_rad: float,
    turn_rate_rad_s: float,
    horizontal_accel_mps2: float,
    vertical_accel_mps2: float,
    command: ScheduledMotionCommand,
    scenario: MotionScenario | PhaseMotionScenario,
    positions: np.ndarray,
    velocities: np.ndarray,
    diagnostics: dict[str, np.ndarray],
) -> None:
    positions[sample_index] = position_enu_m
    velocities[sample_index] = [
        horizontal_velocity_mps[0],
        horizontal_velocity_mps[1],
        vertical_speed_mps,
    ]
    effective_label = _effective_mode_label(
        command,
        speed_mps=speed_mps,
        vertical_speed_mps=vertical_speed_mps,
        turn_rate_rad_s=turn_rate_rad_s,
        scenario=scenario,
    )
    diagnostics["target_speed_mps"][sample_index] = command.target_speed_mps
    diagnostics["target_vertical_speed_mps"][sample_index] = command.target_vertical_speed_mps
    diagnostics["target_turn_rate_deg_s"][sample_index] = math.degrees(
        command.target_turn_rate_rad_s
    )
    diagnostics["actual_speed_mps"][sample_index] = speed_mps
    diagnostics["track_heading_deg"][sample_index] = _heading_degrees(heading_rad)
    diagnostics["actual_turn_rate_deg_s"][sample_index] = math.degrees(turn_rate_rad_s)
    diagnostics["horizontal_accel_mps2"][sample_index] = horizontal_accel_mps2
    diagnostics["vertical_accel_mps2"][sample_index] = vertical_accel_mps2
    diagnostics["mode_index"][sample_index] = scenario.mode_index_map[effective_label]
    diagnostics["segment_index"][sample_index] = command.index


def _events(
    scenario: MotionScenario, reached_at_s: list[float | None]
) -> tuple[MotionEvent, ...]:
    records = []
    for command, completion_time_s in zip(scenario.commands, reached_at_s, strict=True):
        if completion_time_s is not None and completion_time_s > command.end_s + _EPSILON:
            raise FloatingPointError("bounded command completion was recorded after its end time")
        if command.was_cut_off:
            completed = False
            reason = "episode_window_cut_off"
        elif completion_time_s is not None:
            completed = True
            reason = "target_response_tolerance_met"
        else:
            completed = False
            reason = "scheduled_command_window_elapsed"
        records.append(
            MotionEvent(
                event_id=f"segment-{command.index:03d}-{command.mode.name}",
                start_s=command.start_s,
                end_s=command.end_s,
                trigger_kind="simulation_design_command",
                completed=completed,
                completion_reason=reason,
            )
        )
    return tuple(records)


def _uses_phase_execution_policy(profile: Mapping[str, object]) -> bool:
    experiment = profile.get("experiment")
    return isinstance(experiment, Mapping) and "execution_policy" in experiment


def validate_bounded_motion_profile(profile: Mapping[str, object]) -> None:
    """Fail closed on a non-bounded engine or an invalid command design."""
    if not isinstance(profile, Mapping) or profile.get("engine") != "bounded_motion":
        raise ValueError("profile engine must be bounded_motion")
    if "physics" in profile:
        raise ValueError("bounded_motion profiles use motion, not physics")
    limits = parse_motion_limits(profile.get("motion"))
    if _uses_phase_execution_policy(profile):
        build_phase_motion_scenario(profile, limits=limits, rng=np.random.default_rng(0))
    else:
        build_motion_scenario(profile, limits=limits, rng=np.random.default_rng(0))


def run_bounded_motion_episode(seed: int, profile: Mapping[str, object]) -> MotionEpisode:
    """Generate one continuous 60-second Local-ENU bounded-motion episode.

    Horizontal velocity is first-order responsive to speed and track-turn
    commands, then projected onto the exact per-step acceleration ball.  Thus
    forward acceleration and centripetal turning share one configured bound.
    """
    normalized_seed = _seed(seed)
    if not isinstance(profile, Mapping) or profile.get("engine") != "bounded_motion":
        raise ValueError("profile engine must be bounded_motion")
    if "physics" in profile:
        raise ValueError("bounded_motion profiles use motion, not physics")
    limits = parse_motion_limits(profile.get("motion"))
    rng = np.random.default_rng(normalized_seed)
    phase_runtime: PhaseMotionRuntime | None = None
    if _uses_phase_execution_policy(profile):
        scenario: MotionScenario | PhaseMotionScenario = build_phase_motion_scenario(
            profile, limits=limits, rng=rng
        )
        phase_runtime = PhaseMotionRuntime(scenario)
    else:
        scenario = build_motion_scenario(profile, limits=limits, rng=rng)
    output_stride = round(OUTPUT_DT_S / limits.dt_s)
    total_steps = round(DURATION_S / limits.dt_s)
    assert output_stride > 0
    assert total_steps == output_stride * (SAMPLE_COUNT - 1)

    position = scenario.initial_position_enu_m.copy()
    speed = scenario.initial_speed_mps
    heading = scenario.initial_heading_rad
    vertical_speed = scenario.initial_vertical_speed_mps
    turn_rate = scenario.initial_turn_rate_rad_s
    horizontal_velocity = _velocity(speed, heading)
    positions = np.empty((SAMPLE_COUNT, 3), dtype=float)
    velocities = np.empty((SAMPLE_COUNT, 3), dtype=float)
    diagnostics = {name: np.empty(SAMPLE_COUNT, dtype=float) for name in _DIAGNOSTIC_NAMES}
    reached_at_s: list[float | None] = []
    if phase_runtime is None:
        assert isinstance(scenario, MotionScenario)
        reached_at_s = [None] * len(scenario.commands)

    initial_command = (
        phase_runtime.command_at(0.0)
        if phase_runtime is not None
        else _command_at(scenario.commands, 0.0)
    )
    _append_sample(
        sample_index=0,
        position_enu_m=position,
        horizontal_velocity_mps=horizontal_velocity,
        vertical_speed_mps=vertical_speed,
        speed_mps=speed,
        heading_rad=heading,
        turn_rate_rad_s=turn_rate,
        horizontal_accel_mps2=0.0,
        vertical_accel_mps2=0.0,
        command=initial_command,
        scenario=scenario,
        positions=positions,
        velocities=velocities,
        diagnostics=diagnostics,
    )
    if phase_runtime is None and _target_reached(
        initial_command,
        speed_mps=speed,
        vertical_speed_mps=vertical_speed,
        turn_rate_rad_s=turn_rate,
        scenario=scenario,
    ):
        reached_at_s[initial_command.index] = 0.0

    status = "complete"
    failure_reason: str | None = None
    last_written_sample_index = 0
    for internal_step in range(1, total_steps + 1):
        time_s = (internal_step - 1) * limits.dt_s
        base_step_end_s = internal_step * limits.dt_s
        horizontal_accel = 0.0
        vertical_accel = 0.0
        phase_failed = False
        while time_s < base_step_end_s - _EPSILON:
            command = (
                phase_runtime.command_at(time_s)
                if phase_runtime is not None
                else _command_at(scenario.commands, time_s)
            )
            substep_end_s = min(base_step_end_s, command.end_s)
            substep_dt_s = substep_end_s - time_s
            if substep_dt_s <= _EPSILON:
                raise FloatingPointError("bounded command schedule did not advance time")
            step_limits = replace(limits, dt_s=substep_dt_s)
            (
                speed,
                heading,
                turn_rate,
                previous_horizontal_velocity,
                horizontal_velocity,
                horizontal_accel,
            ) = _horizontal_update(
                speed_mps=speed,
                heading_rad=heading,
                turn_rate_rad_s=turn_rate,
                target_speed_mps=command.target_speed_mps,
                target_turn_rate_rad_s=command.target_turn_rate_rad_s,
                limits=step_limits,
            )
            next_vertical_speed, vertical_accel = _vertical_update(
                vertical_speed_mps=vertical_speed,
                target_vertical_speed_mps=command.target_vertical_speed_mps,
                limits=step_limits,
            )
            heading_change = (
                _wrap_angle(
                    heading
                    - math.atan2(
                        float(previous_horizontal_velocity[1]),
                        float(previous_horizontal_velocity[0]),
                    )
                )
                if np.linalg.norm(previous_horizontal_velocity) > _EPSILON
                else 0.0
            )
            if (
                abs(speed - float(np.linalg.norm(previous_horizontal_velocity))) <= _EPSILON
                and abs(heading_change) > _EPSILON
            ):
                angular_rate = heading_change / step_limits.dt_s
                previous_heading = heading - heading_change
                position[:2] += np.array(
                    [
                        speed / angular_rate * (math.sin(heading) - math.sin(previous_heading)),
                        speed / angular_rate * (-math.cos(heading) + math.cos(previous_heading)),
                    ]
                )
            else:
                position[:2] += (
                    0.5 * (previous_horizontal_velocity + horizontal_velocity) * step_limits.dt_s
            )
            position[2] += 0.5 * (vertical_speed + next_vertical_speed) * step_limits.dt_s
            vertical_speed = next_vertical_speed
            time_s = substep_end_s
            target_reached = _target_reached(
                command,
                speed_mps=speed,
                vertical_speed_mps=vertical_speed,
                turn_rate_rad_s=turn_rate,
                scenario=scenario,
            )
            if phase_runtime is not None:
                phase_runtime.observe(time_s, target_reached=target_reached)
                if phase_runtime.has_failed:
                    status = "failed"
                    failure_reason = phase_runtime.failure_reason
                    assert failure_reason is not None
                    phase_failed = True
                    break
            elif target_reached and reached_at_s[command.index] is None:
                reached_at_s[command.index] = time_s
        if phase_failed:
            failed_command = phase_runtime.command_at(time_s)
            for sample_index in range(last_written_sample_index + 1, SAMPLE_COUNT):
                _append_sample(
                    sample_index=sample_index,
                    position_enu_m=position,
                    horizontal_velocity_mps=horizontal_velocity,
                    vertical_speed_mps=vertical_speed,
                    speed_mps=speed,
                    heading_rad=heading,
                    turn_rate_rad_s=turn_rate,
                    horizontal_accel_mps2=0.0,
                    vertical_accel_mps2=0.0,
                    command=failed_command,
                    scenario=scenario,
                    positions=positions,
                    velocities=velocities,
                    diagnostics=diagnostics,
                )
            break
        if not np.all(np.isfinite(position)):
            raise FloatingPointError("bounded position became non-finite")
        if internal_step % output_stride == 0:
            sample_index = internal_step // output_stride
            sample_command = (
                phase_runtime.command_at(time_s)
                if phase_runtime is not None
                else _command_at(scenario.commands, time_s)
            )
            _append_sample(
                sample_index=sample_index,
                position_enu_m=position,
                horizontal_velocity_mps=horizontal_velocity,
                vertical_speed_mps=vertical_speed,
                speed_mps=speed,
                heading_rad=heading,
                turn_rate_rad_s=turn_rate,
                horizontal_accel_mps2=horizontal_accel,
                vertical_accel_mps2=vertical_accel,
                command=sample_command,
                scenario=scenario,
                positions=positions,
                velocities=velocities,
                diagnostics=diagnostics,
            )
            last_written_sample_index = sample_index

    assert positions.shape == (SAMPLE_COUNT, 3)
    assert velocities.shape == (SAMPLE_COUNT, 3)
    assert np.all(np.isfinite(positions))
    assert np.all(np.isfinite(velocities))
    if phase_runtime is not None and status == "complete":
        phase_runtime.finalize_episode(DURATION_S)
    return MotionEpisode(
        episode_id=f"{scenario.object_id}-{normalized_seed:010d}",
        status=status,
        failure_reason=failure_reason,
        seed=normalized_seed,
        steps=np.arange(SAMPLE_COUNT, dtype=int),
        t_s=np.arange(SAMPLE_COUNT, dtype=float) * OUTPUT_DT_S,
        position_enu_m=positions,
        velocity_enu_mps=velocities,
        diagnostic_arrays=diagnostics,
        event_records=(
            phase_runtime.event_records
            if phase_runtime is not None
            else _events(scenario, reached_at_s)
        ),
    )
