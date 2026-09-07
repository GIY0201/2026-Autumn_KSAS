"""Continuous X8 target tracking used by the synthetic scenario runner."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .x8 import (
    ActuatorCommand,
    X8Dynamics,
    mix_elevons,
    quaternion_to_euler,
    quaternion_to_rotation,
)

MAX_ELEVON_RAD = math.radians(25.0)
MAX_BANK_COMMAND_RAD = math.radians(15.0)
MAX_PITCH_COMMAND_RAD = math.radians(20.0)
MAX_ROLL_RATE_RADPS = 0.5
MAX_PITCH_RATE_RADPS = 0.35

assert MAX_ELEVON_RAD > MAX_BANK_COMMAND_RAD > 0.0


@dataclass(frozen=True, slots=True)
class FlightTarget:
    """Persistent outer-loop targets; values not changed by an event remain active."""

    airspeed_mps: float
    bank_rad: float
    climb_mps: float
    course_hold: bool


@dataclass(frozen=True, slots=True)
class FlightTargetUpdate:
    """Partial event update, where ``None`` means retain the preceding target."""

    airspeed_mps: float | None = None
    bank_rad: float | None = None
    climb_mps: float | None = None
    course_hold: bool | None = None


@dataclass(frozen=True, slots=True)
class ControlDiagnostics:
    """Values emitted at the control clock for inspection and dataset diagnostics."""

    airspeed_mps: float
    vertical_speed_up_mps: float
    altitude_up_m: float
    roll_rad: float
    pitch_rad: float
    course_rad: float
    target_bank_rad: float
    target_pitch_rad: float
    raw_command: ActuatorCommand


def apply_target_update(target: FlightTarget, update: FlightTargetUpdate) -> FlightTarget:
    """Apply one partial event update while preserving every unspecified target."""
    return FlightTarget(
        airspeed_mps=target.airspeed_mps if update.airspeed_mps is None else update.airspeed_mps,
        bank_rad=target.bank_rad if update.bank_rad is None else update.bank_rad,
        climb_mps=target.climb_mps if update.climb_mps is None else update.climb_mps,
        course_hold=target.course_hold if update.course_hold is None else update.course_hold,
    )


def wrap_angle_rad(angle_rad: float) -> float:
    """Wrap an angular difference to [-pi, pi)."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


class X8Autopilot:
    """Small target tracker with rate linearization and TECS-derived energy control."""

    def __init__(
        self, dynamics: X8Dynamics, trim_state: np.ndarray, trim_command: ActuatorCommand
    ) -> None:
        self._dynamics = dynamics
        self._trim_state = trim_state.copy()
        self._trim_command = trim_command
        self._trim_euler = quaternion_to_euler(trim_state[6:10])
        self._target = FlightTarget(18.0, self._trim_euler[0], 0.0, True)
        self._course_target_rad: float | None = None
        self._target_altitude_up_m: float | None = None
        self._last_airspeed_mps: float | None = None
        self._integral_roll = 0.0
        self._integral_pitch = 0.0
        self._integral_total_energy = 0.0
        self._integral_balance_energy = 0.0

    @property
    def target(self) -> FlightTarget:
        """Return the currently persistent target values."""
        return self._target

    def set_target(self, target: FlightTarget, state: np.ndarray, *, reset_course: bool) -> None:
        """Update the persistent target and capture course only for a new course-hold event."""
        self._target = target
        if reset_course and target.course_hold:
            self._course_target_rad = float(quaternion_to_euler(state[6:10])[2])

    def _linearized_rate_model(
        self, state: np.ndarray, command: ActuatorCommand, axis: str
    ) -> tuple[float, float]:
        """Central-difference local model ``rate_dot = a*rate + b*surface``."""
        epsilon = 1e-4
        plus_state = state.copy()
        minus_state = state.copy()
        if axis == "roll":
            plus_state[10] += epsilon
            minus_state[10] -= epsilon
            rate_index = 10
            plus_surface = state.copy()
            minus_surface = state.copy()
            plus_surface[14] += epsilon
            plus_surface[15] -= epsilon
            minus_surface[14] -= epsilon
            minus_surface[15] += epsilon
        elif axis == "pitch":
            plus_state[11] += epsilon
            minus_state[11] -= epsilon
            rate_index = 11
            plus_surface = state.copy()
            minus_surface = state.copy()
            plus_surface[14] += epsilon
            plus_surface[15] += epsilon
            minus_surface[14] -= epsilon
            minus_surface[15] -= epsilon
        else:
            raise ValueError(f"unsupported control axis: {axis}")
        a = (
            self._dynamics.derivative(plus_state, command)[rate_index]
            - self._dynamics.derivative(minus_state, command)[rate_index]
        ) / (2.0 * epsilon)
        b = (
            self._dynamics.derivative(plus_surface, command)[rate_index]
            - self._dynamics.derivative(minus_surface, command)[rate_index]
        ) / (2.0 * epsilon)
        if abs(b) < 1e-6:
            raise FloatingPointError(f"{axis} local surface authority is too small")
        return float(a), float(b)

    def step(
        self, state: np.ndarray, delayed_command: ActuatorCommand, dt_s: float
    ) -> ControlDiagnostics:
        """Create one physical actuator command at the 100 Hz controller clock."""
        if not math.isclose(dt_s, 0.01, abs_tol=1e-12):
            raise ValueError("X8-GEN-V1 control clock is fixed at 0.01 s")
        rotation_bn = quaternion_to_rotation(state[6:10])
        euler = quaternion_to_euler(state[6:10])
        ground_velocity_ned = rotation_bn @ state[3:6]
        altitude_up_m = -float(state[2])
        vertical_speed_up_mps = -float(ground_velocity_ned[2])
        airspeed_mps = float(np.linalg.norm(state[3:6]))
        if self._target_altitude_up_m is None:
            self._target_altitude_up_m = altitude_up_m
        self._target_altitude_up_m += self._target.climb_mps * dt_s
        if self._last_airspeed_mps is None:
            airspeed_dot = 0.0
        else:
            airspeed_dot = (airspeed_mps - self._last_airspeed_mps) / dt_s
        self._last_airspeed_mps = airspeed_mps

        if self._target.course_hold:
            if self._course_target_rad is None:
                self._course_target_rad = float(euler[2])
            course_error = wrap_angle_rad(self._course_target_rad - float(euler[2]))
            target_bank_rad = float(
                np.clip(0.6 * course_error, -MAX_BANK_COMMAND_RAD, MAX_BANK_COMMAND_RAD)
            )
        else:
            target_bank_rad = float(
                np.clip(self._target.bank_rad, -MAX_BANK_COMMAND_RAD, MAX_BANK_COMMAND_RAD)
            )

        height_error = self._target_altitude_up_m - altitude_up_m
        speed_error_energy = 0.5 * (self._target.airspeed_mps**2 - airspeed_mps**2)
        climb_error = self._target.climb_mps - vertical_speed_up_mps
        total_energy = 9.80665 * height_error + speed_error_energy
        balance_energy = 9.80665 * height_error - speed_error_energy
        total_energy_rate = 9.80665 * climb_error - airspeed_mps * airspeed_dot
        balance_energy_rate = 9.80665 * climb_error + airspeed_mps * airspeed_dot

        candidate_total_integral = self._integral_total_energy + total_energy * dt_s
        candidate_balance_integral = self._integral_balance_energy + balance_energy * dt_s
        candidate_throttle = (
            self._trim_command.throttle
            + 0.002 * total_energy
            + 0.0002 * candidate_total_integral
            + 0.01 * total_energy_rate
        )
        candidate_pitch_rad = (
            self._trim_euler[1]
            + 0.005 * balance_energy
            + 0.00025 * candidate_balance_integral
            + 0.01 * balance_energy_rate
        )
        throttle = float(np.clip(candidate_throttle, 0.0, 1.0))
        target_pitch_rad = float(
            np.clip(candidate_pitch_rad, -MAX_PITCH_COMMAND_RAD, MAX_PITCH_COMMAND_RAD)
        )
        if throttle == candidate_throttle:
            self._integral_total_energy = candidate_total_integral
        if target_pitch_rad == candidate_pitch_rad:
            self._integral_balance_energy = candidate_balance_integral

        roll_error = target_bank_rad - float(euler[0])
        pitch_error = target_pitch_rad - float(euler[1])
        desired_roll_rate = float(np.clip(roll_error, -MAX_ROLL_RATE_RADPS, MAX_ROLL_RATE_RADPS))
        desired_pitch_rate = float(
            np.clip(0.8 * pitch_error, -MAX_PITCH_RATE_RADPS, MAX_PITCH_RATE_RADPS)
        )
        roll_a, roll_b = self._linearized_rate_model(state, delayed_command, "roll")
        pitch_a, pitch_b = self._linearized_rate_model(state, delayed_command, "pitch")
        p_rate, q_rate = float(state[10]), float(state[11])
        desired_roll_acceleration = 5.0**2 * roll_error - 2.0 * 0.9 * 5.0 * p_rate
        desired_pitch_acceleration = 4.0**2 * pitch_error - 2.0 * 0.9 * 4.0 * q_rate
        roll_ff = (desired_roll_acceleration - roll_a * desired_roll_rate) / roll_b
        pitch_ff = (desired_pitch_acceleration - pitch_a * desired_pitch_rate) / pitch_b
        candidate_roll_integral = self._integral_roll + (desired_roll_rate - p_rate) * dt_s
        candidate_pitch_integral = self._integral_pitch + (desired_pitch_rate - q_rate) * dt_s
        aileron = (
            (self._trim_command.left_rad - self._trim_command.right_rad) / 2.0
            + roll_ff
            + 0.02 * (desired_roll_rate - p_rate)
            + 0.002 * candidate_roll_integral
        )
        elevator = (
            (self._trim_command.left_rad + self._trim_command.right_rad) / 2.0
            + pitch_ff
            + 0.02 * (desired_pitch_rate - q_rate)
            + 0.002 * candidate_pitch_integral
        )
        left_rad, right_rad = mix_elevons(elevator, aileron)
        raw_left, raw_right = left_rad, right_rad
        left_rad = float(np.clip(left_rad, -MAX_ELEVON_RAD, MAX_ELEVON_RAD))
        right_rad = float(np.clip(right_rad, -MAX_ELEVON_RAD, MAX_ELEVON_RAD))
        if left_rad == raw_left and right_rad == raw_right:
            self._integral_roll = candidate_roll_integral
            self._integral_pitch = candidate_pitch_integral
        raw_command = ActuatorCommand(left_rad=left_rad, right_rad=right_rad, throttle=throttle)
        return ControlDiagnostics(
            airspeed_mps=airspeed_mps,
            vertical_speed_up_mps=vertical_speed_up_mps,
            altitude_up_m=altitude_up_m,
            roll_rad=float(euler[0]),
            pitch_rad=float(euler[1]),
            course_rad=float(euler[2]),
            target_bank_rad=target_bank_rad,
            target_pitch_rad=target_pitch_rad,
            raw_command=raw_command,
        )
