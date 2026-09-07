"""Independent kinematic tests for the reduced-motion VTOL/helicopter engine."""

from __future__ import annotations

import math
from copy import deepcopy

import numpy as np
import pytest

from contracts.v1.validation import OUTPUT_DT_S, SAMPLE_COUNT
from data_generation.v1.bounded_motion import run_bounded_motion_episode
from data_generation.v1.profiles import load_generation_profile


def _profile(
    *,
    object_id: str = "helicopter",
    initial_speed_mps: float = 4.0,
    initial_vertical_speed_mps: float = 1.0,
    initial_turn_rate_deg_s: float = 0.0,
    target_speed_mps: tuple[float, float] = (4.0, 4.0),
    target_vertical_speed_mps: tuple[float, float] = (1.0, 1.0),
    target_turn_rate_deg_s: tuple[float, float] = (0.0, 0.0),
) -> dict[str, object]:
    """Return a literal simulation fixture, not a source-backed profile."""
    return {
        "engine": "bounded_motion",
        "object_id": object_id,
        "motion": {
            "dt_s": 0.02,
            "horizontal_speed_max_mps": 10.0,
            "vertical_speed_max_mps": 4.0,
            "horizontal_accel_max_mps2": 3.0,
            "vertical_accel_max_mps2": 2.0,
            "turn_rate_max_deg_s": 45.0,
            "speed_response_s": 0.1,
            "vertical_response_s": 0.1,
            "turn_response_s": 0.1,
        },
        "experiment": {
            "role": "simulation_design_not_airframe_coefficients",
            "initial_position_enu_m": [0.0, 0.0, 0.0],
            "initial_speed_mps": initial_speed_mps,
            "initial_heading_deg": 0.0,
            "initial_vertical_speed_mps": initial_vertical_speed_mps,
            "initial_turn_rate_deg_s": initial_turn_rate_deg_s,
            "hover_speed_tolerance_mps": 0.25,
            "target_tolerance_speed_mps": 0.02,
            "target_tolerance_vertical_speed_mps": 0.02,
            "target_tolerance_turn_rate_deg_s": 0.2,
            "modes": {
                "test_motion": {
                    "mode_class": "test_motion",
                    "duration_s": [60.0, 60.0],
                    "target_speed_mps": list(target_speed_mps),
                    "target_vertical_speed_mps": list(target_vertical_speed_mps),
                    "target_turn_rate_deg_s": list(target_turn_rate_deg_s),
                }
            },
            "ordering_alternatives": [["test_motion"]],
        },
    }


def test_straight_motion_and_vertical_integration_are_exact_for_constant_fixture() -> None:
    episode = run_bounded_motion_episode(seed=7, profile=_profile())
    expected_time = np.arange(SAMPLE_COUNT, dtype=float) * OUTPUT_DT_S

    assert episode.status == "complete"
    assert np.array_equal(episode.steps, np.arange(SAMPLE_COUNT))
    assert np.array_equal(episode.t_s, expected_time)
    assert np.allclose(episode.position_enu_m[:, 0], 4.0 * expected_time, atol=1e-12)
    assert np.allclose(episode.position_enu_m[:, 1], 0.0, atol=1e-12)
    assert np.allclose(episode.position_enu_m[:, 2], expected_time, atol=1e-12)
    assert np.allclose(episode.velocity_enu_mps, [4.0, 0.0, 1.0], atol=1e-12)


def test_constant_coordinated_turn_has_known_radius_and_direction() -> None:
    turn_rate_deg_s = 30.0
    speed_mps = 4.0
    episode = run_bounded_motion_episode(
        seed=7,
        profile=_profile(
            initial_speed_mps=speed_mps,
            initial_vertical_speed_mps=0.0,
            initial_turn_rate_deg_s=turn_rate_deg_s,
            target_speed_mps=(speed_mps, speed_mps),
            target_vertical_speed_mps=(0.0, 0.0),
            target_turn_rate_deg_s=(turn_rate_deg_s, turn_rate_deg_s),
        ),
    )
    omega_rad_s = math.radians(turn_rate_deg_s)
    radius_m = speed_mps / omega_rad_s
    time_s = episode.t_s

    assert np.allclose(
        episode.position_enu_m[:, 0], radius_m * np.sin(omega_rad_s * time_s), atol=1e-10
    )
    assert np.allclose(
        episode.position_enu_m[:, 1], radius_m * (1.0 - np.cos(omega_rad_s * time_s)), atol=1e-10
    )
    assert np.allclose(episode.position_enu_m[:, 2], 0.0, atol=1e-12)
    assert np.all(episode.position_enu_m[1:, 1] >= -1e-12)
    assert np.allclose(
        episode.diagnostic_arrays["actual_turn_rate_deg_s"], turn_rate_deg_s, atol=1e-10
    )


def test_coupled_opposite_turn_and_braking_stay_within_acceleration_limits() -> None:
    profile = _profile(
        initial_speed_mps=8.0,
        initial_vertical_speed_mps=3.0,
        initial_turn_rate_deg_s=40.0,
        target_speed_mps=(0.0, 0.0),
        target_vertical_speed_mps=(-3.0, -3.0),
        target_turn_rate_deg_s=(-45.0, -45.0),
    )
    profile["motion"]["horizontal_accel_max_mps2"] = 2.5  # type: ignore[index]
    episode = run_bounded_motion_episode(seed=11, profile=profile)
    horizontal_acceleration = np.linalg.norm(
        np.diff(episode.velocity_enu_mps[:, :2], axis=0) / OUTPUT_DT_S, axis=1
    )
    vertical_acceleration = np.abs(
        np.diff(episode.velocity_enu_mps[:, 2]) / OUTPUT_DT_S
    )

    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
    assert np.max(horizontal_acceleration) <= 2.5 + 1e-10
    assert np.max(vertical_acceleration) <= 2.0 + 1e-10
    assert np.max(np.linalg.norm(episode.velocity_enu_mps[:, :2], axis=1)) <= 10.0 + 1e-12
    assert np.max(np.abs(episode.velocity_enu_mps[:, 2])) <= 4.0 + 1e-12
    assert np.ptp(episode.diagnostic_arrays["track_heading_deg"]) > 1.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda profile: profile["motion"].update(dt_s=0.03), "dt_s"),
        (lambda profile: profile["motion"].update(vertical_response_s=float("nan")), "numeric"),
        (lambda profile: profile["motion"].update(speed_response_s=0.0), "response"),
        (lambda profile: profile["motion"].update(horizontal_speed_max_mps=True), "numeric"),
        (
            lambda profile: profile["experiment"]["modes"]["test_motion"].update(
                target_speed_mps=[4.0, 3.0]
            ),
            "ordered",
        ),
        (
            lambda profile: profile["experiment"]["modes"]["test_motion"].update(
                target_speed_mps=[4.0, 11.0]
            ),
            "supported range",
        ),
        (lambda profile: profile.update(engine="other"), "engine"),
        (lambda profile: profile.update(object_id="quadrotor"), "object"),
    ],
)
def test_invalid_bounded_motion_profiles_fail_before_simulation(mutate, message) -> None:
    profile = deepcopy(_profile())
    mutate(profile)

    with pytest.raises(ValueError, match=message):
        run_bounded_motion_episode(seed=7, profile=profile)


@pytest.mark.parametrize("profile_id", ["tal_tailsitter_motion_v1", "uh60_motion_v1"])
def test_real_reduced_motion_profiles_are_seeded_continuous_and_source_separate(
    profile_id: str,
) -> None:
    """Source evidence labels do not turn simulation-design values into a device model."""
    profile = load_generation_profile(profile_id)
    first = run_bounded_motion_episode(seed=17, profile=profile)
    repeated = run_bounded_motion_episode(seed=17, profile=profile)
    different = run_bounded_motion_episode(seed=18, profile=profile)
    horizontal_acceleration = np.linalg.norm(
        np.diff(first.velocity_enu_mps[:, :2], axis=0) / OUTPUT_DT_S, axis=1
    )
    vertical_acceleration = np.abs(np.diff(first.velocity_enu_mps[:, 2]) / OUTPUT_DT_S)

    assert first.status == "complete"
    assert len(first.steps) == SAMPLE_COUNT
    assert np.array_equal(first.position_enu_m, repeated.position_enu_m)
    assert np.array_equal(first.velocity_enu_mps, repeated.velocity_enu_mps)
    assert not np.array_equal(first.position_enu_m, different.position_enu_m)
    assert np.max(first.velocity_enu_mps[:, 2]) > 0.0
    assert np.min(first.velocity_enu_mps[:, 2]) < 0.0
    assert np.max(np.linalg.norm(np.diff(first.velocity_enu_mps[:, :2], axis=0), axis=1)) > 0.0
    step_distances = np.linalg.norm(np.diff(first.position_enu_m, axis=0), axis=1)
    assert np.max(step_distances) <= (
        np.hypot(
            profile["motion"]["horizontal_speed_max_mps"],
            profile["motion"]["vertical_speed_max_mps"],
        )
        * OUTPUT_DT_S
        + 1e-10
    )
    assert np.max(horizontal_acceleration) <= profile["motion"]["horizontal_accel_max_mps2"] + 1e-9
    assert np.max(vertical_acceleration) <= profile["motion"]["vertical_accel_max_mps2"] + 1e-9
    assert "source_evidence" in profile
    assert "physics" not in profile
    assert not {"left_elevon_rad", "right_elevon_rad", "throttle"} & set(first.diagnostic_arrays)


def test_vtol_cruise_index_never_claims_cruise_below_its_design_threshold() -> None:
    profile = load_generation_profile("tal_tailsitter_motion_v1")
    episode = run_bounded_motion_episode(seed=17, profile=profile)
    experiment = profile["experiment"]
    cruise_index = experiment["mode_index_map"]["cruise"]
    cruise_samples = episode.diagnostic_arrays["mode_index"] == cruise_index

    assert np.any(cruise_samples)
    assert np.all(
        episode.diagnostic_arrays["actual_speed_mps"][cruise_samples]
        >= experiment["cruise_min_speed_mps"] - 1e-12
    )


def test_command_completion_never_uses_dynamics_after_its_continuous_end_time() -> None:
    """A command ending between base dt ticks must not borrow the following tick."""
    profile = _profile(
        initial_speed_mps=0.0,
        initial_vertical_speed_mps=0.0,
        target_speed_mps=(0.06, 0.06),
        target_vertical_speed_mps=(0.0, 0.0),
        target_turn_rate_deg_s=(0.0, 0.0),
    )
    profile["motion"]["speed_response_s"] = 0.001  # type: ignore[index]
    profile["motion"]["horizontal_accel_max_mps2"] = 3.0  # type: ignore[index]
    profile["experiment"]["target_tolerance_speed_mps"] = 0.001  # type: ignore[index]
    profile["experiment"]["modes"] = {  # type: ignore[index]
        "accelerate": {
            "mode_class": "accelerating",
            "duration_s": [0.01, 0.01],
            "target_speed_mps": [0.06, 0.06],
            "target_vertical_speed_mps": [0.0, 0.0],
            "target_turn_rate_deg_s": [0.0, 0.0],
        },
        "brake": {
            "mode_class": "braking",
            "duration_s": [59.99, 59.99],
            "target_speed_mps": [0.0, 0.0],
            "target_vertical_speed_mps": [0.0, 0.0],
            "target_turn_rate_deg_s": [0.0, 0.0],
        },
    }
    profile["experiment"]["ordering_alternatives"] = [["accelerate", "brake"]]  # type: ignore[index]

    episode = run_bounded_motion_episode(seed=7, profile=profile)
    accelerate = episode.event_records[0]

    assert accelerate.end_s == pytest.approx(0.01)
    assert accelerate.completed is False
    assert accelerate.completion_reason == "scheduled_command_window_elapsed"
    assert episode.velocity_enu_mps[1, 0] == pytest.approx(0.0, abs=1e-12)
