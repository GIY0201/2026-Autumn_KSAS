"""TDD coverage for generic v1 motion records and provenance persistence."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import numpy as np
import pytest
from omegaconf import OmegaConf

from contracts.v1.validation import validate_public_dataset
from data_generation.v1 import dataset as dataset_module
from data_generation.v1.dataset import GenerationRequest, write_dataset
from data_generation.v1.records import MotionEpisode, MotionEvent, SourceFileRecord
from data_generation.v1.source import X8_DATASET_DOI, X8_SOURCE_ID
from data_generation.v1.tests.test_dataset import _episode


def _motion_event(
    start_s: float,
    end_s: float,
    *,
    completed: bool = True,
) -> MotionEvent:
    return MotionEvent(
        event_id="hover_hold",
        start_s=start_s,
        end_s=end_s,
        trigger_kind="duration",
        completed=completed,
        completion_reason="episode_end",
    )


def _generic_episode(**overrides: object) -> MotionEpisode:
    sample_count = 301
    steps = np.arange(sample_count, dtype=int)
    position = np.column_stack(
        (
            steps.astype(float) * 0.1,
            np.full(sample_count, -3.0),
            np.full(sample_count, 12.0),
        )
    )
    values: dict[str, object] = {
        "episode_id": "quad-00000041",
        "status": "complete",
        "failure_reason": None,
        "seed": 41,
        "steps": steps,
        "t_s": steps.astype(float) * 0.2,
        "position_enu_m": position,
        "velocity_enu_mps": np.column_stack(
            (np.full(sample_count, 0.5), np.zeros(sample_count), np.zeros(sample_count))
        ),
        "diagnostic_arrays": {
            "rotor_1_thrust_n": np.full(sample_count, 0.11),
            "rotor_2_thrust_n": np.full(sample_count, 0.12),
        },
        "event_records": (_motion_event(0.0, 60.0),),
    }
    values.update(overrides)
    return MotionEpisode(**values)


def _generic_request(**overrides: object) -> GenerationRequest:
    values: dict[str, object] = {
        "master_seed": 41,
        "mode": "diagnostic",
        "source_motion_check_status": "NOT_RUN",
        "identity_label": "Test quadrotor",
        "object_id": "test_quadrotor",
        "model_id": "test_quadrotor_model",
        "source_id": "test_quadrotor_reference",
        "input_id": "doi:10.0000/test-quadrotor",
        "source_files": (
            SourceFileRecord(
                path="data_sources/test_quadrotor_reference/reference.csv",
                sha256="a" * 64,
                url="https://example.invalid/test-quadrotor/reference.csv",
            ),
        ),
        "model_config": {"mass_kg": 0.027, "motor_delay_s": 0.03},
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


def test_generic_motion_episode_writes_own_diagnostics_and_traceable_provenance(
    tmp_path: Path,
) -> None:
    """Generic records keep their own diagnostics and source metadata out of public CSVs."""
    result = write_dataset(
        [_generic_episode()],
        request=_generic_request(),
        output_root=tmp_path / "outputs",
    )

    validate_public_dataset(result.path / "public")
    diagnostic_columns, diagnostic_rows = _read_csv(result.path / "evaluation" / "diagnostics.csv")
    event_columns, event_rows = _read_csv(result.path / "evaluation" / "commands.csv")
    provenance_columns, provenance_rows = _read_csv(result.path / "provenance.csv")
    manifest_columns, manifest_rows = _read_csv(result.path / "manifest.csv")
    inventory_columns, inventory_rows = _read_csv(result.path / "files.csv")

    assert result.status == "complete"
    assert diagnostic_columns == [
        "episode_id",
        "step",
        "t_s",
        "rotor_1_thrust_n",
        "rotor_2_thrust_n",
    ]
    assert "left_elevon_rad" not in diagnostic_columns
    assert diagnostic_rows[0]["rotor_1_thrust_n"] == "0.11"
    assert event_columns == [
        "episode_id",
        "event_id",
        "start_s",
        "end_s",
        "trigger_kind",
        "completed",
        "completion_reason",
    ]
    assert event_rows == [
        {
            "episode_id": "quad-00000041",
            "event_id": "hover_hold",
            "start_s": "0",
            "end_s": "60",
            "trigger_kind": "duration",
            "completed": "1",
            "completion_reason": "episode_end",
        }
    ]
    assert provenance_columns == ["path", "sha256", "url"]
    assert provenance_rows == [
        {
            "path": "data_sources/test_quadrotor_reference/reference.csv",
            "sha256": "a" * 64,
            "url": "https://example.invalid/test-quadrotor/reference.csv",
        }
    ]
    assert manifest_columns[-6:] == [
        "source_motion_check_status",
        "object_id",
        "model_id",
        "provenance_path",
        "model_config_path",
        "record_kind",
    ]
    assert len(manifest_rows) == 1
    manifest = manifest_rows[0]
    assert manifest["object_id"] == "test_quadrotor"
    assert manifest["model_id"] == "test_quadrotor_model"
    assert manifest["source_id"] == "test_quadrotor_reference"
    assert manifest["input_id"] == "doi:10.0000/test-quadrotor"
    assert manifest["provenance_path"] == "provenance.csv"
    assert manifest["model_config_path"] == "effective_model_config.yaml"
    assert manifest["record_kind"] == "motion_episode"
    assert inventory_columns == ["relative_path", "sha256", "size_bytes"]
    assert {row["relative_path"] for row in inventory_rows} >= {
        "provenance.csv",
        "effective_model_config.yaml",
    }
    inventory_by_path = {row["relative_path"]: row for row in inventory_rows}
    for relative_path in ("provenance.csv", "effective_model_config.yaml"):
        assert inventory_by_path[relative_path]["sha256"] == hashlib.sha256(
            (result.path / relative_path).read_bytes()
        ).hexdigest()
    model_config = OmegaConf.to_container(
        OmegaConf.load(result.path / "effective_model_config.yaml"), resolve=True
    )
    assert model_config == {"mass_kg": 0.027, "motor_delay_s": 0.03}


def test_generic_writer_is_seed_repeatable_without_mutating_the_record(tmp_path: Path) -> None:
    """A fresh UUID must not change deterministic observations or mutate motion arrays."""
    episode = _generic_episode()
    original_position = episode.position_enu_m.copy()
    original_diagnostics = {
        name: values.copy() for name, values in episode.diagnostic_arrays.items()
    }
    request = _generic_request()

    first = write_dataset([episode], request=request, output_root=tmp_path / "outputs")
    second = write_dataset([episode], request=request, output_root=tmp_path / "outputs")

    assert first.dataset_id != second.dataset_id
    assert (first.path / "public" / "observations.csv").read_bytes() == (
        second.path / "public" / "observations.csv"
    ).read_bytes()
    assert (first.path / "evaluation" / "diagnostics.csv").read_bytes() == (
        second.path / "evaluation" / "diagnostics.csv"
    ).read_bytes()
    assert np.array_equal(episode.position_enu_m, original_position)
    assert all(
        np.array_equal(episode.diagnostic_arrays[name], values)
        for name, values in original_diagnostics.items()
    )


@pytest.mark.parametrize(
    "episode",
    [
        _generic_episode(position_enu_m=np.zeros((300, 3))),
        _generic_episode(
            velocity_enu_mps=np.where(
                np.arange(301)[:, None] == 10,
                np.nan,
                np.zeros((301, 3)),
            )
        ),
        _generic_episode(
            diagnostic_arrays={"rotor_1_thrust_n": np.full(301, np.nan)},
        ),
        _generic_episode(t_s=np.arange(301, dtype=float) * 0.2 + 0.01),
    ],
)
def test_malformed_generic_payload_is_rejected_before_dataset_creation(
    tmp_path: Path, episode: MotionEpisode
) -> None:
    """Malformed core data must not be turned into a partial public publication."""
    with pytest.raises(ValueError):
        write_dataset(
            [episode],
            request=_generic_request(),
            output_root=tmp_path / "outputs",
        )

    assert not (tmp_path / "outputs" / "data_generation").exists()


def test_duplicate_or_mixed_record_ids_are_rejected_before_dataset_creation(tmp_path: Path) -> None:
    """A writer invocation has one record kind and unique episode identities."""
    first = _generic_episode()
    duplicate = _generic_episode(seed=42)

    with pytest.raises(ValueError, match="unique"):
        write_dataset(
            [first, duplicate], request=_generic_request(), output_root=tmp_path / "outputs"
        )
    with pytest.raises(ValueError, match="homogeneous"):
        write_dataset(
            [first, _episode()], request=_generic_request(), output_root=tmp_path / "outputs"
        )

    assert not (tmp_path / "outputs" / "data_generation").exists()


def test_generic_partial_episode_remains_a_failed_artifact_without_public_files(
    tmp_path: Path,
) -> None:
    """A structurally valid incomplete simulation keeps a failure audit, not public rows."""
    sample_count = 300
    steps = np.arange(sample_count, dtype=int)
    partial = _generic_episode(
        status="partial",
        failure_reason="simulation stopped before the final stored sample",
        steps=steps,
        t_s=steps.astype(float) * 0.2,
        position_enu_m=np.zeros((sample_count, 3)),
        velocity_enu_mps=np.zeros((sample_count, 3)),
        diagnostic_arrays={"rotor_1_thrust_n": np.zeros(sample_count)},
        event_records=(
            _motion_event(0.0, float(steps[-1]) * 0.2, completed=False),
        ),
    )

    result = write_dataset(
        [partial], request=_generic_request(), output_root=tmp_path / "outputs"
    )

    assert result.status == "failed"
    assert (result.path / "failed_episodes.csv").is_file()
    assert not (result.path / "public").exists()


@pytest.mark.parametrize(
    "event",
    [_motion_event(-0.1, 0.0), _motion_event(0.0, 60.1)],
)
def test_complete_generic_events_must_stay_within_the_sample_window(
    tmp_path: Path,
    event: MotionEvent,
) -> None:
    """Complete episodes cannot publish command evidence outside 0 through 60 seconds."""
    output_root = tmp_path / "outputs"

    with pytest.raises(ValueError, match="within the episode window"):
        write_dataset(
            [_generic_episode(event_records=(event,))],
            request=_generic_request(),
            output_root=output_root,
        )

    assert not (output_root / "data_generation").exists()


def test_partial_generic_event_cannot_extend_beyond_the_last_stored_sample(
    tmp_path: Path,
) -> None:
    """A partial audit retains in-progress events, but never future timestamps."""
    sample_count = 300
    steps = np.arange(sample_count, dtype=int)
    partial = _generic_episode(
        status="partial",
        failure_reason="simulation stopped before the final stored sample",
        steps=steps,
        t_s=steps.astype(float) * 0.2,
        position_enu_m=np.zeros((sample_count, 3)),
        velocity_enu_mps=np.zeros((sample_count, 3)),
        diagnostic_arrays={"rotor_1_thrust_n": np.zeros(sample_count)},
        event_records=(_motion_event(0.0, 60.0, completed=False),),
    )
    output_root = tmp_path / "outputs"

    with pytest.raises(ValueError, match="within the episode window"):
        write_dataset([partial], request=_generic_request(), output_root=output_root)

    assert not (output_root / "data_generation").exists()


def test_non_x8_request_cannot_reuse_x8_source_identity() -> None:
    """New object metadata must be explicit rather than silently inheriting X8 provenance."""
    with pytest.raises(ValueError, match="X8"):
        _generic_request(
            source_id=X8_SOURCE_ID,
            input_id=f"doi:{X8_DATASET_DOI}",
        )


@pytest.mark.parametrize(
    "source_files",
    [
        [
            SourceFileRecord(
                path="data_sources/test_quadrotor_reference/reference.csv",
                sha256="a" * 64,
                url="https://example.invalid/test-quadrotor/reference.csv",
            )
        ],
        (
            record
            for record in (
                SourceFileRecord(
                    path="data_sources/test_quadrotor_reference/reference.csv",
                    sha256="a" * 64,
                    url="https://example.invalid/test-quadrotor/reference.csv",
                ),
            )
        ),
    ],
)
def test_non_x8_request_requires_concrete_source_file_tuple(
    source_files: object,
) -> None:
    """Mutable or one-shot source provenance cannot become an empty later snapshot."""
    with pytest.raises(ValueError, match="source_files.*tuple"):
        _generic_request(source_files=source_files)


def test_writer_revalidates_a_mutated_non_x8_request_before_dataset_creation(
    tmp_path: Path,
) -> None:
    """A cleared mutable configuration cannot turn into an empty published snapshot."""
    model_config = {"mass_kg": 0.027, "motor_delay_s": 0.03}
    request = _generic_request(model_config=model_config)
    model_config.clear()
    output_root = tmp_path / "outputs"

    with pytest.raises(ValueError, match="model_config"):
        write_dataset([_generic_episode()], request=request, output_root=output_root)

    assert not (output_root / "data_generation").exists()


def test_writer_rejects_a_changed_generation_code_hash_before_dataset_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A code edit between generation start and publishing cannot get a false receipt."""
    output_root = tmp_path / "outputs"
    request = _generic_request(code_hash_at_start="a" * 64)
    monkeypatch.setattr(dataset_module, "_code_hash", lambda: "b" * 64)

    with pytest.raises(ValueError, match="code hash.*changed"):
        write_dataset([_generic_episode()], request=request, output_root=output_root)

    assert not (output_root / "data_generation").exists()


def test_record_kind_cannot_mismatch_request_object_before_dataset_creation(
    tmp_path: Path,
) -> None:
    """A generic object cannot silently inherit X8 provenance, or vice versa."""
    output_root = tmp_path / "outputs"

    with pytest.raises(ValueError, match="MotionEpisode"):
        write_dataset(
            [_generic_episode()],
            request=GenerationRequest(41, "diagnostic", "NOT_RUN"),
            output_root=output_root,
        )
    with pytest.raises(ValueError, match="GeneratedEpisode"):
        write_dataset(
            [_episode()], request=_generic_request(), output_root=output_root
        )

    assert not (output_root / "data_generation").exists()


def test_malformed_x8_diagnostics_are_rejected_before_dataset_creation(tmp_path: Path) -> None:
    """Legacy diagnostics must be finite before a completed X8 result can publish."""
    episode = _episode()
    episode.raw_commands[0, 0] = np.nan
    output_root = tmp_path / "outputs"

    with pytest.raises(ValueError, match="raw_commands"):
        write_dataset(
            [episode],
            request=GenerationRequest(17, "diagnostic", "NOT_RUN"),
            output_root=output_root,
        )

    assert not (output_root / "data_generation").exists()


def test_legacy_x8_request_keeps_the_existing_x8_diagnostic_columns(tmp_path: Path) -> None:
    """The generic writer must not change the fixed X8 diagnostic CSV schema."""
    result = write_dataset(
        [_episode()],
        request=GenerationRequest(17, "diagnostic", "NOT_RUN"),
        output_root=tmp_path / "outputs",
    )
    diagnostic_columns, _ = _read_csv(result.path / "evaluation" / "diagnostics.csv")

    assert diagnostic_columns == [
        "episode_id",
        "step",
        "t_s",
        "airspeed_mps",
        "alpha_rad",
        "beta_rad",
        "roll_rad",
        "pitch_rad",
        "yaw_rad",
        "left_elevon_rad",
        "right_elevon_rad",
        "throttle",
    ]
def test_explicit_episode_splits_require_exact_ids_labels_and_counts() -> None:
    episode_ids = [f"episode-{index:03d}" for index in range(160)]
    valid = {
        episode_id: (
            "train" if index < 112 else "validation" if index < 136 else "test"
        )
        for index, episode_id in enumerate(episode_ids)
    }
    assert dataset_module.assign_episode_splits(
        episode_ids, master_seed=17, diagnostic=False, explicit=valid
    ) == valid

    missing = dict(valid)
    missing.pop(episode_ids[-1])
    with pytest.raises(ValueError, match="IDs"):
        dataset_module.assign_episode_splits(
            episode_ids, master_seed=17, diagnostic=False, explicit=missing
        )
    invalid = dict(valid)
    invalid[episode_ids[-1]] = "diagnostic"
    with pytest.raises(ValueError, match="label"):
        dataset_module.assign_episode_splits(
            episode_ids, master_seed=17, diagnostic=False, explicit=invalid
        )
    wrong_count = dict(valid)
    wrong_count[episode_ids[111]] = "validation"
    with pytest.raises(ValueError, match="70/15/15"):
        dataset_module.assign_episode_splits(
            episode_ids, master_seed=17, diagnostic=False, explicit=wrong_count
        )
