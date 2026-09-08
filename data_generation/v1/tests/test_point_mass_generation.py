"""TDD coverage for Stage 1 profile dispatch and point-mass datasets."""

import copy
import csv
import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
from omegaconf import OmegaConf

from contracts.v1.csv_io import read_public_dataset
from data_generation.v1 import generate as generate_module
from data_generation.v1.point_mass_profile import (
    point_mass_limits_from_profile,
    validate_point_mass_profile,
)
from data_generation.v1.point_mass_scenario import run_point_mass_episode
from data_generation.v1.profiles import load_generation_profile


@pytest.mark.parametrize(
    ("profile_id", "object_id", "model_id"),
    [
        ("fixed_wing_point_mass_v1", "x8_fixed_wing", "fixed_wing_point_mass_v1"),
        ("quadrotor_point_mass_v1", "quadrotor", "quadrotor_point_mass_v1"),
    ],
)
def test_point_mass_profile_requires_explicit_engine_and_parameter_roles(
    profile_id: str, object_id: str, model_id: str
) -> None:
    profile = load_generation_profile(profile_id)

    assert profile["engine"] == "point_mass"
    assert profile["object_id"] == object_id
    assert profile["model_id"] == model_id
    assert profile["metadata"]["motion_abstraction"] == "constrained_point_mass_kinematics"
    assert profile["source_evidence"]
    assert profile["parameter_provenance"]


@pytest.mark.parametrize("profile_id", ["fixed_wing_point_mass_v1", "quadrotor_point_mass_v1"])
def test_point_mass_runner_returns_one_finite_contract_shaped_episode(profile_id: str) -> None:
    profile = load_generation_profile(profile_id)

    episode = run_point_mass_episode(seed=17, profile=profile)

    assert episode.status == "complete"
    assert episode.failure_reason is None
    assert episode.steps.shape == (301,)
    assert episode.position_enu_m.shape == (301, 3)
    assert episode.velocity_enu_mps.shape == (301, 3)
    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
    assert episode.event_records[-1].completed is False
    assert episode.event_records[-1].completion_reason == "episode_window_cut_off"
    assert all(event.completed for event in episode.event_records[:-1])
    assert "target_horizontal_speed_mps" in episode.diagnostic_arrays
    assert "actual_track_turn_rate_rad_s" in episode.diagnostic_arrays


@pytest.mark.parametrize("profile_id", ["fixed_wing_point_mass_v1", "quadrotor_point_mass_v1"])
def test_point_mass_runner_keeps_recorded_states_inside_profile_limits(profile_id: str) -> None:
    profile = load_generation_profile(profile_id)
    limits = point_mass_limits_from_profile(profile)
    episode = run_point_mass_episode(seed=17, profile=profile)
    diagnostics = episode.diagnostic_arrays
    horizontal_speed = diagnostics["actual_horizontal_speed_mps"]
    vertical_speed = diagnostics["actual_vertical_speed_mps"]
    turn_rate = diagnostics["actual_track_turn_rate_rad_s"]

    recorded_horizontal_speed = np.hypot(
        episode.velocity_enu_mps[:, 0], episode.velocity_enu_mps[:, 1]
    )
    assert np.allclose(recorded_horizontal_speed, horizontal_speed)
    assert np.all(horizontal_speed >= limits.minimum_horizontal_speed_mps)
    assert np.all(horizontal_speed <= limits.maximum_horizontal_speed_mps)
    assert np.all(np.abs(vertical_speed) <= limits.maximum_vertical_speed_mps)
    assert np.all(np.abs(turn_rate) <= limits.maximum_track_turn_rate_rad_s)
    assert np.all(
        np.hypot(
            diagnostics["actual_horizontal_tangential_acceleration_mps2"],
            diagnostics["actual_horizontal_normal_acceleration_mps2"],
        )
        <= limits.horizontal_acceleration_max_mps2 + 1e-9
    )
    assert np.all(
        np.abs(diagnostics["actual_vertical_acceleration_mps2"])
        <= limits.vertical_acceleration_max_mps2 + 1e-9
    )


def test_point_mass_profile_rejects_mismatched_metadata_before_execution() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["metadata"]["motion_abstraction"] = "published_6dof"

    with pytest.raises(ValueError, match="metadata"):
        validate_point_mass_profile(profile)


def test_point_mass_profile_rejects_an_initial_range_outside_its_limits() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["initial_state"]["horizontal_speed_mps"] = [14.0, 23.0]

    with pytest.raises(ValueError, match="initial horizontal speed"):
        validate_point_mass_profile(profile)


def test_fixed_wing_profile_rejects_a_hover_capable_minimum_speed() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["motion"]["minimum_horizontal_speed_mps"] = 0.0
    profile["initial_state"]["horizontal_speed_mps"] = [0.0, 0.0]
    for command in profile["commands"]:
        command["target_horizontal_speed_mps"] = 0.0
        command["target_vertical_speed_mps"] = 0.0
        command["target_track_turn_rate_rad_s"] = 0.0

    with pytest.raises(ValueError, match="fixed-wing minimum horizontal speed"):
        validate_point_mass_profile(profile)


def test_fixed_wing_profile_rejects_an_initial_shared_turn_budget_violation() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["initial_state"]["horizontal_speed_mps"] = [22.0, 22.0]
    profile["initial_state"]["track_turn_rate_rad_s"] = [0.18, 0.18]

    with pytest.raises(ValueError, match="initial track turn-rate range"):
        validate_point_mass_profile(profile)


def test_point_mass_profile_rejects_an_integration_step_at_the_runtime_epsilon() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["motion"]["integration_dt_s"] = 1e-13

    with pytest.raises(ValueError, match="integration_dt_s"):
        validate_point_mass_profile(profile)


def test_fixed_wing_legacy_turn_labels_follow_enu_left_and_right_geometry() -> None:
    profile = load_generation_profile("fixed_wing_point_mass_v1")
    command_by_id = {command["command_id"]: command for command in profile["commands"]}

    assert command_by_id["legacy_left_turn"]["target_track_turn_rate_rad_s"] > 0.0
    assert command_by_id["legacy_climb"]["target_track_turn_rate_rad_s"] > 0.0
    assert command_by_id["legacy_right_turn"]["target_track_turn_rate_rad_s"] < 0.0
    assert command_by_id["legacy_descent"]["target_track_turn_rate_rad_s"] < 0.0

    episode = run_point_mass_episode(seed=17, profile=profile)
    heading = np.unwrap(episode.diagnostic_arrays["track_heading_rad"])
    command_index = episode.diagnostic_arrays["command_index"].astype(int)
    for index, expected_heading_change, expected_lateral_sign in (
        (1, 1.0, 1.0),
        (4, -1.0, -1.0),
    ):
        samples = np.flatnonzero(command_index == index)
        assert samples.size > 1
        assert expected_heading_change * (heading[samples[-1]] - heading[samples[0]]) > 0.0
        start = samples[0]
        lateral_unit = np.array(
            [-math.sin(heading[start]), math.cos(heading[start])], dtype=float
        )
        displacement = episode.position_enu_m[samples[-1], :2] - episode.position_enu_m[start, :2]
        assert expected_lateral_sign * float(np.dot(displacement, lateral_unit)) > 0.0


def test_point_mass_episode_rejects_an_intermediate_fixed_wing_path_angle() -> None:
    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["motion"]["maximum_track_path_angle_deg"] = 1.4
    profile["commands"] = [
        {
            "command_id": "window_tail",
            "duration_s": None,
            "target_horizontal_speed_mps": 22.0,
            "target_vertical_speed_mps": 0.5,
            "target_track_turn_rate_rad_s": 0.0,
        }
    ]
    profile["parameter_provenance"]["commands"] = {
        "window_tail": {
            "role": "new_simulation_design",
            "basis": "Fixture that exposes a state-path-angle violation during acceleration.",
        }
    }

    validate_point_mass_profile(profile)
    with pytest.raises(ValueError, match="next state track path angle"):
        run_point_mass_episode(seed=17, profile=profile)


def test_point_mass_profile_loader_rejects_a_changed_declared_source(
    tmp_path: Path, monkeypatch
) -> None:
    """The point-mass engine must not bypass the normal local hash boundary."""
    import data_generation.v1.profiles as profiles

    profile = copy.deepcopy(load_generation_profile("fixed_wing_point_mass_v1"))
    profile["model_id"] = "fixture_point_mass"
    source = tmp_path / "data_sources" / "x8_dataverse_2024" / "README.md"
    source.parent.mkdir(parents=True)
    source.write_text("fixture source", encoding="utf-8")
    profile["source_files"] = [
        {
            "path": source.relative_to(tmp_path).as_posix(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "url": "https://example.invalid/x8-source",
        }
    ]
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    OmegaConf.save(OmegaConf.create(profile), profile_root / "fixture_point_mass.yaml")
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(profiles, "PROFILE_ROOT", profile_root)

    assert load_generation_profile("fixture_point_mass")["model_id"] == "fixture_point_mass"
    source.write_text("changed fixture source", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_generation_profile("fixture_point_mass")


@pytest.mark.parametrize("profile_id", ["fixed_wing_point_mass_v1", "quadrotor_point_mass_v1"])
def test_point_mass_generation_is_deterministic_and_never_calls_legacy_runners(
    tmp_path: Path, monkeypatch, profile_id: str
) -> None:
    """New profiles must stay on the new kernel even when both legacy runners are blocked."""

    def unexpected_legacy_runner(*args, **kwargs):
        pytest.fail("point-mass profile reached a legacy 6-DOF runner")

    monkeypatch.setattr(generate_module, "run_default_x8_episode", unexpected_legacy_runner)
    monkeypatch.setattr(
        "data_generation.v1.quadrotor_scenario.run_quadrotor_episode", unexpected_legacy_runner
    )
    config = OmegaConf.create(
        {
            "profile_id": profile_id,
            "mode": "diagnostic",
            "episode_count": 1,
            "seed": 42,
            "source_motion_check_status": "NOT_RUN",
        }
    )

    first = generate_module.generate_from_config(config, output_root=tmp_path)
    second = generate_module.generate_from_config(config, output_root=tmp_path)

    assert first.status == second.status == "complete"
    assert first.dataset_id != second.dataset_id
    profile = load_generation_profile(profile_id)
    public = read_public_dataset(first.path / "public")
    assert len(public.episodes) == 1
    assert set(path.name for path in (first.path / "public").iterdir()) == {
        "episodes.csv",
        "observations.csv",
    }
    for relative_path in ("public/observations.csv", "evaluation/truth.csv"):
        assert (first.path / relative_path).read_bytes() == (
            second.path / relative_path
        ).read_bytes()
    with (first.path / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifest = next(csv.DictReader(handle))
    assert manifest["object_id"] == profile["object_id"]
    assert manifest["model_id"] == profile["model_id"]
    assert manifest["record_kind"] == "motion_episode"
    with (first.path / "evaluation" / "diagnostics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        columns = set(csv.DictReader(handle).fieldnames or ())
    assert {
        "target_horizontal_speed_mps",
        "actual_horizontal_speed_mps",
        "target_vertical_speed_mps",
        "actual_vertical_speed_mps",
    } <= columns
    assert not {"left_elevon_rad", "right_elevon_rad", "throttle"} & columns
