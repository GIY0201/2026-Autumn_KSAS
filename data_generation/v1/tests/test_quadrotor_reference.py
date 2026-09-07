"""Behavioral tests for source-grounded Crazyflie reference planning."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import numpy as np
import pytest

import data_generation.v1.quadrotor_scenario as quadrotor_scenario_module
from contracts.v1.validation import OUTPUT_DT_S, SAMPLE_COUNT
from data_generation.v1.profiles import (
    freeze_motion_reference_profile,
    load_generation_profile,
)
from data_generation.v1.quadrotor_reference import (
    RestToRestSegment,
    SourceGroundedReferencePlanner,
    load_source_grounded_reference_settings,
    normalized_rest_to_rest_blend,
)
from data_generation.v1.quadrotor_scenario import run_quadrotor_episode

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_CONFIG_PATH = (
    PROJECT_ROOT
    / "data_generation"
    / "v1"
    / "configs"
    / "motion_reference"
    / "bitcraze_positioning.yaml"
)


@pytest.fixture(scope="module")
def profile() -> dict[str, object]:
    """Load the resolved profile whose default is source-grounded."""
    return load_generation_profile("crazyflie_eschmann_2024")


def test_rest_to_rest_blend_has_analytic_endpoints_and_hand_derived_midpoint() -> None:
    """The seventh-order blend is rest-to-rest through jerk, not a waypoint jump."""
    start = np.array([-1.0, 2.0, 0.5])
    end = np.array([3.0, -2.0, 2.5])
    duration_s = 4.0
    segment = RestToRestSegment(start, end, duration_s)

    assert normalized_rest_to_rest_blend(0.0) == pytest.approx((0.0, 0.0, 0.0, 0.0))
    assert normalized_rest_to_rest_blend(1.0) == pytest.approx((1.0, 0.0, 0.0, 0.0))
    midpoint_terms = normalized_rest_to_rest_blend(0.5)
    assert midpoint_terms[0] == pytest.approx(0.5)
    assert midpoint_terms[1] == pytest.approx(2.1875)
    assert midpoint_terms[2] == pytest.approx(0.0)
    assert normalized_rest_to_rest_blend(-5.0) == pytest.approx(
        normalized_rest_to_rest_blend(0.0)
    )
    assert normalized_rest_to_rest_blend(5.0) == pytest.approx(
        normalized_rest_to_rest_blend(1.0)
    )

    at_start = segment.evaluate(0.0)
    at_end = segment.evaluate(duration_s)
    at_midpoint = segment.evaluate(duration_s / 2.0)
    for state in (at_start, at_end):
        assert np.allclose(state.velocity_enu_mps, np.zeros(3))
        assert np.allclose(state.acceleration_enu_mps2, np.zeros(3))
        assert np.allclose(state.jerk_enu_mps3, np.zeros(3))
    assert np.allclose(at_start.position_enu_m, start)
    assert np.allclose(at_end.position_enu_m, end)
    assert np.allclose(at_midpoint.position_enu_m, (start + end) / 2.0)
    assert np.allclose(
        at_midpoint.velocity_enu_mps,
        (end - start) * 2.1875 / duration_s,
    )


def test_source_grounded_planner_is_seeded_varied_and_honors_duration_limits() -> None:
    """Fresh targets use the calibrated envelope instead of a rotated fixed polygon."""
    settings = load_source_grounded_reference_settings(REFERENCE_CONFIG_PATH)

    def plan_targets(seed: int) -> tuple[list[np.ndarray], list[object]]:
        planner = SourceGroundedReferencePlanner(settings, seed=seed)
        origin = np.zeros(3)
        targets: list[np.ndarray] = []
        legs: list[object] = []
        for _ in range(5):
            leg = planner.next_leg(origin)
            targets.append(leg.target_enu_m)
            legs.append(leg)
            origin = leg.target_enu_m
        return targets, legs

    first_targets, first_legs = plan_targets(41)
    repeated_targets, repeated_legs = plan_targets(41)
    other_targets, _ = plan_targets(73)

    assert all(
        np.array_equal(left, right)
        for left, right in zip(first_targets, repeated_targets, strict=True)
    )
    assert [leg.duration_s for leg in first_legs] == pytest.approx(
        [leg.duration_s for leg in repeated_legs]
    )
    assert not all(
        np.array_equal(left, right)
        for left, right in zip(first_targets, other_targets, strict=True)
    )
    assert len({tuple(np.round(target, 9)) for target in first_targets}) > 2

    for leg in first_legs:
        displacement = leg.target_enu_m - leg.start_enu_m
        distance_m = float(np.linalg.norm(displacement))
        assert distance_m >= settings.minimum_leg_distance_m
        assert leg.duration_s >= distance_m / leg.nominal_mean_speed_mps
        assert leg.duration_s >= (
            distance_m
            * settings.blend_peak_speed_factor
            / settings.maximum_peak_speed_mps
        )
        assert leg.duration_s >= np.sqrt(
            distance_m
            * settings.blend_peak_acceleration_factor
            / settings.maximum_peak_acceleration_mps2
        )
        times = np.linspace(0.0, leg.duration_s, num=101)
        samples = [leg.segment.evaluate(float(time_s)) for time_s in times]
        speeds = [float(np.linalg.norm(sample.velocity_enu_mps)) for sample in samples]
        accelerations = [
            float(np.linalg.norm(sample.acceleration_enu_mps2)) for sample in samples
        ]
        assert np.max(speeds) <= settings.maximum_peak_speed_mps + 1e-10
        assert np.max(accelerations) <= settings.maximum_peak_acceleration_mps2 + 1e-10


def test_source_grounded_episode_uses_physical_state_and_continuous_tracking(
    profile: dict[str, object],
) -> None:
    """Reference diagnostics are distinct from actual RK4 output and never reset position."""
    episode = run_quadrotor_episode(17, profile)
    experiment = profile["experiment"]
    assert isinstance(experiment, dict)

    assert episode.status == "complete"
    assert episode.position_enu_m.shape == (SAMPLE_COUNT, 3)
    assert episode.velocity_enu_mps.shape == (SAMPLE_COUNT, 3)
    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
    reference_position = np.column_stack(
        [episode.diagnostic_arrays[f"reference_position_{axis}_m"] for axis in "xyz"]
    )
    reference_velocity = np.column_stack(
        [episode.diagnostic_arrays[f"reference_velocity_{axis}_mps"] for axis in "xyz"]
    )
    reference_acceleration = np.column_stack(
        [episode.diagnostic_arrays[f"reference_acceleration_{axis}_mps2"] for axis in "xyz"]
    )
    tracking_error = episode.diagnostic_arrays["reference_tracking_error_m"]
    leg_index = episode.diagnostic_arrays["reference_leg_index"]

    assert np.array_equal(reference_position[0], np.zeros(3))
    assert np.all(np.isfinite(reference_position))
    assert np.all(np.isfinite(reference_velocity))
    assert np.all(np.isfinite(reference_acceleration))
    assert np.all(np.isfinite(tracking_error))
    assert not np.array_equal(episode.position_enu_m, reference_position)
    assert np.allclose(
        tracking_error,
        np.linalg.norm(reference_position - episode.position_enu_m, axis=1),
    )
    assert np.max(np.linalg.norm(np.diff(episode.position_enu_m, axis=0), axis=1)) <= (
        float(experiment["failure_speed_mps"]) * OUTPUT_DT_S + 1e-9
    )
    assert np.count_nonzero(np.diff(leg_index)) >= 1
    assert any(event.event_id.startswith("reference_leg_") for event in episode.event_records)
    assert any(event.completed for event in episode.event_records)


def test_frozen_motion_reference_uses_snapshot_without_rereading_external_config(
    profile: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation-start resolution is the only external motion-config read for an Episode."""
    frozen_profile = freeze_motion_reference_profile(profile)

    def unexpected_config_read(*args: object, **kwargs: object) -> object:
        pytest.fail("frozen source-grounded profile reread its external config")

    monkeypatch.setattr(
        quadrotor_scenario_module,
        "load_motion_reference_config",
        unexpected_config_read,
    )
    resolved = quadrotor_scenario_module._source_grounded_config(frozen_profile)

    assert resolved is not None
    motion_reference = frozen_profile["motion_reference"]
    assert isinstance(motion_reference, dict)
    snapshot = motion_reference["resolved_config"]
    assert isinstance(snapshot, dict)
    assert resolved.sha256 == snapshot["config_sha256"]
    assert resolved.values == snapshot["values"]


def test_frozen_motion_reference_rejects_profile_split_mismatch(
    profile: dict[str, object],
) -> None:
    """Profile split metadata cannot disagree with the resolved external config."""
    mismatched_profile = copy.deepcopy(profile)
    motion_reference = mismatched_profile["motion_reference"]
    assert isinstance(motion_reference, dict)
    motion_reference["calibration_flight_ids"] = ["mocap02"]

    with pytest.raises(ValueError, match="calibration_flight_ids"):
        freeze_motion_reference_profile(mismatched_profile)


def test_legacy_profile_without_motion_reference_keeps_waypoint_experiment(
    profile: dict[str, object],
) -> None:
    """Old dictionaries select the preserved waypoint branch explicitly by absence."""
    legacy_profile = copy.deepcopy(profile)
    legacy_profile.pop("motion_reference", None)

    episode = run_quadrotor_episode(17, legacy_profile)

    assert episode.status == "complete"
    assert episode.position_enu_m.shape == (SAMPLE_COUNT, 3)
    assert all(event.event_id.startswith("waypoint_") for event in episode.event_records)
    assert "reference_tracking_error_m" not in episode.diagnostic_arrays
    assert hashlib.sha256(episode.position_enu_m.tobytes()).hexdigest() == (
        "df63d923b6e1a46332f583726b8f021ef99dfd4f5256e9b3b148ce42c9257daf"
    )
