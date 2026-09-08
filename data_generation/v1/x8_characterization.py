"""Offline X8 motion experiments; no PX4, Gazebo, UI, or profile mutation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real

import numpy as np

from .control import MAX_BANK_COMMAND_RAD, FlightTarget, X8Autopilot
from .scenario import CONTROL_DT_S
from .x8 import (
    STATE_SIZE,
    CommandDelay,
    TrimError,
    X8Dynamics,
    integrate_rk4,
    ned_to_enu,
    quaternion_to_euler,
    quaternion_to_rotation,
    solve_calm_trim,
)

PHASES = ("baseline", "entry", "hold", "recovery", "post")
Scalar = str | float | int | bool | None


@dataclass(frozen=True)
class TrialCase:
    """Experiment commands, not identified physical limits."""

    trial_id: str
    label: str
    initial_speed_mps: float
    target_speed_mps: float
    bank_deg: float
    climb_mps: float


@dataclass(frozen=True)
class TrialSettings:
    """Explicit integration, experiment-envelope, and reporting settings."""

    physics_dt_s: float
    record_dt_s: float
    phase_durations_s: dict[str, float]
    initial_altitude_m: float
    speed_bounds_mps: tuple[float, float]
    alpha_bounds_deg: tuple[float, float]
    max_abs_beta_deg: float
    max_abs_bank_deg: float
    max_abs_pitch_deg: float
    speed_tolerance_mps: float
    bank_tolerance_deg: float
    climb_tolerance_mps: float
    terminal_dwell_s: float
    speed_floor_mps: float
    turn_rate_floor_rad_s: float


@dataclass(frozen=True)
class TrialResult:
    """A complete or failed reference experiment, including its observed prefix."""

    case: TrialCase
    status: str
    failure_reason: str | None
    samples: list[dict[str, Scalar]]
    summary: list[dict[str, Scalar]]


def _finite(value: Real, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite real number")
    if positive and value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _ticks(duration: float, dt: float, name: str) -> int:
    count = round(duration / dt)
    if count < 1 or not math.isclose(count * dt, duration, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{name} must be an integer multiple of its clock")
    return count


def validate_trial(case: TrialCase, settings: TrialSettings) -> None:
    """Reject unsupported commands and clocks before creating any simulation state."""
    for name in ("trial_id", "label"):
        value = getattr(case, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    for name in ("initial_speed_mps", "target_speed_mps", "bank_deg", "climb_mps"):
        _finite(getattr(case, name), name)
    for name in (
        "physics_dt_s",
        "record_dt_s",
        "initial_altitude_m",
        "max_abs_beta_deg",
        "max_abs_bank_deg",
        "max_abs_pitch_deg",
        "speed_tolerance_mps",
        "bank_tolerance_deg",
        "climb_tolerance_mps",
        "terminal_dwell_s",
        "speed_floor_mps",
        "turn_rate_floor_rad_s",
    ):
        _finite(getattr(settings, name), name, positive=True)
    _ticks(CONTROL_DT_S, settings.physics_dt_s, "physics_dt_s")
    _ticks(settings.record_dt_s, CONTROL_DT_S, "record_dt_s")
    if not isinstance(settings.phase_durations_s, dict) or set(settings.phase_durations_s) != set(
        PHASES
    ):
        raise ValueError(f"phase_durations_s must contain exactly {PHASES}")
    for name, duration in settings.phase_durations_s.items():
        _finite(duration, name, positive=True)
        _ticks(duration, settings.record_dt_s, name)
    for name in ("speed_bounds_mps", "alpha_bounds_deg"):
        bounds = getattr(settings, name)
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError(f"{name} must have two bounds")
        low, high = (_finite(v, name) for v in bounds)
        if low >= high:
            raise ValueError(f"{name} must be increasing")
    low, high = settings.speed_bounds_mps
    if low <= settings.speed_floor_mps:
        raise ValueError("speed lower bound must exceed the reporting speed floor")
    if not (low <= case.initial_speed_mps <= high and low <= case.target_speed_mps <= high):
        raise ValueError("requested speed is outside the experiment envelope")
    if abs(math.radians(case.bank_deg)) > MAX_BANK_COMMAND_RAD:
        raise ValueError("bank request exceeds the existing controller limit")
    if abs(case.climb_mps) >= case.target_speed_mps:
        raise ValueError("climb speed must be smaller than requested total speed")


def enu_motion(state: np.ndarray, derivative: np.ndarray) -> tuple[np.ndarray, ...]:
    """Convert body velocity derivatives to gravity-inclusive inertial acceleration."""
    state = np.asarray(state, dtype=float)
    derivative = np.asarray(derivative, dtype=float)
    if state.shape != (STATE_SIZE,) or derivative.shape != (STATE_SIZE,):
        raise ValueError("X8 state and derivative must have the declared state size")
    if not np.all(np.isfinite(state)) or not np.all(np.isfinite(derivative)):
        raise ValueError("X8 state and derivative must be finite")
    rotation = quaternion_to_rotation(state[6:10])
    velocity = ned_to_enu(rotation @ state[3:6])
    acceleration = ned_to_enu(rotation @ (derivative[3:6] + np.cross(state[10:13], state[3:6])))
    assert acceleration.shape == velocity.shape == (3,)
    return ned_to_enu(state[:3]), velocity, acceleration


def motion_metrics(
    velocity: np.ndarray,
    acceleration: np.ndarray,
    *,
    speed_floor_mps: float,
    turn_rate_floor_rad_s: float,
) -> dict[str, float | None]:
    """Use horizontal track curvature; undefined quantities remain missing."""
    _finite(speed_floor_mps, "speed_floor_mps", positive=True)
    _finite(turn_rate_floor_rad_s, "turn_rate_floor_rad_s", positive=True)
    velocity = np.asarray(velocity, dtype=float)
    acceleration = np.asarray(acceleration, dtype=float)
    if velocity.shape != (3,) or acceleration.shape != (3,):
        raise ValueError("velocity and acceleration must have three components")
    if not np.all(np.isfinite(velocity)) or not np.all(np.isfinite(acceleration)):
        raise ValueError("velocity and acceleration must be finite")
    speed = float(np.linalg.norm(velocity))
    horizontal = float(np.linalg.norm(velocity[:2]))
    cross = float(velocity[0] * acceleration[1] - velocity[1] * acceleration[0])
    turning = cross / horizontal**2 if horizontal > speed_floor_mps else None
    radius = (
        horizontal / abs(turning)
        if turning is not None and abs(turning) > turn_rate_floor_rad_s
        else None
    )
    return {
        "speed_mps": speed,
        "horizontal_speed_mps": horizontal,
        "acceleration_mps2": float(np.linalg.norm(acceleration)),
        "tangential_acceleration_mps2": (
            float(velocity @ acceleration) / speed if speed > speed_floor_mps else None
        ),
        "lateral_acceleration_mps2": cross / horizontal if horizontal > speed_floor_mps else None,
        "track_turn_rate_rad_s": turning,
        "turn_radius_m": radius,
        "flight_path_angle_deg": (
            math.degrees(math.atan2(float(velocity[2]), horizontal))
            if speed > speed_floor_mps
            else None
        ),
    }


def terminal_target_time(
    times: list[float],
    within_tolerance: list[bool],
    dwell_s: float,
) -> float | None:
    """Start of a terminal uninterrupted in-tolerance interval, if long enough."""
    _finite(dwell_s, "dwell_s", positive=True)
    if len(times) != len(within_tolerance):
        raise ValueError("times and tolerance flags must have equal length")
    if not times:
        return None
    if not all(math.isfinite(t) for t in times) or any(
        later <= earlier for earlier, later in zip(times, times[1:], strict=False)
    ):
        raise ValueError("times must be finite and strictly increasing")
    start: float | None = None
    for time, within in zip(times, within_tolerance, strict=True):
        if not within:
            start = None
        elif start is None:
            start = time
    if start is not None and times[-1] - start + 1e-10 >= dwell_s:
        return start
    return None


def _schedule(step: int, case: TrialCase, settings: TrialSettings) -> tuple[str, FlightTarget]:
    offset = 0
    phase = PHASES[-1]
    blend = 0.0
    for phase in PHASES:
        length = _ticks(settings.phase_durations_s[phase], CONTROL_DT_S, phase)
        if step < offset + length or phase == PHASES[-1]:
            fraction = min(1.0, max(0.0, (step - offset) / length))
            smooth = fraction**3 * (10.0 + fraction * (-15.0 + 6.0 * fraction))
            blend = {
                "baseline": 0.0,
                "entry": smooth,
                "hold": 1.0,
                "recovery": 1.0 - smooth,
                "post": 0.0,
            }[phase]
            break
        offset += length
    return phase, FlightTarget(
        case.initial_speed_mps + blend * (case.target_speed_mps - case.initial_speed_mps),
        math.radians(case.bank_deg) * blend,
        case.climb_mps * blend,
        case.bank_deg == 0.0 or phase in ("baseline", "post"),
    )


def _envelope_failure(row: dict[str, Scalar], state: np.ndarray, settings: TrialSettings):
    bounded = (
        ("airspeed_mps", settings.speed_bounds_mps),
        ("alpha_deg", settings.alpha_bounds_deg),
        ("beta_deg", (-settings.max_abs_beta_deg, settings.max_abs_beta_deg)),
        ("bank_deg", (-settings.max_abs_bank_deg, settings.max_abs_bank_deg)),
        ("pitch_deg", (-settings.max_abs_pitch_deg, settings.max_abs_pitch_deg)),
    )
    for field, (low, high) in bounded:
        if not low <= row[field] <= high:
            return f"{field}_outside_experiment_envelope"
    return "propeller_stopped" if state[13] <= 0.0 else None


def _summary(
    case: TrialCase,
    settings: TrialSettings,
    rows: list[dict[str, Scalar]],
    *,
    control_rows: list[dict[str, Scalar]] | None = None,
):
    history = rows if control_rows is None else control_rows
    result = []
    phase_start = 0.0
    for phase in PHASES:
        selected = [r for r in rows if r["phase"] == phase]
        phase_end = phase_start + settings.phase_durations_s[phase]
        phase_complete = bool(history) and history[-1]["t_s"] + 1e-10 >= phase_end
        summary = {
            "trial_id": case.trial_id,
            "label": case.label,
            "phase": phase,
            "phase_complete": phase_complete,
            "sample_count": len(selected),
            "initial_speed_mps": case.initial_speed_mps,
            "maneuver_target_speed_mps": case.target_speed_mps,
            "maneuver_bank_deg": case.bank_deg,
            "maneuver_climb_mps": case.climb_mps,
            "terminal_target_met": False,
            "terminal_target_time_s": None,
        }
        if phase in ("hold", "post") and phase_complete:
            # Include both boundary states and every control tick, even if CSV is decimated.
            terminal_rows = [
                r for r in history if phase_start - 1e-10 <= r["t_s"] <= phase_end + 1e-10
            ]
            flags = [
                abs(r["airspeed_mps"] - r["target_speed_mps"]) <= settings.speed_tolerance_mps
                and abs(r["bank_deg"] - r["target_bank_deg"]) <= settings.bank_tolerance_deg
                and abs(r["vz_mps"] - r["target_climb_mps"]) <= settings.climb_tolerance_mps
                for r in terminal_rows
            ]
            terminal = terminal_target_time(
                [max(0.0, r["t_s"] - phase_start) for r in terminal_rows],
                flags,
                settings.terminal_dwell_s,
            )
            summary["terminal_target_met"] = terminal is not None
            summary["terminal_target_time_s"] = terminal
        for field in (
            "airspeed_mps",
            "bank_deg",
            "vz_mps",
            "flight_path_angle_deg",
            "track_turn_rate_rad_s",
            "turn_radius_m",
            "acceleration_mps2",
            "tangential_acceleration_mps2",
            "lateral_acceleration_mps2",
            "az_mps2",
        ):
            values = [r[field] for r in selected if r[field] is not None]
            for name, function in (("min", min), ("max", max), ("median", np.median)):
                summary[f"{field}_{name}"] = float(function(values)) if values else None
        result.append(summary)
        phase_start = phase_end
    return result


def run_trial(case: TrialCase, settings: TrialSettings) -> TrialResult:
    """Integrate one explicit calm-air experiment without touching files or profiles."""
    validate_trial(case, settings)
    rows: list[dict[str, Scalar]] = []
    control_rows: list[dict[str, Scalar]] = []
    failure: str | None = None
    record_ticks = _ticks(settings.record_dt_s, CONTROL_DT_S, "record_dt_s")
    total_ticks = sum(_ticks(v, CONTROL_DT_S, k) for k, v in settings.phase_durations_s.items())
    try:
        dynamics = X8Dynamics.default()
        trim = solve_calm_trim(dynamics, airspeed_mps=case.initial_speed_mps)
        state = trim.state.copy()
        state[:3] = ned_to_enu(np.array([0.0, 0.0, settings.initial_altitude_m]))
        autopilot = X8Autopilot(dynamics, trim.state, trim.command)
        delay = CommandDelay(CONTROL_DT_S, trim.command)
        delayed = raw = trim.command
        previous_course_hold = False
        for step in range(total_ticks + 1):
            phase, target = _schedule(step, case, settings)
            autopilot.set_target(
                target, state, reset_course=target.course_hold and not previous_course_hold
            )
            previous_course_hold = target.course_hold
            if step < total_ticks:
                raw = autopilot.step(state, delayed, CONTROL_DT_S).raw_command
                delayed = delay.advance(raw)
            derivative = dynamics.derivative(state, delayed)
            position, velocity, acceleration = enu_motion(state, derivative)
            roll, pitch, _ = np.degrees(quaternion_to_euler(state[6:10]))
            airspeed = float(np.linalg.norm(state[3:6]))
            row: dict[str, Scalar] = {
                "trial_id": case.trial_id,
                "label": case.label,
                "phase": phase,
                "t_s": step * CONTROL_DT_S,
                "command_time_s": min(step, total_ticks - 1) * CONTROL_DT_S,
                "target_speed_mps": target.airspeed_mps,
                "target_bank_deg": math.degrees(target.bank_rad),
                "target_climb_mps": target.climb_mps,
                "course_hold": target.course_hold,
                "airspeed_mps": airspeed,
                "bank_deg": float(roll),
                "pitch_deg": float(pitch),
                "alpha_deg": math.degrees(math.atan2(float(state[5]), float(state[3]))),
                "beta_deg": math.degrees(math.asin(float(np.clip(state[4] / airspeed, -1, 1)))),
                "raw_left_rad": raw.left_rad,
                "raw_right_rad": raw.right_rad,
                "raw_throttle": raw.throttle,
                "applied_left_rad": delayed.left_rad,
                "applied_right_rad": delayed.right_rad,
                "applied_throttle": delayed.throttle,
            }
            for fields, vector in (
                (("x_m", "y_m", "z_m"), position),
                (("vx_mps", "vy_mps", "vz_mps"), velocity),
                (("ax_mps2", "ay_mps2", "az_mps2"), acceleration),
            ):
                row.update(zip(fields, map(float, vector), strict=True))
            row.update(
                motion_metrics(
                    velocity,
                    acceleration,
                    speed_floor_mps=settings.speed_floor_mps,
                    turn_rate_floor_rad_s=settings.turn_rate_floor_rad_s,
                )
            )
            failure = _envelope_failure(row, state, settings)
            control_rows.append(row)
            if step % record_ticks == 0 or failure:
                rows.append(row)
            if failure or step == total_ticks:
                break
            state = integrate_rk4(
                dynamics, state, delayed, duration_s=CONTROL_DT_S, dt_s=settings.physics_dt_s
            )
    except (FloatingPointError, TrimError, OverflowError) as exc:
        failure = f"{type(exc).__name__}: {exc}"
    except ValueError as exc:
        # Existing numerical primitives use ValueError for invalid floating-point state.
        # Unexpected API/config/programming errors must still propagate to the caller.
        if str(exc) not in {
            "quaternion norm must be finite and positive",
            "X8 state must be finite",
            "X8 state and derivative must be finite",
            "elevon commands must be finite",
        }:
            raise
        failure = f"{type(exc).__name__}: {exc}"
    if failure and control_rows and (not rows or rows[-1] is not control_rows[-1]):
        rows.append(control_rows[-1])
    return TrialResult(
        case,
        "failed" if failure else "complete",
        failure,
        rows,
        _summary(case, settings, rows, control_rows=control_rows),
    )
