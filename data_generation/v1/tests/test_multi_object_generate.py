"""Exercise real profile dispatch and the existing CSV boundary."""

import csv
import hashlib
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from contracts.v1.csv_io import read_public_dataset
from data_generation.v1 import dataset as dataset_module
from data_generation.v1 import generate as generate_module
from data_generation.v1.generate import generate_from_config
from data_generation.v1.profiles import freeze_motion_reference_profile, load_generation_profile
from data_generation.v1.tests.test_dataset import _episode


def test_unsupported_profile_is_rejected_without_running_x8(tmp_path: Path, monkeypatch) -> None:
    def unexpected_x8(*args, **kwargs):
        pytest.fail("unsupported profile silently fell back to X8")

    monkeypatch.setattr("data_generation.v1.generate.run_default_x8_episode", unexpected_x8)
    config = OmegaConf.create(
        {
            "profile_id": "not_implemented",
            "mode": "diagnostic",
            "seed": 17,
            "episode_count": 1,
            "source_motion_check_status": "NOT_RUN",
        }
    )
    with pytest.raises(ValueError, match="profile"):
        generate_from_config(config, output_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [("seed", True), ("seed", 1.5), ("episode_count", 1.5)])
def test_invalid_numeric_run_setting_is_rejected_before_simulation(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value: object,
) -> None:
    def unexpected_x8(*args, **kwargs):
        pytest.fail("invalid request reached simulation")

    monkeypatch.setattr("data_generation.v1.generate.run_default_x8_episode", unexpected_x8)
    config = OmegaConf.create(
        {
            "mode": "diagnostic",
            "seed": 17,
            "episode_count": 1,
            "identity_label": "X8 고정익 UAV",
            "source_motion_check_status": "NOT_RUN",
        }
    )
    config[field] = value
    with pytest.raises(ValueError, match=field):
        generate_from_config(config, output_root=tmp_path)


def test_no_profile_generation_rejects_noncanonical_x8_identity_before_simulation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The legacy X8 path must not serialize an arbitrary object identity."""

    def unexpected_x8(*args, **kwargs):
        pytest.fail("noncanonical X8 identity reached simulation")

    monkeypatch.setattr("data_generation.v1.generate.run_default_x8_episode", unexpected_x8)
    config = OmegaConf.create(
        {
            "mode": "diagnostic",
            "seed": 17,
            "episode_count": 1,
            "identity_label": "VTOL",
            "source_motion_check_status": "NOT_RUN",
        }
    )

    with pytest.raises(ValueError, match="identity_label"):
        generate_from_config(config, output_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_generation_records_the_unchanged_start_code_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A generated manifest records the hash captured before its simulation begins."""
    expected_hash = "a" * 64
    monkeypatch.setattr(dataset_module, "_code_hash", lambda: expected_hash)
    monkeypatch.setattr(
        "data_generation.v1.generate.run_default_x8_episode",
        lambda seed: _episode(),
    )
    original_write_dataset = generate_module.write_dataset

    def verify_start_snapshot(
        episodes,
        *,
        request,
        output_root,
        source_motion_check_report=None,
        training_config=None,
        episode_splits=None,
    ):
        assert request.code_hash_at_start == expected_hash
        return original_write_dataset(
            episodes,
            request=request,
            output_root=output_root,
            source_motion_check_report=source_motion_check_report,
            training_config=training_config,
            episode_splits=episode_splits,
        )

    monkeypatch.setattr(generate_module, "write_dataset", verify_start_snapshot)
    config = OmegaConf.create(
        {
            "mode": "diagnostic",
            "seed": 17,
            "episode_count": 1,
            "identity_label": "X8 고정익 UAV",
            "source_motion_check_status": "NOT_RUN",
        }
    )

    result = generate_from_config(config, output_root=tmp_path)

    with (result.path / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifest = next(csv.DictReader(handle))
    assert manifest["code_hash"] == expected_hash


def test_generation_rejects_a_code_change_before_allocating_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A hash change after simulation start cannot become a completed receipt."""
    hashes = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(dataset_module, "_code_hash", lambda: next(hashes))
    monkeypatch.setattr(
        "data_generation.v1.generate.run_default_x8_episode",
        lambda _seed: _episode(),
    )
    config = OmegaConf.create(
        {
            "mode": "diagnostic",
            "seed": 17,
            "episode_count": 1,
            "identity_label": "X8 고정익 UAV",
            "source_motion_check_status": "NOT_RUN",
        }
    )

    with pytest.raises(ValueError, match="code hash.*changed"):
        generate_from_config(config, output_root=tmp_path)

    assert not (tmp_path / "data_generation").exists()


def test_real_quadrotor_generation_preserves_contract_provenance_and_reproducibility(
    tmp_path: Path,
) -> None:
    config = OmegaConf.create(
        {
            "profile_id": "crazyflie_eschmann_2024",
            "mode": "diagnostic",
            "episode_count": 1,
            "seed": 42,
            "source_motion_check_status": "NOT_RUN",
        }
    )
    progress = []
    first = generate_from_config(
        config,
        output_root=tmp_path,
        progress=lambda done, total, phase: progress.append((done, total, phase)),
    )
    second = generate_from_config(config, output_root=tmp_path)
    assert first.status == second.status == "complete"
    assert first.dataset_id != second.dataset_id
    assert first.path.parent == tmp_path / "data_generation" / "v1"
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
    profile = load_generation_profile("crazyflie_eschmann_2024")
    frozen_profile = freeze_motion_reference_profile(profile)
    for key in ("object_id", "model_id", "source_id", "input_id"):
        assert manifest[key] == profile[key]
    with (first.path / "provenance.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == profile["source_files"]
    snapshot = OmegaConf.load(first.path / "effective_model_config.yaml")
    snapshot_profile = OmegaConf.to_container(snapshot.profile, resolve=True)
    assert snapshot_profile == frozen_profile
    assert isinstance(snapshot_profile, dict)
    motion_reference = snapshot_profile["motion_reference"]
    assert isinstance(motion_reference, dict)
    resolved_config = motion_reference["resolved_config"]
    assert isinstance(resolved_config, dict)
    assert len(resolved_config["config_sha256"]) == 64
    assert resolved_config["values"]["splits"]["calibration_flight_ids"] == motion_reference[
        "calibration_flight_ids"
    ]
    assert resolved_config["values"]["splits"]["comparison_flight_ids"] == motion_reference[
        "comparison_flight_ids"
    ]
    assert snapshot.run.seed == 42
    with (first.path / "files.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            assert (
                hashlib.sha256((first.path / row["relative_path"]).read_bytes()).hexdigest()
                == row["sha256"]
            )
    with (first.path / "evaluation/diagnostics.csv").open(encoding="utf-8", newline="") as handle:
        columns = set(csv.DictReader(handle).fieldnames)
    assert not {"left_elevon_rad", "right_elevon_rad", "throttle"} & columns
    assert not (first.path / "evaluation/source_motion_check.csv").exists()
    assert progress[0] == (0, 1, "simulating")
    assert (1, 1, "simulating") in progress
    assert progress[-1] == (1, 1, "writing")


@pytest.mark.parametrize("profile_id", ["tal_tailsitter_motion_v1", "uh60_motion_v1"])
def test_real_bounded_motion_generation_preserves_contract_provenance_and_reproducibility(
    tmp_path: Path,
    profile_id: str,
) -> None:
    config = OmegaConf.create(
        {
            "profile_id": profile_id,
            "mode": "diagnostic",
            "episode_count": 1,
            "seed": 42,
            "source_motion_check_status": "NOT_RUN",
        }
    )
    first = generate_from_config(config, output_root=tmp_path)
    second = generate_from_config(config, output_root=tmp_path)

    assert first.status == second.status == "complete"
    assert first.dataset_id != second.dataset_id
    for relative_path in ("public/observations.csv", "evaluation/truth.csv"):
        assert (first.path / relative_path).read_bytes() == (
            second.path / relative_path
        ).read_bytes()
    public = read_public_dataset(first.path / "public")
    assert len(public.episodes) == 1
    profile = load_generation_profile(profile_id)
    with (first.path / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifest = next(csv.DictReader(handle))
    for key in ("object_id", "model_id", "source_id", "input_id"):
        assert manifest[key] == profile[key]
    with (first.path / "provenance.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == profile["source_files"]
    snapshot = OmegaConf.load(first.path / "effective_model_config.yaml")
    assert OmegaConf.to_container(snapshot.profile, resolve=True) == profile
    with (first.path / "files.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            assert (
                hashlib.sha256((first.path / row["relative_path"]).read_bytes()).hexdigest()
                == row["sha256"]
            )
    with (first.path / "evaluation/diagnostics.csv").open(encoding="utf-8", newline="") as handle:
        columns = set(csv.DictReader(handle).fieldnames or ())
    assert {"target_speed_mps", "actual_speed_mps", "mode_index", "segment_index"} <= columns
    assert not {"left_elevon_rad", "right_elevon_rad", "throttle"} & columns
    assert not (first.path / "evaluation/source_motion_check.csv").exists()


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("identity_label", "VTOL", "identity_label"),
        ("source_motion_check_status", "REPORT_ONLY", "non-X8"),
    ],
)
def test_quadrotor_cannot_claim_another_identity_or_x8_source_check(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    config = OmegaConf.create(
        {
            "profile_id": "crazyflie_eschmann_2024",
            "mode": "diagnostic",
            "episode_count": 1,
            "seed": 42,
            "source_motion_check_status": "NOT_RUN",
        }
    )
    config[field] = value
    with pytest.raises(ValueError, match=error):
        generate_from_config(config, output_root=tmp_path)
    assert not list(tmp_path.iterdir())
