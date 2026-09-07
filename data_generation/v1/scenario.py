"""Event-driven generation of one continuous, calm-air X8 trajectory."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .control import (
    FlightTarget,
    FlightTargetUpdate,
    X8Autopilot,
    apply_target_update,
    wrap_angle_rad,
)
from .x8 import (
    ActuatorCommand,
    CommandDelay,
    X8Dynamics,
    integrate_rk4,
    ned_to_enu,
    quaternion_from_euler,
    quaternion_to_euler,
    quaternion_to_rotation,
    solve_calm_trim,
)

PHYSICS_DT_S = 0.0025
CONTROL_DT_S = 0.01
OUTPUT_DT_S = 0.2
EPISODE_DURATION_S = 60.0
OUTPUT_SAMPLE_COUNT = 301
MAX_EVENTS = 64

assert int(CONTROL_DT_S / PHYSICS_DT_S) == 4
assert int(OUTPUT_DT_S / CONTROL_DT_S) == 20
assert OUTPUT_SAMPLE_COUNT == int(EPISODE_DURATION_S / OUTPUT_DT_S) + 1


@dataclass(slots=True)
class ResponseLatch:
    """Directional response progress for one changed target channel."""

    start_value: float
    target_value: float
    changed_threshold: float
    progress: float = 0.0
    changed: bool = False
    latched: bool = False

    def __post_init__(self) -> None:
        self.changed = abs(self.target_value - self.start_value) >= self.changed_threshold

    def observe(self, actual_value: float, *, fraction: float) -> None:
        """Latch only after a signed response reaches the configured fraction."""
        if not self.changed:
            self.progress = 1.0
            self.latched = True
            return
        self.progress = (actual_value - self.start_value) / (self.target_value - self.start_value)
        if self.progress >= fraction:
            self.latched = True


@dataclass(frozen=True, slots=True)
class ManeuverEvent:
    """One partial target update and the condition that advances the event queue."""

    event_id: str
    update: FlightTargetUpdate
    trigger_kind: str
    trigger_value: float
    response_fraction: float = 0.7


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Audit record for a completed or episode-end event."""

    event_id: str
    start_s: float
    end_s: float
    trigger_kind: str
    completed: bool
    completion_reason: str
    final_course_change_rad: float


@dataclass(frozen=True, slots=True)
class GeneratedEpisode:
    """Continuous simulation output retained internally before CSV serialization."""

    episode_id: str
    status: str
    failure_reason: str | None
    seed: int
    steps: np.ndarray
    t_s: np.ndarray
    position_enu_m: np.ndarray
    velocity_enu_mps: np.ndarray
    euler_rad: np.ndarray
    airspeed_mps: np.ndarray
    alpha_rad: np.ndarray
    beta_rad: np.ndarray
    raw_commands: np.ndarray
    event_records: tuple[EventRecord, ...]


@dataclass(slots=True)
class _ActiveEvent:
    event: ManeuverEvent
    start_s: float
    start_position_enu_m: np.ndarray
    start_altitude_up_m: float
    start_course_rad: float
    latches: dict[str, ResponseLatch]


def _flight_values(state: np.ndarray) -> tuple[float, float, float, float, float, float]:
    rotation_bn = quaternion_to_rotation(state[6:10])
    ground_velocity_ned = rotation_bn @ state[3:6]
    airspeed = float(np.linalg.norm(state[3:6]))
    u, v, w = state[3:6]
    alpha = math.atan2(float(w), float(u))
    beta = math.asin(float(np.clip(v / max(airspeed, 1e-6), -1.0, 1.0)))
    euler = quaternion_to_euler(state[6:10])
    vertical_speed_up = -float(ground_velocity_ned[2])
    return airspeed, alpha, beta, float(euler[0]), float(euler[1]), vertical_speed_up


def _unwrapped_delta(current_rad: float, start_rad: float) -> float:
    return wrap_angle_rad(current_rad - start_rad)


def _default_events() -> tuple[ManeuverEvent, ...]:
    """Use the agreed straight, turning, climb/descent, and compound event sequence."""
    return (
        ManeuverEvent(
            "straight_100m",
            FlightTargetUpdate(airspeed_mps=18.0, bank_rad=0.0, climb_mps=0.0, course_hold=True),
            "distance",
            100.0,
        ),
        ManeuverEvent(
            "left_turn",
            FlightTargetUpdate(bank_rad=math.radians(-10.0), course_hold=False),
            "heading_change",
            math.radians(-30.0),
        ),
        ManeuverEvent("add_climb", FlightTargetUpdate(climb_mps=0.5), "response", 0.0),
        ManeuverEvent(
            "recover_and_accelerate",
            FlightTargetUpdate(airspeed_mps=19.0, bank_rad=0.0, climb_mps=0.0, course_hold=True),
            "response",
            0.0,
        ),
        ManeuverEvent(
            "right_turn",
            FlightTargetUpdate(bank_rad=math.radians(10.0), course_hold=False),
            "heading_change",
            math.radians(30.0),
        ),
        ManeuverEvent("add_descent", FlightTargetUpdate(climb_mps=-0.5), "response", 0.0),
        ManeuverEvent(
            "final_straight",
            FlightTargetUpdate(airspeed_mps=18.0, bank_rad=0.0, climb_mps=0.0, course_hold=True),
            "episode_end",
            EPISODE_DURATION_S,
        ),
    )


def _start_event(
    event: ManeuverEvent,
    *,
    now_s: float,
    state: np.ndarray,
    target: FlightTarget,
    autopilot: X8Autopilot,
) -> tuple[_ActiveEvent, FlightTarget]:
    updated_target = apply_target_update(target, event.update)
    autopilot.set_target(updated_target, state, reset_course=event.update.course_hold is True)
    airspeed, _, _, roll, _, vertical_speed = _flight_values(state)
    euler = quaternion_to_euler(state[6:10])
    response_targets = {
        "airspeed": (airspeed, updated_target.airspeed_mps, 0.1),
        "bank": (roll, updated_target.bank_rad, math.radians(0.5)),
        "climb": (vertical_speed, updated_target.climb_mps, 0.05),
    }
    latches: dict[str, ResponseLatch] = {}
    if event.trigger_kind == "response":
        for name, (start, desired, threshold) in response_targets.items():
            if (
                (name == "airspeed" and event.update.airspeed_mps is not None)
                or (name == "bank" and event.update.bank_rad is not None)
                or (name == "climb" and event.update.climb_mps is not None)
            ):
                latches[name] = ResponseLatch(start, desired, threshold)
    return (
        _ActiveEvent(
            event=event,
            start_s=now_s,
            start_position_enu_m=ned_to_enu(state[0:3]),
            start_altitude_up_m=-float(state[2]),
            start_course_rad=float(euler[2]),
            latches=latches,
        ),
        updated_target,
    )


def _event_complete(active: _ActiveEvent, state: np.ndarray) -> bool:
    event = active.event
    position_enu = ned_to_enu(state[0:3])
    course = float(quaternion_to_euler(state[6:10])[2])
    if event.trigger_kind == "distance":
        return (
            float(np.linalg.norm(position_enu[:2] - active.start_position_enu_m[:2]))
            >= event.trigger_value
        )
    if event.trigger_kind == "heading_change":
        heading_change = _unwrapped_delta(course, active.start_course_rad)
        return (
            heading_change >= event.trigger_value
            if event.trigger_value >= 0.0
            else heading_change <= event.trigger_value
        )
    if event.trigger_kind == "altitude_change":
        altitude_change = -float(state[2]) - active.start_altitude_up_m
        return (
            altitude_change >= event.trigger_value
            if event.trigger_value >= 0.0
            else altitude_change <= event.trigger_value
        )
    if event.trigger_kind == "response":
        airspeed, _, _, roll, _, vertical_speed = _flight_values(state)
        values = {
            "airspeed": airspeed,
            "bank": roll,
            "climb": vertical_speed,
        }
        for name, latch in active.latches.items():
            latch.observe(values[name], fraction=event.response_fraction)
        return bool(active.latches) and all(latch.latched for latch in active.latches.values())
    if event.trigger_kind == "episode_end":
        return False
    raise ValueError(f"unsupported trigger kind: {event.trigger_kind}")


def _working_region_failure(state: np.ndarray) -> str | None:
    airspeed, alpha, beta, roll, pitch, _ = _flight_values(state)
    if not 14.0 <= airspeed <= 22.0:
        return "airspeed_out_of_range"
    if abs(roll) > math.radians(45.0):
        return "bank_out_of_range"
    if abs(pitch) > math.radians(30.0):
        return "pitch_out_of_range"
    if not math.radians(-5.0) <= alpha <= math.radians(15.0):
        return "alpha_out_of_range"
    if abs(beta) > math.radians(10.0):
        return "beta_out_of_range"
    if state[13] <= 0.0:
        return "propeller_stopped"
    return None


def _record_sample(
    state: np.ndarray,
    command: ActuatorCommand,
    positions: list[np.ndarray],
    velocities: list[np.ndarray],
    eulers: list[np.ndarray],
    airspeeds: list[float],
    alphas: list[float],
    betas: list[float],
    commands: list[np.ndarray],
) -> None:
    airspeed, alpha, beta, _, _, _ = _flight_values(state)
    rotation_bn = quaternion_to_rotation(state[6:10])
    positions.append(ned_to_enu(state[0:3]))
    velocities.append(ned_to_enu(rotation_bn @ state[3:6]))
    eulers.append(quaternion_to_euler(state[6:10]))
    airspeeds.append(airspeed)
    alphas.append(alpha)
    betas.append(beta)
    commands.append(np.array([command.left_rad, command.right_rad, command.throttle], dtype=float))


def run_default_x8_episode(seed: int) -> GeneratedEpisode:
    """Generate exactly one 60-second X8 trajectory using the fixed event sequence."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0]))
    dynamics = X8Dynamics.default()
    trim = solve_calm_trim(dynamics, airspeed_mps=18.0)
    state = trim.state.copy()
    initial_enu = np.array(
        [rng.uniform(-500.0, 500.0), rng.uniform(-500.0, 500.0), rng.uniform(80.0, 120.0)],
        dtype=float,
    )
    state[0:3] = ned_to_enu(initial_enu)
    initial_yaw = float(rng.uniform(-math.pi, math.pi))
    state[6:10] = quaternion_from_euler(trim.phi_rad, trim.theta_rad, initial_yaw)
    initial_target = FlightTarget(18.0, 0.0, 0.0, True)
    autopilot = X8Autopilot(dynamics, trim.state, trim.command)
    autopilot.set_target(initial_target, state, reset_course=True)
    delayed_command = trim.command
    delay = CommandDelay(CONTROL_DT_S, trim.command)
    events = _default_events()
    if len(events) > MAX_EVENTS:
        raise ValueError("default scenario exceeds the X8-GEN-V1 event limit")

    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    eulers: list[np.ndarray] = []
    airspeeds: list[float] = []
    alphas: list[float] = []
    betas: list[float] = []
    commands: list[np.ndarray] = []
    _record_sample(
        state,
        delayed_command,
        positions,
        velocities,
        eulers,
        airspeeds,
        alphas,
        betas,
        commands,
    )
    target = initial_target
    active: _ActiveEvent | None = None
    event_index = 0
    records: list[EventRecord] = []
    status = "complete"
    failure_reason: str | None = None
    control_steps = int(EPISODE_DURATION_S / CONTROL_DT_S)

    for control_step in range(control_steps):
        now_s = control_step * CONTROL_DT_S
        if active is None and event_index < len(events):
            active, target = _start_event(
                events[event_index],
                now_s=now_s,
                state=state,
                target=target,
                autopilot=autopilot,
            )
        diagnostics = autopilot.step(state, delayed_command, CONTROL_DT_S)
        raw_command = diagnostics.raw_command
        delayed_command = delay.advance(raw_command)
        state = integrate_rk4(
            dynamics,
            state,
            delayed_command,
            duration_s=CONTROL_DT_S,
            dt_s=PHYSICS_DT_S,
        )
        failure_reason = _working_region_failure(state)
        if failure_reason is not None:
            status = "failed"
            break
        now_after_s = (control_step + 1) * CONTROL_DT_S
        if active is not None and _event_complete(active, state):
            course = float(quaternion_to_euler(state[6:10])[2])
            records.append(
                EventRecord(
                    event_id=active.event.event_id,
                    start_s=active.start_s,
                    end_s=now_after_s,
                    trigger_kind=active.event.trigger_kind,
                    completed=True,
                    completion_reason="triggered",
                    final_course_change_rad=_unwrapped_delta(course, active.start_course_rad),
                )
            )
            event_index += 1
            active = None
        if (control_step + 1) % int(OUTPUT_DT_S / CONTROL_DT_S) == 0:
            _record_sample(
                state,
                delayed_command,
                positions,
                velocities,
                eulers,
                airspeeds,
                alphas,
                betas,
                commands,
            )

    if active is not None and status == "complete":
        course = float(quaternion_to_euler(state[6:10])[2])
        records.append(
            EventRecord(
                event_id=active.event.event_id,
                start_s=active.start_s,
                end_s=EPISODE_DURATION_S,
                trigger_kind=active.event.trigger_kind,
                completed=False,
                completion_reason="episode_end",
                final_course_change_rad=_unwrapped_delta(course, active.start_course_rad),
            )
        )
    if status == "complete" and len(positions) != OUTPUT_SAMPLE_COUNT:
        status = "partial"
        failure_reason = "output_sample_count"

    sample_count = len(positions)
    steps = np.arange(sample_count, dtype=int)
    return GeneratedEpisode(
        episode_id=f"x8-{seed:08d}",
        status=status,
        failure_reason=failure_reason,
        seed=seed,
        steps=steps,
        t_s=steps.astype(float) * OUTPUT_DT_S,
        position_enu_m=np.asarray(positions, dtype=float),
        velocity_enu_mps=np.asarray(velocities, dtype=float),
        euler_rad=np.asarray(eulers, dtype=float),
        airspeed_mps=np.asarray(airspeeds, dtype=float),
        alpha_rad=np.asarray(alphas, dtype=float),
        beta_rad=np.asarray(betas, dtype=float),
        raw_commands=np.asarray(commands, dtype=float),
        event_records=tuple(records),
    )


__all__ = [
    "FlightTarget",
    "FlightTargetUpdate",
    "GeneratedEpisode",
    "ResponseLatch",
    "apply_target_update",
    "run_default_x8_episode",
]
