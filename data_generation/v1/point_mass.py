"""Constrained 3D point-mass kinematics for Stage 1 trajectory generation.

The state is a ground-track state in Local ENU.  It deliberately contains no
body attitude, motor, actuator, trim, or controller state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class PointMassState:
    """One ENU point-mass state using track, rather than body-yaw, coordinates."""

    position_enu_m: np.ndarray
    horizontal_speed_mps: float
    track_heading_rad: float
    vertical_speed_mps: float
    track_turn_rate_rad_s: float


@dataclass(frozen=True, slots=True)
class PointMassTarget:
    """Explicit target values for the constrained kinematic state."""

    horizontal_speed_mps: float
    vertical_speed_mps: float
    track_turn_rate_rad_s: float


@dataclass(frozen=True, slots=True)
class PointMassLimits:
    """Profile-supplied limits for one point-mass object category."""

    minimum_horizontal_speed_mps: float
    maximum_horizontal_speed_mps: float
    maximum_vertical_speed_mps: float
    maximum_track_turn_rate_rad_s: float
    horizontal_acceleration_max_mps2: float
    tangential_acceleration_max_mps2: float
    vertical_acceleration_max_mps2: float
    maximum_track_path_angle_rad: float | None = None


def _finite_scalar(value: object, *, name: str) -> float:
    """Convert one scalar while rejecting booleans and non-finite values."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _position(value: object) -> np.ndarray:
    """Validate the independent ENU position vector without retaining a caller alias."""
    try:
        position = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("position_enu_m must be a finite 3-vector") from error
    if position.shape != (3,) or not np.all(np.isfinite(position)):
        raise ValueError("position_enu_m must be a finite 3-vector")
    return position.copy()


def _wrap_heading(angle_rad: float) -> float:
    """Keep an angle finite and stable without conflating it with body yaw."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _validate_limits(limits: PointMassLimits) -> tuple[float, ...]:
    """Return normalized limits after rejecting incomplete or incompatible budgets."""
    if not isinstance(limits, PointMassLimits):
        raise ValueError("limits must be PointMassLimits")
    minimum_speed = _finite_scalar(
        limits.minimum_horizontal_speed_mps, name="minimum_horizontal_speed_mps"
    )
    maximum_speed = _finite_scalar(
        limits.maximum_horizontal_speed_mps, name="maximum_horizontal_speed_mps"
    )
    vertical_speed = _finite_scalar(
        limits.maximum_vertical_speed_mps, name="maximum_vertical_speed_mps"
    )
    turn_rate = _finite_scalar(
        limits.maximum_track_turn_rate_rad_s, name="maximum_track_turn_rate_rad_s"
    )
    horizontal_acceleration = _finite_scalar(
        limits.horizontal_acceleration_max_mps2, name="horizontal_acceleration_max_mps2"
    )
    tangential_acceleration = _finite_scalar(
        limits.tangential_acceleration_max_mps2, name="tangential_acceleration_max_mps2"
    )
    vertical_acceleration = _finite_scalar(
        limits.vertical_acceleration_max_mps2, name="vertical_acceleration_max_mps2"
    )
    if minimum_speed < 0.0 or maximum_speed < minimum_speed:
        raise ValueError("horizontal speed limits are invalid")
    if any(value <= 0.0 for value in (vertical_speed, turn_rate, horizontal_acceleration)):
        raise ValueError("point-mass maximum limits must be positive")
    if tangential_acceleration <= 0.0 or vertical_acceleration <= 0.0:
        raise ValueError("point-mass acceleration limits must be positive")
    path_angle = limits.maximum_track_path_angle_rad
    if path_angle is None:
        normalized_path_angle = math.nan
    else:
        normalized_path_angle = _finite_scalar(path_angle, name="maximum_track_path_angle_rad")
        if not 0.0 < normalized_path_angle < math.pi / 2.0:
            raise ValueError("maximum_track_path_angle_rad must be in (0, pi/2)")
    return (
        minimum_speed,
        maximum_speed,
        vertical_speed,
        turn_rate,
        horizontal_acceleration,
        tangential_acceleration,
        vertical_acceleration,
        normalized_path_angle,
    )


def _validate_state(
    state: PointMassState,
    *,
    minimum_speed: float,
    maximum_speed: float,
    maximum_vertical_speed: float,
    maximum_turn_rate: float,
    horizontal_acceleration_max: float,
    maximum_path_angle: float,
) -> tuple[np.ndarray, float, float, float, float]:
    """Reject an invalid runtime state before an integration step begins."""
    if not isinstance(state, PointMassState):
        raise ValueError("state must be PointMassState")
    position = _position(state.position_enu_m)
    speed = _finite_scalar(state.horizontal_speed_mps, name="horizontal_speed_mps")
    heading = _finite_scalar(state.track_heading_rad, name="track_heading_rad")
    vertical_speed = _finite_scalar(state.vertical_speed_mps, name="vertical_speed_mps")
    turn_rate = _finite_scalar(state.track_turn_rate_rad_s, name="track_turn_rate_rad_s")
    if not minimum_speed <= speed <= maximum_speed:
        raise ValueError("state horizontal_speed_mps is outside the configured limits")
    if abs(vertical_speed) > maximum_vertical_speed:
        raise ValueError("state vertical_speed_mps is outside the configured limits")
    if abs(turn_rate) > maximum_turn_rate:
        raise ValueError("state track_turn_rate_rad_s is outside the configured limits")
    if speed * abs(turn_rate) > horizontal_acceleration_max + _EPSILON:
        raise ValueError("state turn demand exceeds the shared horizontal acceleration budget")
    if not math.isnan(maximum_path_angle):
        path_angle = abs(math.atan2(vertical_speed, speed))
        if path_angle > maximum_path_angle + _EPSILON:
            raise ValueError("state track path angle exceeds the configured maximum")
    return position, speed, heading, vertical_speed, turn_rate


def _validate_target(
    target: PointMassTarget,
    *,
    minimum_speed: float,
    maximum_speed: float,
    maximum_vertical_speed: float,
    maximum_turn_rate: float,
    horizontal_acceleration_max: float,
    maximum_path_angle: float,
) -> tuple[float, float, float]:
    """Reject impossible targets rather than clipping them into a profile boundary."""
    if not isinstance(target, PointMassTarget):
        raise ValueError("target must be PointMassTarget")
    speed = _finite_scalar(target.horizontal_speed_mps, name="target horizontal_speed_mps")
    vertical_speed = _finite_scalar(
        target.vertical_speed_mps, name="target vertical_speed_mps"
    )
    turn_rate = _finite_scalar(
        target.track_turn_rate_rad_s, name="target track_turn_rate_rad_s"
    )
    if speed < minimum_speed:
        raise ValueError("target horizontal_speed_mps is below the configured minimum speed")
    if speed > maximum_speed:
        raise ValueError("target horizontal_speed_mps exceeds the configured maximum speed")
    if abs(vertical_speed) > maximum_vertical_speed:
        raise ValueError("target vertical_speed_mps exceeds the configured maximum speed")
    if abs(turn_rate) > maximum_turn_rate:
        raise ValueError("target track_turn_rate_rad_s exceeds the configured maximum")
    if not math.isnan(maximum_path_angle):
        path_angle = abs(math.atan2(vertical_speed, speed))
        if path_angle > maximum_path_angle + _EPSILON:
            raise ValueError("target track path angle exceeds the configured maximum")
    if speed * abs(turn_rate) > horizontal_acceleration_max + _EPSILON:
        raise ValueError("target turn demand exceeds the shared horizontal acceleration budget")
    return speed, vertical_speed, turn_rate


def _move_toward(current: float, target: float, *, maximum_rate: float, dt_s: float) -> float:
    """Make one rate-limited scalar update without overshooting a target."""
    delta = target - current
    allowed_delta = maximum_rate * dt_s
    if abs(delta) <= allowed_delta:
        return target
    return current + math.copysign(allowed_delta, delta)


def _horizontal_displacement(
    speed_mps: float, heading_rad: float, turn_rate_rad_s: float, dt_s: float
) -> np.ndarray:
    """Integrate constant midpoint speed and track turn rate exactly over one step."""
    turn_angle = turn_rate_rad_s * dt_s
    if abs(turn_rate_rad_s) <= _EPSILON:
        return np.array(
            [
                speed_mps * dt_s * math.cos(heading_rad),
                speed_mps * dt_s * math.sin(heading_rad),
            ],
            dtype=float,
        )
    scale = speed_mps / turn_rate_rad_s
    return np.array(
        [
            scale * (math.sin(heading_rad + turn_angle) - math.sin(heading_rad)),
            scale * (math.cos(heading_rad) - math.cos(heading_rad + turn_angle)),
        ],
        dtype=float,
    )


def advance_point_mass(
    state: PointMassState,
    target: PointMassTarget,
    limits: PointMassLimits,
    dt_s: float,
) -> PointMassState:
    """Advance one constrained state using velocity integration only.

    The target turn rate is an explicitly checked track command.  A speed change is
    reduced to the remaining vector budget after the normal acceleration demand,
    instead of allowing independent tangential and turn accelerations to add beyond
    the configured horizontal bound.
    """
    dt = _finite_scalar(dt_s, name="dt_s")
    if dt <= 0.0:
        raise ValueError("dt_s must be positive")
    (
        minimum_speed,
        maximum_speed,
        maximum_vertical_speed,
        maximum_turn_rate,
        horizontal_acceleration_max,
        tangential_acceleration_max,
        vertical_acceleration_max,
        maximum_path_angle,
    ) = _validate_limits(limits)
    position, speed, heading, vertical_speed, _ = _validate_state(
        state,
        minimum_speed=minimum_speed,
        maximum_speed=maximum_speed,
        maximum_vertical_speed=maximum_vertical_speed,
        maximum_turn_rate=maximum_turn_rate,
        horizontal_acceleration_max=horizontal_acceleration_max,
        maximum_path_angle=maximum_path_angle,
    )
    target_speed, target_vertical_speed, target_turn_rate = _validate_target(
        target,
        minimum_speed=minimum_speed,
        maximum_speed=maximum_speed,
        maximum_vertical_speed=maximum_vertical_speed,
        maximum_turn_rate=maximum_turn_rate,
        horizontal_acceleration_max=horizontal_acceleration_max,
        maximum_path_angle=maximum_path_angle,
    )

    maximum_speed_for_turn = max(speed, target_speed)
    normal_acceleration_bound = maximum_speed_for_turn * abs(target_turn_rate)
    if normal_acceleration_bound > horizontal_acceleration_max + _EPSILON:
        raise ValueError(
            "target turn demand exceeds the shared horizontal acceleration budget at current speed"
        )
    remaining_horizontal_budget = math.sqrt(
        max(0.0, horizontal_acceleration_max**2 - normal_acceleration_bound**2)
    )
    permitted_tangential_acceleration = min(
        tangential_acceleration_max, remaining_horizontal_budget
    )
    next_speed = _move_toward(
        speed,
        target_speed,
        maximum_rate=permitted_tangential_acceleration,
        dt_s=dt,
    )
    next_vertical_speed = _move_toward(
        vertical_speed,
        target_vertical_speed,
        maximum_rate=vertical_acceleration_max,
        dt_s=dt,
    )
    if not math.isnan(maximum_path_angle):
        next_path_angle = abs(math.atan2(next_vertical_speed, next_speed))
        if next_path_angle > maximum_path_angle + _EPSILON:
            raise ValueError("next state track path angle exceeds the configured maximum")
    midpoint_speed = (speed + next_speed) / 2.0
    horizontal_displacement = _horizontal_displacement(
        midpoint_speed, heading, target_turn_rate, dt
    )
    next_position = position.copy()
    next_position[:2] += horizontal_displacement
    next_position[2] += (vertical_speed + next_vertical_speed) * dt / 2.0
    next_heading = _wrap_heading(heading + target_turn_rate * dt)

    tangent_acceleration = (next_speed - speed) / dt
    normal_acceleration = midpoint_speed * target_turn_rate
    assert math.hypot(tangent_acceleration, normal_acceleration) <= (
        horizontal_acceleration_max + 1e-10
    )
    assert abs((next_vertical_speed - vertical_speed) / dt) <= vertical_acceleration_max + 1e-10
    assert np.all(np.isfinite(next_position))

    return PointMassState(
        position_enu_m=next_position,
        horizontal_speed_mps=next_speed,
        track_heading_rad=next_heading,
        vertical_speed_mps=next_vertical_speed,
        track_turn_rate_rad_s=target_turn_rate,
    )
