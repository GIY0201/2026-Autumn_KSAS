"""Explicit model selection and source integrity checks happen before simulation."""

import hashlib
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from data_generation.v1.profiles import load_generation_profile


def test_published_profile_loads_exact_identity_and_verified_local_sources() -> None:
    profile = load_generation_profile("crazyflie_eschmann_2024")
    assert profile["model_id"] == "crazyflie_eschmann_2024"
    assert profile["object_id"] == "quadrotor"
    assert profile["physics"]["mass_kg"] == 0.027
    assert len(profile["source_files"]) == 3


@pytest.mark.parametrize(
    ("profile_id", "object_id"),
    [
        ("tal_tailsitter_motion_v1", "vtol"),
        ("uh60_motion_v1", "helicopter"),
    ],
)
def test_reduced_motion_profile_loads_hash_verified_evidence_and_design_separately(
    profile_id: str, object_id: str
) -> None:
    profile = load_generation_profile(profile_id)

    assert profile["object_id"] == object_id
    assert profile["engine"] == "bounded_motion"
    assert "motion" in profile and "physics" not in profile
    assert profile["metadata"]["operating_parameter_role"] == "simulation_design"
    assert profile["source_evidence"]
    assert profile["experiment"]["ordering_alternatives"]


@pytest.mark.parametrize("profile_id", ["../../outside", "missing", "", None, "a/b"])
def test_unknown_or_path_like_profile_is_rejected(profile_id: object) -> None:
    with pytest.raises(ValueError, match="profile"):
        load_generation_profile(profile_id)


def _profile_fixture(tmp_path: Path, monkeypatch):
    import data_generation.v1.profiles as profiles

    source = tmp_path / "data_sources" / "test_source" / "input.txt"
    source.parent.mkdir(parents=True)
    source.write_text("original source", encoding="utf-8")
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(profiles, "PROFILE_ROOT", profile_root)
    config = {
        "object_id": "quadrotor",
        "model_id": "example",
        "identity_label": "Example",
        "source_id": "test_source",
        "input_id": "example:1",
        "evidence_status": "TEST_FIXTURE",
        "limitations": ["test-only fixture"],
        "physics": {"mass_kg": 1.0},
        "experiment": {"role": "test"},
        "source_files": [
            {
                "path": source.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "url": "https://example.org/source",
            }
        ],
    }
    path = profile_root / "example.yaml"
    OmegaConf.save(OmegaConf.create(config), path)
    return source, path, config


def test_changed_source_is_rejected_before_dynamics_can_run(tmp_path: Path, monkeypatch) -> None:
    source, _, _ = _profile_fixture(tmp_path, monkeypatch)
    source.write_text("changed source", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_generation_profile("example")


def test_bounded_motion_engine_rejects_an_incompatible_object_before_simulation(
    tmp_path: Path, monkeypatch
) -> None:
    _, path, config = _profile_fixture(tmp_path, monkeypatch)
    del config["physics"]
    config["engine"] = "bounded_motion"
    config["motion"] = {"dt_s": 0.02}
    OmegaConf.save(OmegaConf.create(config), path)

    with pytest.raises(ValueError, match="bounded_motion"):
        load_generation_profile("example")


@pytest.mark.parametrize("change", ["source_escape", "model_mismatch", "missing_physics"])
def test_malformed_profile_is_not_a_silent_fallback(
    tmp_path: Path,
    monkeypatch,
    change: str,
) -> None:
    _, path, config = _profile_fixture(tmp_path, monkeypatch)
    if change == "source_escape":
        config["source_files"][0]["path"] = "../outside.txt"
    elif change == "model_mismatch":
        config["model_id"] = "different_model"
    else:
        del config["physics"]
    OmegaConf.save(OmegaConf.create(config), path)
    with pytest.raises(ValueError):
        load_generation_profile("example")
