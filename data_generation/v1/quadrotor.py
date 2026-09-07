"""Published-parameter Crazyflie rigid-body and motor-lag dynamics in Local ENU."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

STATE_SIZE = 17
ROTOR_COUNT = 4


class _NonfiniteDynamicsError(ArithmeticError):
    """Signal a finite input whose evaluated physical state cannot be represented."""


def _finite_scalar(value: object, *, name: str, positive: bool = False) -> float:
    """Convert one scalar while retaining a clear physical validation error."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(parsed) or (positive and parsed <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return parsed


def _finite_vector(value: object, *, name: str, shape: tuple[int, ...]) -> np.ndarray:
    """Convert one physics/configuration vector with an exact expected shape."""
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite array with shape {shape}")
    return array


def _normalized_motor_values(value: object, *, name: str) -> np.ndarray:
    """Validate the physical normalized motor interval used by the published fit."""
    motors = _finite_vector(value, name=name, shape=(ROTOR_COUNT,))
    if np.any(motors < 0.0) or np.any(motors > 1.0):
        raise ValueError(f"{name} values must lie in [0, 1]")
    return motors


def quaternion_normalize(quaternion_wxyz: object) -> np.ndarray:
    """Return a finite unit scalar-first body-to-world quaternion."""
    quaternion = _finite_vector(quaternion_wxyz, name="quaternion_wxyz", shape=(4,))
    norm = float(np.linalg.norm(quaternion))
    if norm <= np.finfo(float).eps:
        raise ValueError("quaternion_wxyz must have nonzero norm")
    return quaternion / norm


def quaternion_to_rotation(quaternion_wxyz: object) -> np.ndarray:
    """Return the FLU body-to-Local-ENU rotation matrix for a scalar-first quaternion."""
    w, x, y, z = quaternion_normalize(quaternion_wxyz)
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=float,
    )


def quaternion_from_yaw(yaw_rad: float) -> np.ndarray:
    """Construct an identity-roll/pitch Local-ENU yaw quaternion."""
    yaw = _finite_scalar(yaw_rad, name="yaw_rad")
    half_yaw = yaw / 2.0
    return np.array([math.cos(half_yaw), 0.0, 0.0, math.sin(half_yaw)], dtype=float)


def quaternion_to_euler(quaternion_wxyz: object) -> np.ndarray:
    """Return standard roll, pitch, yaw angles from a scalar-first body-to-world quaternion."""
    w, x, y, z = quaternion_normalize(quaternion_wxyz)
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch_argument = float(np.clip(2.0 * (w * y - z * x), -1.0, 1.0))
    pitch = math.asin(pitch_argument)
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return np.array([roll, pitch, yaw], dtype=float)


def _quaternion_derivative(quaternion_wxyz: np.ndarray, body_rates_radps: np.ndarray) -> np.ndarray:
    """Compute q_dot = 0.5 q tensor [0, omega] for scalar-first body rates."""
    w, x, y, z = quaternion_wxyz
    p, q, r = body_rates_radps
    return 0.5 * np.array(
        [
            -x * p - y * q - z * r,
            w * p + y * r - z * q,
            w * q - x * r + z * p,
            w * r + x * q - y * p,
        ],
        dtype=float,
    )


@dataclass(slots=True)
class QuadrotorDynamics:
    """17-state published Crazyflie model with force, derivative, and RK4 step APIs.

    ``force_and_torque(motor_state)`` accepts four normalized physical motor
    states and returns body force/moment. ``derivative(state, motor_command)``
    returns the continuous 17-vector derivative. ``step(state, motor_command,
    dt_s)`` applies an RK4 step and renormalizes its quaternion explicitly.
    """

    physics: Mapping[str, object]
    mass_kg: float = field(init=False)
    gravity_mps2: float = field(init=False)
    inertia_kg_m2: np.ndarray = field(init=False)
    rotor_positions_body_m: np.ndarray = field(init=False)
    rotor_torque_signs: np.ndarray = field(init=False)
    thrust_coefficients_n: np.ndarray = field(init=False)
    torque_per_thrust_m: float = field(init=False)
    motor_time_constant_s: float = field(init=False)
    wrench_allocation: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.physics, Mapping):
            raise ValueError("physics must be a mapping")
        self.mass_kg = _finite_scalar(self.physics.get("mass_kg"), name="mass_kg", positive=True)
        self.gravity_mps2 = _finite_scalar(
            self.physics.get("gravity_mps2"), name="gravity_mps2", positive=True
        )
        self.inertia_kg_m2 = _finite_vector(
            self.physics.get("inertia_kg_m2"), name="inertia_kg_m2", shape=(3,)
        )
        if np.any(self.inertia_kg_m2 <= 0.0):
            raise ValueError("inertia_kg_m2 values must be positive")
        self.rotor_positions_body_m = _finite_vector(
            self.physics.get("rotor_positions_body_m"),
            name="rotor_positions_body_m",
            shape=(ROTOR_COUNT, 3),
        )
        self.rotor_torque_signs = _finite_vector(
            self.physics.get("rotor_torque_signs"),
            name="rotor_torque_signs",
            shape=(ROTOR_COUNT,),
        )
        if not np.all(np.isin(self.rotor_torque_signs, (-1.0, 1.0))):
            raise ValueError("rotor_torque_signs must contain only -1 or 1")
        self.thrust_coefficients_n = _finite_vector(
            self.physics.get("thrust_coefficients_n"),
            name="thrust_coefficients_n",
            shape=(3,),
        )
        if self.thrust_coefficients_n[2] <= 0.0:
            raise ValueError("thrust_coefficients_n quadratic coefficient must be positive")
        self.torque_per_thrust_m = _finite_scalar(
            self.physics.get("torque_per_thrust_m"),
            name="torque_per_thrust_m",
            positive=True,
        )
        self.motor_time_constant_s = _finite_scalar(
            self.physics.get("motor_time_constant_s"),
            name="motor_time_constant_s",
            positive=True,
        )
        self._validate_thrust_polynomial()
        self.wrench_allocation = np.vstack(
            (
                np.ones(ROTOR_COUNT),
                self.rotor_positions_body_m[:, 1],
                -self.rotor_positions_body_m[:, 0],
                self.rotor_torque_signs * self.torque_per_thrust_m,
            )
        )
        if np.linalg.matrix_rank(self.wrench_allocation) != ROTOR_COUNT:
            raise ValueError("rotor geometry does not provide a full-rank wrench allocation")

    def _validate_thrust_polynomial(self) -> None:
        """Reject a profile whose normalized physical interval produces nonpositive thrust."""
        c0, c1, c2 = self.thrust_coefficients_n
        vertex = float(np.clip(-c1 / (2.0 * c2), 0.0, 1.0))
        values = c0 + c1 * np.array([0.0, vertex, 1.0]) + c2 * np.array([0.0, vertex, 1.0]) ** 2
        if np.min(values) <= 0.0:
            raise ValueError("thrust polynomial must be positive on normalized [0, 1]")

    def thrust_from_normalized(self, motor_state: object) -> np.ndarray:
        """Return per-rotor thrust in newtons for four physical normalized motor states."""
        motors = _normalized_motor_values(motor_state, name="motor_state")
        c0, c1, c2 = self.thrust_coefficients_n
        thrust = c0 + c1 * motors + c2 * motors**2
        assert thrust.shape == (ROTOR_COUNT,)
        return thrust

    def command_from_thrust(self, thrust_n: object) -> np.ndarray:
        """Invert the increasing branch of the published normalized motor thrust fit."""
        thrust = _finite_vector(thrust_n, name="thrust_n", shape=(ROTOR_COUNT,))
        c0, c1, c2 = self.thrust_coefficients_n
        discriminant = c1**2 - 4.0 * c2 * (c0 - thrust)
        if np.any(discriminant < 0.0):
            raise ValueError("thrust_n is outside the invertible thrust polynomial range")
        command = (-c1 + np.sqrt(discriminant)) / (2.0 * c2)
        if np.any(command < 0.0) or np.any(command > 1.0):
            raise ValueError("thrust_n maps outside normalized [0, 1]")
        assert command.shape == (ROTOR_COUNT,)
        return command

    def force_and_torque(self, motor_state: object) -> tuple[np.ndarray, np.ndarray]:
        """Map four physical motor states to FLU body force and body moment."""
        thrust = self.thrust_from_normalized(motor_state)
        wrench = self.wrench_allocation @ thrust
        force_body = np.array([0.0, 0.0, wrench[0]], dtype=float)
        moment_body = wrench[1:].copy()
        assert force_body.shape == (3,)
        assert moment_body.shape == (3,)
        return force_body, moment_body

    def hover_command(self) -> np.ndarray:
        """Solve equal normalized motor commands that make total thrust exactly m*g."""
        per_rotor_thrust = self.mass_kg * self.gravity_mps2 / ROTOR_COUNT
        command = self.command_from_thrust(np.full(ROTOR_COUNT, per_rotor_thrust))
        assert command.shape == (ROTOR_COUNT,)
        return command

    def hover_state(self, *, yaw_rad: float) -> np.ndarray:
        """Create an airborne zero-position/velocity hover state with exact hover motors."""
        state = np.zeros(STATE_SIZE, dtype=float)
        state[6:10] = quaternion_from_yaw(yaw_rad)
        state[13:17] = self.hover_command()
        return state

    def derivative(self, state: object, motor_command: object) -> np.ndarray:
        """Return continuous Local-ENU state derivative for a held motor command."""
        values = self._validated_state(state)
        command = _normalized_motor_values(motor_command, name="motor_command")
        quaternion = quaternion_normalize(values[6:10])
        angular_velocity = values[10:13]
        force_body, moment_body = self.force_and_torque(values[13:17])
        rotation_body_to_world = quaternion_to_rotation(quaternion)
        angular_momentum = self.inertia_kg_m2 * angular_velocity

        derivative = np.empty(STATE_SIZE, dtype=float)
        derivative[0:3] = values[3:6]
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            derivative[3:6] = (
                rotation_body_to_world @ force_body / self.mass_kg
                + np.array([0.0, 0.0, -self.gravity_mps2])
            )
            derivative[6:10] = _quaternion_derivative(quaternion, angular_velocity)
            derivative[10:13] = (
                moment_body - np.cross(angular_velocity, angular_momentum)
            ) / self.inertia_kg_m2
            derivative[13:17] = (command - values[13:17]) / self.motor_time_constant_s
        if not np.all(np.isfinite(derivative)):
            raise _NonfiniteDynamicsError("quadrotor derivative is not finite")
        return derivative

    def step(self, state: object, motor_command: object, dt_s: float) -> np.ndarray:
        """Advance one held-command RK4 step and explicitly renormalize the quaternion."""
        values = self._validated_state(state)
        command = _normalized_motor_values(motor_command, name="motor_command")
        dt = _finite_scalar(dt_s, name="dt_s", positive=True)
        first = self.derivative(values, command)
        second = self.derivative(values + 0.5 * dt * first, command)
        third = self.derivative(values + 0.5 * dt * second, command)
        fourth = self.derivative(values + dt * third, command)
        advanced = values + dt * (first + 2.0 * second + 2.0 * third + fourth) / 6.0
        if not np.all(np.isfinite(advanced)):
            raise _NonfiniteDynamicsError("quadrotor RK4 state is not finite")
        advanced[6:10] = quaternion_normalize(advanced[6:10])
        self._validated_state(advanced)
        return advanced

    def allocate_wrench(
        self,
        *,
        total_thrust_n: float,
        moment_body_nm: object,
        command_min: float,
        command_max: float,
    ) -> np.ndarray:
        """Allocate one desired body wrench through physical command bounds and thrust inversion."""
        total_thrust = _finite_scalar(total_thrust_n, name="total_thrust_n")
        moment = _finite_vector(moment_body_nm, name="moment_body_nm", shape=(3,))
        lower = _finite_scalar(command_min, name="command_min")
        upper = _finite_scalar(command_max, name="command_max")
        if lower < 0.0 or upper > 1.0 or lower > upper:
            raise ValueError("command bounds must satisfy 0 <= command_min <= command_max <= 1")
        desired_wrench = np.concatenate(([total_thrust], moment))
        rotor_thrust = np.linalg.solve(self.wrench_allocation, desired_wrench)
        lower_thrust = self.thrust_from_normalized(np.full(ROTOR_COUNT, lower))[0]
        upper_thrust = self.thrust_from_normalized(np.full(ROTOR_COUNT, upper))[0]
        bounded_thrust = np.clip(rotor_thrust, lower_thrust, upper_thrust)
        return self.command_from_thrust(bounded_thrust)

    def _validated_state(self, state: object) -> np.ndarray:
        """Validate the full physical 17-vector without modifying caller-owned input."""
        values = _finite_vector(state, name="state", shape=(STATE_SIZE,))
        quaternion_normalize(values[6:10])
        _normalized_motor_values(values[13:17], name="state motor values")
        return values


__all__ = [
    "QuadrotorDynamics",
    "ROTOR_COUNT",
    "STATE_SIZE",
    "quaternion_from_yaw",
    "quaternion_normalize",
    "quaternion_to_euler",
    "quaternion_to_rotation",
]
