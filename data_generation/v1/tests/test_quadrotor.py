"""Mathematical and continuous-trajectory tests for the published Crazyflie model."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pytest

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S, SAMPLE_COUNT
from data_generation.v1.profiles import load_generation_profile
from data_generation.v1.quadrotor import QuadrotorDynamics
from data_generation.v1.quadrotor_scenario import run_quadrotor_episode


@pytest.fixture(scope="module")
def profile() -> dict[str, object]:
    """Load the already hash-verified published-model profile."""
    return load_generation_profile("crazyflie_eschmann_2024")


def _physics(profile: Mapping[str, object]) -> Mapping[str, object]:
    physics = profile["physics"]
    assert isinstance(physics, Mapping)
    return physics


def test_analytic_hover_matches_weight_zero_moment_and_published_izz_relation(
    profile: Mapping[str, object],
) -> None:
    """A physical hover is an equilibrium, not a shape-only fixture."""
    physics = _physics(profile)
    dynamics = QuadrotorDynamics(physics)
    command = dynamics.hover_command()
    state = dynamics.hover_state(yaw_rad=0.0)
    force_body, moment_body = dynamics.force_and_torque(command)
    derivative = dynamics.derivative(state, command)
    inertia = np.asarray(physics["inertia_kg_m2"], dtype=float)

    assert np.allclose(force_body, [0.0, 0.0, physics["mass_kg"] * physics["gravity_mps2"]])
    assert np.allclose(moment_body, np.zeros(3), atol=1e-14)
    assert np.allclose(derivative[3:6], np.zeros(3), atol=1e-12)
    assert np.allclose(derivative[10:13], np.zeros(3), atol=1e-12)
    assert np.allclose(command, np.full(4, 0.66), atol=0.01)
    assert inertia[2] == pytest.approx((inertia[0] + inertia[1]) * 1.832 / 2.0)


def test_rotor_wrench_signs_free_rotation_and_body_to_world_transform(
    profile: Mapping[str, object],
) -> None:
    """Known rotor geometry fixes roll/pitch/yaw signs independently of allocation code."""
    physics = _physics(profile)
    dynamics = QuadrotorDynamics(physics)
    baseline = np.zeros(4)
    selected = baseline.copy()
    selected[0] = 0.7
    _, baseline_moment = dynamics.force_and_torque(baseline)
    force_body, selected_moment = dynamics.force_and_torque(selected)
    coefficients = np.asarray(physics["thrust_coefficients_n"], dtype=float)
    delta_force = np.polyval(coefficients[::-1], 0.7) - np.polyval(coefficients[::-1], 0.0)
    rotor_position = np.asarray(physics["rotor_positions_body_m"], dtype=float)[0]
    torque_sign = np.asarray(physics["rotor_torque_signs"], dtype=float)[0]
    expected_delta_moment = np.array(
        [
            rotor_position[1] * delta_force,
            -rotor_position[0] * delta_force,
            torque_sign * physics["torque_per_thrust_m"] * delta_force,
        ]
    )

    assert force_body[2] > 0.0
    assert np.allclose(selected_moment - baseline_moment, expected_delta_moment)

    state = dynamics.hover_state(yaw_rad=0.0)
    state[10:13] = np.array([2.0, -3.0, 4.0])
    free_rotation = dynamics.derivative(state, dynamics.hover_command())[10:13]
    inertia = np.asarray(physics["inertia_kg_m2"], dtype=float)
    expected_free_rotation = -np.cross(state[10:13], inertia * state[10:13]) / inertia
    assert np.allclose(free_rotation, expected_free_rotation, atol=1e-12)

    rotated = dynamics.hover_state(yaw_rad=0.0)
    rotated[6:10] = np.array([math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0])
    high_command = dynamics.hover_command() + 0.05
    acceleration = dynamics.derivative(rotated, high_command)[3:6]
    assert acceleration[0] > 0.0
    assert abs(acceleration[1]) < 1e-12
    assert acceleration[2] == pytest.approx(-physics["gravity_mps2"], abs=1e-12)


def test_motor_lag_input_rejection_and_quaternion_normalization(
    profile: Mapping[str, object],
) -> None:
    """RK4 motor evolution follows the published first-order lag without invalid inputs."""
    dynamics = QuadrotorDynamics(_physics(profile))
    state = dynamics.hover_state(yaw_rad=0.3)
    state[13:17] = 0.5
    command = np.full(4, 0.8)
    dt_s = 0.01
    advanced = dynamics.step(state, command, dt_s)
    expected_motor = command + (state[13:17] - command) * math.exp(
        -dt_s / dynamics.motor_time_constant_s
    )

    assert np.allclose(advanced[13:17], expected_motor, atol=1e-8)
    assert np.linalg.norm(advanced[6:10]) == pytest.approx(1.0, abs=1e-14)
    with pytest.raises(ValueError):
        dynamics.step(state, command, -dt_s)
    with pytest.raises(ValueError):
        dynamics.step(state, command, float("nan"))
    with pytest.raises(ValueError):
        dynamics.step(np.full(17, np.nan), command, dt_s)
    with pytest.raises(ValueError):
        dynamics.step(state, np.full(4, np.nan), dt_s)
    with pytest.raises(ValueError):
        dynamics.step(state, np.array([0.2, 0.2, 0.2, 1.1]), dt_s)


def _integrate_excited_dynamics(
    dynamics: QuadrotorDynamics,
    *,
    dt_s: float,
    duration_s: float,
) -> np.ndarray:
    state = dynamics.hover_state(yaw_rad=0.2)
    hover = dynamics.hover_command()
    step_count = round(duration_s / dt_s)
    assert step_count * dt_s == pytest.approx(duration_s)
    for step in range(step_count):
        time_s = step * dt_s
        command = hover + np.array(
            [
                0.03 * math.sin(3.0 * time_s),
                -0.02 * math.cos(2.0 * time_s),
                0.025 * math.sin(5.0 * time_s),
                -0.015 * math.cos(4.0 * time_s),
            ]
        )
        state = dynamics.step(state, command, dt_s)
    return state


def test_rk4_step_size_convergence_uses_actual_excited_dynamics(
    profile: Mapping[str, object],
) -> None:
    """Halving the integration step improves a real motor/body response trajectory."""
    dynamics = QuadrotorDynamics(_physics(profile))
    duration_s = 0.5
    coarse = _integrate_excited_dynamics(dynamics, dt_s=0.01, duration_s=duration_s)
    fine = _integrate_excited_dynamics(dynamics, dt_s=0.005, duration_s=duration_s)
    reference = _integrate_excited_dynamics(dynamics, dt_s=0.0025, duration_s=duration_s)
    coarse_error = np.linalg.norm(coarse - reference)
    fine_error = np.linalg.norm(fine - reference)

    assert coarse_error > 0.0
    assert fine_error < coarse_error


def test_two_seeded_episodes_are_continuous_repeatable_and_physically_bounded(
    profile: Mapping[str, object],
) -> None:
    """Scenario control changes targets only and produces finite 60-second motion."""
    first = run_quadrotor_episode(17, profile)
    repeated = run_quadrotor_episode(17, profile)
    other = run_quadrotor_episode(29, profile)
    experiment = profile["experiment"]
    assert isinstance(experiment, Mapping)

    assert first.status == "complete"
    assert other.status == "complete"
    assert first.steps.shape == (SAMPLE_COUNT,)
    assert np.array_equal(first.steps, np.arange(SAMPLE_COUNT))
    assert np.allclose(first.t_s, np.arange(SAMPLE_COUNT) * OUTPUT_DT_S)
    assert first.t_s[-1] == pytest.approx(DURATION_S)
    assert np.all(np.isfinite(first.position_enu_m))
    assert np.all(np.isfinite(first.velocity_enu_mps))
    assert np.array_equal(first.position_enu_m[0], np.zeros(3))
    assert all(np.ptp(first.position_enu_m[:, axis]) > 0.1 for axis in range(3))
    assert np.max(np.linalg.norm(first.velocity_enu_mps, axis=1)) <= float(
        experiment["failure_speed_mps"]
    ) + 1e-9
    for name, values in first.diagnostic_arrays.items():
        assert np.all(np.isfinite(values)), name
    motor_values = np.column_stack(
        [first.diagnostic_arrays[f"motor_{index}_normalized"] for index in range(1, 5)]
    )
    command_values = np.column_stack(
        [first.diagnostic_arrays[f"command_{index}_normalized"] for index in range(1, 5)]
    )
    assert np.all(motor_values >= experiment["motor_command_min"])
    assert np.all(motor_values <= experiment["motor_command_max"])
    assert np.all(command_values >= experiment["motor_command_min"])
    assert np.all(command_values <= experiment["motor_command_max"])
    assert len(first.event_records) >= 3
    assert all(
        0.0 <= event.start_s <= event.end_s <= first.t_s[-1]
        for event in first.event_records
    )
    assert np.array_equal(first.position_enu_m, repeated.position_enu_m)
    assert np.array_equal(first.velocity_enu_mps, repeated.velocity_enu_mps)
    assert np.array_equal(
        first.diagnostic_arrays["command_1_normalized"],
        repeated.diagnostic_arrays["command_1_normalized"],
    )
    assert not np.array_equal(first.position_enu_m, other.position_enu_m)


def test_failure_envelope_returns_finite_partial_evidence_with_clipped_event_window(
    profile: Mapping[str, object],
) -> None:
    """A configured speed failure remains an honest partial record for the common writer."""
    experiment = profile["experiment"]
    assert isinstance(experiment, Mapping)
    failing_profile = dict(profile)
    failing_experiment = dict(experiment)
    failing_experiment["failure_speed_mps"] = 0.05
    failing_profile["experiment"] = failing_experiment

    episode = run_quadrotor_episode(17, failing_profile)

    assert episode.status == "failed"
    assert episode.failure_reason == "failure_speed_mps"
    assert 1 <= len(episode.steps) < SAMPLE_COUNT
    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
    assert all(
        0.0 <= event.start_s <= event.end_s <= episode.t_s[-1]
        for event in episode.event_records
    )


def test_nonfinite_dynamics_returns_finite_partial_evidence(
    profile: Mapping[str, object],
) -> None:
    """A finite but numerically unstable model is a failed record, not an uncaught run."""
    physics = _physics(profile)
    failing_profile = dict(profile)
    failing_physics = dict(physics)
    failing_physics["motor_time_constant_s"] = 1e-320
    failing_profile["physics"] = failing_physics

    episode = run_quadrotor_episode(17, failing_profile)

    assert episode.status == "failed"
    assert episode.failure_reason == "nonfinite_dynamics"
    assert len(episode.steps) == 1
    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
