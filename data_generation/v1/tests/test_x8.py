"""Numerical behavior tests for the X8 6-DOF dynamics implementation."""

from __future__ import annotations

import math

import numpy as np

from data_generation.v1.x8 import (
    ActuatorCommand,
    CommandDelay,
    X8Dynamics,
    integrate_rk4,
    mix_elevons,
    ned_to_enu,
    quaternion_to_euler,
    solve_calm_trim,
)


def test_ned_enu_round_trip_and_elevon_mixer_preserve_declared_signs() -> None:
    """Frame conversion and elevon mixing must retain the documented axis conventions."""
    ned = np.array([1.0, 2.0, 3.0])

    enu = ned_to_enu(ned)
    left, right = mix_elevons(0.10, 0.05)

    assert np.array_equal(enu, np.array([2.0, 1.0, -3.0]))
    assert np.array_equal(ned_to_enu(enu), ned)
    assert left == pytest_approx(0.15)
    assert right == pytest_approx(0.05)


def test_command_delay_holds_elevons_for_70ms_and_throttle_for_50ms() -> None:
    """A command must not bypass the identified actuator transport delays."""
    initial = ActuatorCommand(left_rad=0.0, right_rad=0.0, throttle=0.0)
    changed = ActuatorCommand(left_rad=0.1, right_rad=-0.1, throttle=0.8)
    delay = CommandDelay(dt_s=0.01, initial=initial)

    outputs = [delay.advance(changed) for _ in range(8)]

    assert [item.left_rad for item in outputs[:7]] == [0.0] * 7
    assert [item.throttle for item in outputs[:5]] == [0.0] * 5
    assert outputs[5].throttle == pytest_approx(0.8)
    assert outputs[7].left_rad == pytest_approx(0.1)
    assert outputs[7].right_rad == pytest_approx(-0.1)


def test_calm_trim_has_small_equilibrium_residual_and_finite_6dof_derivative() -> None:
    """A calm 18 m/s equilibrium must balance forces, moments, rotor, and vertical motion."""
    dynamics = X8Dynamics.default()
    trim = solve_calm_trim(dynamics, airspeed_mps=18.0)

    derivative = dynamics.derivative(trim.state, trim.command)

    assert trim.acceleration_residual_mps2 < 1e-5
    assert trim.angular_acceleration_residual_radps2 < 1e-6
    assert trim.rotor_acceleration_residual_radps2 < 0.1
    assert trim.vertical_velocity_residual_mps < 1e-5
    assert derivative.shape == (19,)
    assert np.all(np.isfinite(derivative))


def test_servo_lag_and_rk4_step_halving_are_numerically_stable() -> None:
    """The identified lag should react continuously and the 2.5 ms integrator should converge."""
    dynamics = X8Dynamics.default()
    trim = solve_calm_trim(dynamics, airspeed_mps=18.0)
    perturbed = ActuatorCommand(
        left_rad=trim.command.left_rad + math.radians(1.0),
        right_rad=trim.command.right_rad - math.radians(1.0),
        throttle=trim.command.throttle,
    )

    initial_derivative = dynamics.derivative(trim.state, perturbed)
    coarse = integrate_rk4(dynamics, trim.state, perturbed, duration_s=0.25, dt_s=0.0025)
    fine = integrate_rk4(dynamics, trim.state, perturbed, duration_s=0.25, dt_s=0.00125)
    coarse_euler = quaternion_to_euler(coarse[6:10])
    fine_euler = quaternion_to_euler(fine[6:10])

    assert initial_derivative[16] > 0.0
    assert np.linalg.norm(coarse[:3] - fine[:3]) < 0.05
    assert np.linalg.norm(coarse[3:6] - fine[3:6]) < 0.01
    assert np.max(np.abs(coarse_euler - fine_euler)) < math.radians(0.1)


def pytest_approx(value: float):
    """Keep scalar approximate assertions readable without importing a test helper mock."""
    import pytest

    return pytest.approx(value, abs=1e-12)
