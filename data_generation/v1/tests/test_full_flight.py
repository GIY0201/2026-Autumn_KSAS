"""Behavioral checks for connected full-flight point-mass scenarios."""

import numpy as np
import pytest

from data_generation.v1.full_flight import catalog, load_settings, run_full_flight_episode


@pytest.mark.parametrize(
    "object_id,scenario_id,altitude,count",
    [
        ("fixed_wing", "F01-A", 300, 64),
        ("helicopter", "H01-B", 500, 96),
        ("vtol", "V01-A", 500, 64),
    ],
)
def test_flight_ground_to_ground(object_id, scenario_id, altitude, count):
    settings = load_settings()
    assert len(catalog(settings, object_id)) == count
    result = run_full_flight_episode(
        17, settings=settings, object_id=object_id, scenario_id=scenario_id
    )
    assert result.status == "complete"
    assert 60 < result.t_s[-1] <= 600
    np.testing.assert_allclose(result.position_enu_m[[0, -1], 2], 0, atol=1e-7)
    np.testing.assert_allclose(result.velocity_enu_mps[[0, -1]], 0, atol=1e-7)
    assert abs(result.position_enu_m[:, 2].max() - altitude) < 1e-5
    assert result.position_enu_m[:, 2].min() >= -1e-7
    np.testing.assert_allclose(np.diff(result.t_s), 0.2, atol=1e-10)
    if object_id == "vtol":
        events = {e.event_id: e for e in result.event_records}
        outbound = events["transition_out"]
        inbound = events["transition_in"]
        assert outbound.end_s - outbound.start_s == pytest.approx(20)
        assert inbound.end_s - inbound.start_s == pytest.approx(10)
        assert "residual_braking" in events
        for event in (outbound, inbound):
            mask = (result.t_s >= event.start_s) & (result.t_s <= event.end_s)
            np.testing.assert_allclose(result.position_enu_m[mask, 2], 80, atol=1e-6)
    if object_id == "fixed_wing":
        z = result.position_enu_m[:, 2]
        omega = result.diagnostic_arrays["track_turn_rate_rad_s"]
        assert np.max(np.abs(omega[z < 50])) < 1e-9


def test_catalog_choice_and_seed_are_explicit_and_reproducible():
    settings = load_settings()
    a = run_full_flight_episode(7, settings=settings, object_id="helicopter", scenario_id="H10-F")
    b = run_full_flight_episode(7, settings=settings, object_id="helicopter", scenario_id="H10-F")
    np.testing.assert_array_equal(a.position_enu_m, b.position_enu_m)
    assert "cruise_restart" in {event.event_id for event in a.event_records}
    with pytest.raises(ValueError, match="scenario"):
        run_full_flight_episode(7, settings=settings, object_id="vtol", scenario_id="F01-A")


def test_impossible_time_budget_rejected_without_silent_cap_increase():
    settings = load_settings()
    settings["max_duration_s"] = 30
    with pytest.raises(ValueError, match="duration|600|budget"):
        run_full_flight_episode(1, settings=settings, object_id="fixed_wing", scenario_id="F01-A")


@pytest.mark.parametrize("object_id", ["fixed_wing", "helicopter", "vtol"])
def test_full_flight_config_writes_public_dataset(tmp_path, object_id):
    from omegaconf import OmegaConf

    from contracts.v1.csv_io import read_public_dataset
    from data_generation.v1.generate import generate_from_config

    config = OmegaConf.create(
        dict(
            seed=17,
            profile_id=f"{object_id}_full_flight_v1",
            mode="diagnostic",
            episode_count=1,
            source_motion_check_status="NOT_RUN",
        )
    )
    result = generate_from_config(config, output_root=tmp_path)
    assert result.status == "complete"
    data = read_public_dataset(result.path / "public")
    assert 60 < data.episodes[0].duration_s <= 600
    assert len(data.observations) == 3 * data.episodes[0].sample_count


def test_rejects_unknown_maneuver_instead_of_silent_straight_flight():
    settings = load_settings()
    settings["objects"]["fixed_wing"]["climb"][0] = "unknown"
    with pytest.raises(ValueError, match="maneuver"):
        run_full_flight_episode(1, settings=settings, object_id="fixed_wing", scenario_id="F01-A")


def test_rejects_wing_speed_below_configured_airborne_minimum():
    settings = load_settings()
    settings["objects"]["fixed_wing"]["minimum_airborne_horizontal_speed_mps"] = 31
    with pytest.raises(ValueError, match="airborne"):
        run_full_flight_episode(1, settings=settings, object_id="fixed_wing", scenario_id="F01-A")


def test_public_identifier_does_not_encode_future_maneuvers():
    result = run_full_flight_episode(
        17, settings=load_settings(), object_id="vtol", scenario_id="V01-A"
    )
    assert "V01-A" not in result.episode_id
    assert "상승" not in result.episode_id
    assert "seed" not in result.episode_id
