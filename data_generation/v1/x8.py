"""Nonlinear 6-DOF Skywalker X8 model used by X8-GEN-V1.

The implementation keeps the model internally in NED / body-FRD coordinates.
Only the dataset contract converts persisted trajectory positions to Local ENU.
The aerodynamic, propulsion, and actuator values are transcribed from
Løw-Hansen et al. (2025), Tables 1 and 6--9.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.optimize import least_squares

GRAVITY_MPS2: Final = 9.80665
AIR_DENSITY_KGPM3: Final = 1.225
NOMINAL_VOLTAGE_V: Final = 15.8
STATE_SIZE: Final = 19
SERVO_NATURAL_FREQUENCY_RADPS: Final = 100.0
SERVO_DAMPING_RATIO: Final = 0.707
SERVO_DELAY_S: Final = 0.07
THROTTLE_TIME_CONSTANT_S: Final = 0.2
THROTTLE_DELAY_S: Final = 0.05

assert STATE_SIZE == 3 + 3 + 4 + 3 + 1 + 2 + 2 + 1
assert SERVO_DELAY_S > THROTTLE_DELAY_S > 0.0


class TrimError(RuntimeError):
    """Raised when a calm equilibrium cannot satisfy the declared residual gates."""


@dataclass(frozen=True, slots=True)
class X8Parameters:
    """Physical and identified aerodynamic constants for one Skywalker X8."""

    mass_kg: float = 3.364
    ix_kgm2: float = 0.325
    iy_kgm2: float = 0.140
    iz_kgm2: float = 0.400
    ixz_kgm2: float = 0.029
    mean_chord_m: float = 0.36
    span_m: float = 2.10
    area_m2: float = 0.75
    propeller_diameter_m: float = 0.3556
    propeller_inertia_kgm2: float = 0.000346
    motor_back_emf_v_per_radps: float = 0.0157
    motor_resistance_ohm: float = 0.017

    cd_zero: float = 0.058
    cd_q: float = 0.480
    cd_ct: float = -0.217
    cd_alpha_linear: float = -0.034
    cd_alpha_quadratic: float = 0.225
    cl_zero: float = -0.077
    cl_alpha: float = 2.573
    cl_q: float = 17.119
    cl_elevator: float = 1.369
    cm_zero: float = 0.027
    cm_alpha: float = -0.274
    cm_q: float = -1.608
    cm_elevator: float = -0.276
    cy_zero: float = 0.011
    cy_beta: float = -0.285
    cy_p: float = -0.270
    cy_r: float = 0.108
    cy_aileron: float = 0.097
    cl_roll_zero: float = 0.007
    cl_roll_beta: float = -0.108
    cl_roll_p: float = -0.313
    cl_roll_r: float = 0.037
    cl_roll_aileron: float = 0.102
    cn_zero: float = -0.00063
    cn_beta: float = 0.022
    cn_p: float = -0.009
    cn_r: float = -0.050
    cn_aileron: float = -0.007

    @property
    def inertia_matrix(self) -> np.ndarray:
        """Return the body-FRD inertia tensor with the published product of inertia."""
        return np.array(
            [
                [self.ix_kgm2, 0.0, -self.ixz_kgm2],
                [0.0, self.iy_kgm2, 0.0],
                [-self.ixz_kgm2, 0.0, self.iz_kgm2],
            ],
            dtype=float,
        )


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
    """Delayed command presented to the physical elevon and throttle models."""

    left_rad: float
    right_rad: float
    throttle: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.left_rad) and math.isfinite(self.right_rad)):
            raise ValueError("elevon commands must be finite")
        if not math.isfinite(self.throttle) or not 0.0 <= self.throttle <= 1.0:
            raise ValueError("throttle command must be finite and in [0, 1]")


class CommandDelay:
    """Independent transport delays for elevons and throttle, sampled at one fixed tick."""

    def __init__(self, dt_s: float, initial: ActuatorCommand) -> None:
        if not math.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError("dt_s must be positive")
        servo_steps = round(SERVO_DELAY_S / dt_s)
        throttle_steps = round(THROTTLE_DELAY_S / dt_s)
        if not math.isclose(servo_steps * dt_s, SERVO_DELAY_S, abs_tol=1e-12):
            raise ValueError("dt_s must divide the 70 ms servo delay")
        if not math.isclose(throttle_steps * dt_s, THROTTLE_DELAY_S, abs_tol=1e-12):
            raise ValueError("dt_s must divide the 50 ms throttle delay")
        self._left: deque[float] = deque([initial.left_rad] * servo_steps)
        self._right: deque[float] = deque([initial.right_rad] * servo_steps)
        self._throttle: deque[float] = deque([initial.throttle] * throttle_steps)

    def advance(self, command: ActuatorCommand) -> ActuatorCommand:
        """Advance exactly one tick and return the command whose delay just elapsed."""
        delayed = ActuatorCommand(
            left_rad=self._left.popleft(),
            right_rad=self._right.popleft(),
            throttle=self._throttle.popleft(),
        )
        self._left.append(command.left_rad)
        self._right.append(command.right_rad)
        self._throttle.append(command.throttle)
        return delayed


@dataclass(frozen=True, slots=True)
class TrimResult:
    """Calm-air equilibrium used as the initial condition for new episodes."""

    state: np.ndarray
    command: ActuatorCommand
    alpha_rad: float
    beta_rad: float
    phi_rad: float
    theta_rad: float
    residual: np.ndarray

    @property
    def acceleration_residual_mps2(self) -> float:
        return float(np.max(np.abs(self.residual[:3])))

    @property
    def angular_acceleration_residual_radps2(self) -> float:
        return float(np.max(np.abs(self.residual[3:6])))

    @property
    def rotor_acceleration_residual_radps2(self) -> float:
        return float(abs(self.residual[6]))

    @property
    def vertical_velocity_residual_mps(self) -> float:
        return float(abs(self.residual[7]))


def ned_to_enu(vector_ned: np.ndarray) -> np.ndarray:
    """Convert a 3-vector between NED and Local ENU; this permutation is involutory."""
    vector = np.asarray(vector_ned, dtype=float)
    if vector.shape != (3,):
        raise ValueError("NED/ENU conversion requires exactly three components")
    return np.array([vector[1], vector[0], -vector[2]], dtype=float)


def mix_elevons(elevator_rad: float, aileron_rad: float) -> tuple[float, float]:
    """Apply the paper's elevon convention: left=elevator+aileron, right=elevator-aileron."""
    return elevator_rad + aileron_rad, elevator_rad - aileron_rad


def normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
    """Normalize a scalar-first quaternion and reject a zero or non-finite state."""
    quaternion = np.asarray(quaternion, dtype=float)
    norm = float(np.linalg.norm(quaternion))
    if not math.isfinite(norm) or norm <= 1e-15:
        raise ValueError("quaternion norm must be finite and positive")
    return quaternion / norm


def quaternion_from_euler(phi_rad: float, theta_rad: float, psi_rad: float) -> np.ndarray:
    """Create a scalar-first body-to-NED quaternion from roll, pitch, and yaw."""
    half_phi = phi_rad / 2.0
    half_theta = theta_rad / 2.0
    half_psi = psi_rad / 2.0
    c_phi, s_phi = math.cos(half_phi), math.sin(half_phi)
    c_theta, s_theta = math.cos(half_theta), math.sin(half_theta)
    c_psi, s_psi = math.cos(half_psi), math.sin(half_psi)
    return normalize_quaternion(
        np.array(
            [
                c_phi * c_theta * c_psi + s_phi * s_theta * s_psi,
                s_phi * c_theta * c_psi - c_phi * s_theta * s_psi,
                c_phi * s_theta * c_psi + s_phi * c_theta * s_psi,
                c_phi * c_theta * s_psi - s_phi * s_theta * c_psi,
            ],
            dtype=float,
        )
    )


def quaternion_to_rotation(quaternion_bn: np.ndarray) -> np.ndarray:
    """Return the body-to-NED rotation matrix for a scalar-first quaternion."""
    q0, q1, q2, q3 = normalize_quaternion(quaternion_bn)
    return np.array(
        [
            [1.0 - 2.0 * (q2 * q2 + q3 * q3), 2.0 * (q1 * q2 - q0 * q3), 2.0 * (q1 * q3 + q0 * q2)],
            [2.0 * (q1 * q2 + q0 * q3), 1.0 - 2.0 * (q1 * q1 + q3 * q3), 2.0 * (q2 * q3 - q0 * q1)],
            [2.0 * (q1 * q3 - q0 * q2), 2.0 * (q2 * q3 + q0 * q1), 1.0 - 2.0 * (q1 * q1 + q2 * q2)],
        ],
        dtype=float,
    )


def quaternion_to_euler(quaternion_bn: np.ndarray) -> np.ndarray:
    """Return roll, pitch, yaw in radians from a body-to-NED quaternion."""
    q0, q1, q2, q3 = normalize_quaternion(quaternion_bn)
    roll = math.atan2(2.0 * (q0 * q1 + q2 * q3), 1.0 - 2.0 * (q1 * q1 + q2 * q2))
    pitch_argument = max(-1.0, min(1.0, 2.0 * (q0 * q2 - q3 * q1)))
    pitch = math.asin(pitch_argument)
    yaw = math.atan2(2.0 * (q0 * q3 + q1 * q2), 1.0 - 2.0 * (q2 * q2 + q3 * q3))
    return np.array([roll, pitch, yaw], dtype=float)


def _quaternion_derivative(quaternion_bn: np.ndarray, body_rates_radps: np.ndarray) -> np.ndarray:
    q0, q1, q2, q3 = normalize_quaternion(quaternion_bn)
    p, q, r = body_rates_radps
    return 0.5 * np.array(
        [
            -q1 * p - q2 * q - q3 * r,
            q0 * p + q2 * r - q3 * q,
            q0 * q - q1 * r + q3 * p,
            q0 * r + q1 * q - q2 * p,
        ],
        dtype=float,
    )


def _polynomial(coefficients: tuple[float, ...], value: float) -> float:
    return float(sum(coefficient * value**index for index, coefficient in enumerate(coefficients)))


@dataclass(frozen=True, slots=True)
class X8Dynamics:
    """Published X8 aerodynamic, propulsion, rigid-body, and actuator dynamics."""

    parameters: X8Parameters

    @classmethod
    def default(cls) -> X8Dynamics:
        """Construct the fixed X8-GEN-V1 parameterization."""
        return cls(parameters=X8Parameters())

    def derivative(
        self,
        state: np.ndarray,
        command: ActuatorCommand,
        *,
        wind_ned_mps: np.ndarray | None = None,
        voltage_v: float = NOMINAL_VOLTAGE_V,
    ) -> np.ndarray:
        """Evaluate the 19-state derivative in NED/body-FRD coordinates."""
        state = np.asarray(state, dtype=float)
        if state.shape != (STATE_SIZE,):
            raise ValueError(f"X8 state must have {STATE_SIZE} values")
        if not np.all(np.isfinite(state)):
            raise ValueError("X8 state must be finite")
        if not math.isfinite(voltage_v) or voltage_v <= 0.0:
            raise ValueError("voltage_v must be finite and positive")
        wind_ned = (
            np.zeros(3, dtype=float)
            if wind_ned_mps is None
            else np.asarray(wind_ned_mps, dtype=float)
        )
        if wind_ned.shape != (3,) or not np.all(np.isfinite(wind_ned)):
            raise ValueError("wind_ned_mps must be a finite three-vector")

        parameters = self.parameters
        body_velocity = state[3:6]
        quaternion_bn = normalize_quaternion(state[6:10])
        body_rates = state[10:13]
        propeller_speed = max(0.0, float(state[13]))
        left_elevon, right_elevon = state[14:16]
        left_rate, right_rate = state[16:18]
        throttle_state = float(np.clip(state[18], 0.0, 1.0))

        rotation_bn = quaternion_to_rotation(quaternion_bn)
        rotation_nb = rotation_bn.T
        body_wind = rotation_nb @ wind_ned
        air_velocity = body_velocity - body_wind
        airspeed = max(float(np.linalg.norm(air_velocity)), 1e-3)
        u_air, v_air, w_air = air_velocity
        alpha = math.atan2(w_air, u_air)
        beta = math.asin(float(np.clip(v_air / airspeed, -1.0, 1.0)))
        p_rate, q_rate, r_rate = body_rates
        p_hat = p_rate * parameters.span_m / (2.0 * airspeed)
        q_hat = q_rate * parameters.mean_chord_m / (2.0 * airspeed)
        r_hat = r_rate * parameters.span_m / (2.0 * airspeed)
        elevator = (left_elevon + right_elevon) / 2.0
        aileron = (left_elevon - right_elevon) / 2.0

        advance_ratio = (
            2.0 * math.pi * airspeed / max(propeller_speed * parameters.propeller_diameter_m, 1e-6)
        )
        thrust_coefficient = _polynomial((0.1400, -0.0300, -0.2370, 0.0847), advance_ratio)
        torque_coefficient = _polynomial((0.0082, 0.0112, -0.0211), advance_ratio)
        thrust_n = (
            AIR_DENSITY_KGPM3
            * parameters.propeller_diameter_m**4
            / (4.0 * math.pi**2)
            * thrust_coefficient
            * propeller_speed**2
        )
        propeller_torque_nm = (
            AIR_DENSITY_KGPM3
            * parameters.propeller_diameter_m**5
            / (4.0 * math.pi**2)
            * torque_coefficient
            * propeller_speed**2
        )
        motor_current_a = (
            voltage_v * throttle_state - propeller_speed * parameters.motor_back_emf_v_per_radps
        ) / parameters.motor_resistance_ohm
        propeller_speed_dot = (
            motor_current_a * parameters.motor_back_emf_v_per_radps - propeller_torque_nm
        ) / parameters.propeller_inertia_kgm2
        if propeller_speed <= 0.0 and propeller_speed_dot < 0.0:
            propeller_speed_dot = 0.0

        cd = (
            parameters.cd_zero
            + parameters.cd_alpha_linear * alpha
            + parameters.cd_alpha_quadratic * alpha**2
            + parameters.cd_q * q_hat
            + parameters.cd_ct * thrust_coefficient
        )
        cl = (
            parameters.cl_zero
            + parameters.cl_alpha * alpha
            + parameters.cl_q * q_hat
            + parameters.cl_elevator * elevator
        )
        cm = (
            parameters.cm_zero
            + parameters.cm_alpha * alpha
            + parameters.cm_q * q_hat
            + parameters.cm_elevator * elevator
        )
        cy = (
            parameters.cy_zero
            + parameters.cy_beta * beta
            + parameters.cy_p * p_hat
            + parameters.cy_r * r_hat
            + parameters.cy_aileron * aileron
        )
        cl_roll = (
            parameters.cl_roll_zero
            + parameters.cl_roll_beta * beta
            + parameters.cl_roll_p * p_hat
            + parameters.cl_roll_r * r_hat
            + parameters.cl_roll_aileron * aileron
        )
        cn = (
            parameters.cn_zero
            + parameters.cn_beta * beta
            + parameters.cn_p * p_hat
            + parameters.cn_r * r_hat
            + parameters.cn_aileron * aileron
        )
        cx = -cd * math.cos(alpha) + cl * math.sin(alpha)
        cz = -cd * math.sin(alpha) - cl * math.cos(alpha)
        dynamic_pressure = 0.5 * AIR_DENSITY_KGPM3 * airspeed**2
        aerodynamic_force = (
            dynamic_pressure * parameters.area_m2 * np.array([cx, cy, cz], dtype=float)
        )
        aerodynamic_moment = (
            dynamic_pressure
            * parameters.area_m2
            * np.array(
                [parameters.span_m * cl_roll, parameters.mean_chord_m * cm, parameters.span_m * cn],
                dtype=float,
            )
        )
        gravity_body = rotation_nb @ np.array([0.0, 0.0, GRAVITY_MPS2], dtype=float)
        propeller_reaction_moment = -np.array(
            [
                propeller_torque_nm + parameters.propeller_inertia_kgm2 * propeller_speed_dot,
                parameters.propeller_inertia_kgm2 * propeller_speed * r_rate,
                -parameters.propeller_inertia_kgm2 * propeller_speed * q_rate,
            ],
            dtype=float,
        )
        total_force = aerodynamic_force + np.array([thrust_n, 0.0, 0.0], dtype=float)
        total_force += parameters.mass_kg * gravity_body
        body_velocity_dot = total_force / parameters.mass_kg - np.cross(body_rates, body_velocity)
        inertia = parameters.inertia_matrix
        body_rates_dot = np.linalg.solve(
            inertia,
            aerodynamic_moment
            + propeller_reaction_moment
            - np.cross(body_rates, inertia @ body_rates),
        )

        derivative = np.zeros(STATE_SIZE, dtype=float)
        derivative[0:3] = rotation_bn @ body_velocity
        derivative[3:6] = body_velocity_dot
        derivative[6:10] = _quaternion_derivative(quaternion_bn, body_rates)
        derivative[10:13] = body_rates_dot
        derivative[13] = propeller_speed_dot
        derivative[14] = left_rate
        derivative[15] = right_rate
        derivative[16] = (
            SERVO_NATURAL_FREQUENCY_RADPS**2 * (command.left_rad - left_elevon)
            - 2.0 * SERVO_DAMPING_RATIO * SERVO_NATURAL_FREQUENCY_RADPS * left_rate
        )
        derivative[17] = (
            SERVO_NATURAL_FREQUENCY_RADPS**2 * (command.right_rad - right_elevon)
            - 2.0 * SERVO_DAMPING_RATIO * SERVO_NATURAL_FREQUENCY_RADPS * right_rate
        )
        derivative[18] = (command.throttle - throttle_state) / THROTTLE_TIME_CONSTANT_S
        if not np.all(np.isfinite(derivative)):
            raise FloatingPointError("X8 derivative is not finite")
        return derivative


def integrate_rk4(
    dynamics: X8Dynamics,
    initial_state: np.ndarray,
    command: ActuatorCommand,
    *,
    duration_s: float,
    dt_s: float,
    wind_ned_mps: np.ndarray | None = None,
    voltage_v: float = NOMINAL_VOLTAGE_V,
) -> np.ndarray:
    """Integrate a held actuator command with fixed-step classical RK4."""
    if not math.isfinite(duration_s) or not math.isfinite(dt_s) or duration_s <= 0.0 or dt_s <= 0.0:
        raise ValueError("duration_s and dt_s must be finite and positive")
    step_count = round(duration_s / dt_s)
    if not math.isclose(step_count * dt_s, duration_s, abs_tol=1e-12):
        raise ValueError("duration_s must be an integer multiple of dt_s")
    state = np.asarray(initial_state, dtype=float).copy()
    if state.shape != (STATE_SIZE,):
        raise ValueError(f"initial state must have {STATE_SIZE} values")
    for _ in range(step_count):
        k1 = dynamics.derivative(state, command, wind_ned_mps=wind_ned_mps, voltage_v=voltage_v)
        k2 = dynamics.derivative(
            state + dt_s * k1 / 2.0,
            command,
            wind_ned_mps=wind_ned_mps,
            voltage_v=voltage_v,
        )
        k3 = dynamics.derivative(
            state + dt_s * k2 / 2.0,
            command,
            wind_ned_mps=wind_ned_mps,
            voltage_v=voltage_v,
        )
        k4 = dynamics.derivative(
            state + dt_s * k3,
            command,
            wind_ned_mps=wind_ned_mps,
            voltage_v=voltage_v,
        )
        state += dt_s * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        state[6:10] = normalize_quaternion(state[6:10])
        state[13] = max(0.0, state[13])
        state[18] = float(np.clip(state[18], 0.0, 1.0))
        if not np.all(np.isfinite(state)):
            raise FloatingPointError("RK4 integration produced a non-finite X8 state")
    return state


def _state_from_trim_variables(
    values: np.ndarray, airspeed_mps: float
) -> tuple[np.ndarray, ActuatorCommand]:
    alpha, beta, phi, theta, elevator, aileron, throttle, propeller_speed = values
    body_air_velocity = np.array(
        [
            airspeed_mps * math.cos(alpha) * math.cos(beta),
            airspeed_mps * math.sin(beta),
            airspeed_mps * math.sin(alpha) * math.cos(beta),
        ],
        dtype=float,
    )
    left_elevon, right_elevon = mix_elevons(elevator, aileron)
    state = np.zeros(STATE_SIZE, dtype=float)
    state[3:6] = body_air_velocity
    state[6:10] = quaternion_from_euler(phi, theta, 0.0)
    state[13] = propeller_speed
    state[14:16] = (left_elevon, right_elevon)
    state[18] = throttle
    return state, ActuatorCommand(left_elevon, right_elevon, throttle)


def solve_calm_trim(dynamics: X8Dynamics, *, airspeed_mps: float) -> TrimResult:
    """Solve the published model's full force/moment calm-air equilibrium at one airspeed."""
    if not math.isfinite(airspeed_mps) or airspeed_mps <= 0.0:
        raise ValueError("airspeed_mps must be finite and positive")

    def raw_residual(values: np.ndarray) -> np.ndarray:
        state, command = _state_from_trim_variables(values, airspeed_mps)
        derivative = dynamics.derivative(state, command)
        return np.concatenate((derivative[3:6], derivative[10:13], [derivative[13], derivative[2]]))

    scales = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 100.0, 10.0], dtype=float)

    def scaled_residual(values: np.ndarray) -> np.ndarray:
        return raw_residual(values) / scales

    initial = np.array([0.14, 0.0, 0.0, 0.14, -0.04, -0.04, 0.45, 500.0], dtype=float)
    lower = np.array(
        [
            math.radians(-5.0),
            math.radians(-10.0),
            math.radians(-45.0),
            math.radians(-30.0),
            math.radians(-25.0),
            math.radians(-25.0),
            0.0,
            1.0,
        ],
        dtype=float,
    )
    upper = np.array(
        [
            math.radians(15.0),
            math.radians(10.0),
            math.radians(45.0),
            math.radians(30.0),
            math.radians(25.0),
            math.radians(25.0),
            1.0,
            2000.0,
        ],
        dtype=float,
    )
    solution = least_squares(
        scaled_residual,
        initial,
        bounds=(lower, upper),
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
        max_nfev=5000,
    )
    if not solution.success:
        raise TrimError(f"calm trim optimizer failed: {solution.message}")
    state, command = _state_from_trim_variables(solution.x, airspeed_mps)
    residual = raw_residual(solution.x)
    trim = TrimResult(
        state=state,
        command=command,
        alpha_rad=float(solution.x[0]),
        beta_rad=float(solution.x[1]),
        phi_rad=float(solution.x[2]),
        theta_rad=float(solution.x[3]),
        residual=residual,
    )
    if (
        trim.acceleration_residual_mps2 >= 1e-5
        or trim.angular_acceleration_residual_radps2 >= 1e-6
        or trim.rotor_acceleration_residual_radps2 >= 0.1
        or trim.vertical_velocity_residual_mps >= 1e-5
    ):
        raise TrimError(
            "calm trim residual gate failed: "
            f"acc={trim.acceleration_residual_mps2:.3e}, "
            f"angular={trim.angular_acceleration_residual_radps2:.3e}, "
            f"rotor={trim.rotor_acceleration_residual_radps2:.3e}, "
            f"vertical={trim.vertical_velocity_residual_mps:.3e}"
        )
    return trim
