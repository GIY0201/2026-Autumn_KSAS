"""Behavioral tests for the X8-GEN-V1 CSV contract."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from contracts.v1.csv_io import read_evaluation_truth, read_public_dataset
from contracts.v1.validation import ContractError, validate_public_dataset

PUBLIC_OBSERVATION_COLUMNS = [
    "episode_id",
    "variant_id",
    "step",
    "t_s",
    "x_m",
    "y_m",
    "z_m",
    "sigma_x_m",
    "sigma_y_m",
    "sigma_z_m",
    "valid",
]

PUBLIC_EPISODE_COLUMNS = [
    "episode_id",
    "split",
    "duration_s",
    "sample_count",
    "dt_s",
    "identity_label",
]

TRUTH_COLUMNS = [
    "episode_id",
    "step",
    "t_s",
    "x_m",
    "y_m",
    "z_m",
    "vx_mps",
    "vy_mps",
    "vz_mps",
    "ax_mps2",
    "ay_mps2",
    "az_mps2",
]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_valid_public_dataset(directory: Path) -> None:
    directory.mkdir()
    _write_csv(
        directory / "episodes.csv",
        PUBLIC_EPISODE_COLUMNS,
        [
            {
                "episode_id": "episode-001",
                "split": "diagnostic",
                "duration_s": "60",
                "sample_count": "301",
                "dt_s": "0.2",
                "identity_label": "Unknown",
            }
        ],
    )
    rows: list[dict[str, str]] = []
    for variant_id, sigma in (("sigma_1m", "1"), ("sigma_3m", "3"), ("sigma_5m", "5")):
        for step in range(301):
            rows.append(
                {
                    "episode_id": "episode-001",
                    "variant_id": variant_id,
                    "step": str(step),
                    "t_s": f"{step / 5:.1f}",
                    "x_m": str(step),
                    "y_m": "2",
                    "z_m": "100",
                    "sigma_x_m": sigma,
                    "sigma_y_m": sigma,
                    "sigma_z_m": sigma,
                    "valid": "1",
                }
            )
    _write_csv(directory / "observations.csv", PUBLIC_OBSERVATION_COLUMNS, rows)


def test_public_reader_accepts_exact_episode_variants_without_truth_leakage(tmp_path: Path) -> None:
    """A public reader must expose only 3×301 observation rows and public metadata."""
    public_dir = tmp_path / "public"
    _write_valid_public_dataset(public_dir)

    dataset = read_public_dataset(public_dir)

    assert dataset.episode_ids == ("episode-001",)
    assert len(dataset.observations) == 903
    assert dataset.observations[0].t_s == 0.0
    assert dataset.observations[-1].t_s == 60.0
    assert not hasattr(dataset, "truth")
    assert not hasattr(dataset.observations[0], "command")


def test_validator_rejects_an_observation_sequence_with_a_missing_step(tmp_path: Path) -> None:
    """A dropped 0.2-second sample must not be silently accepted as a complete episode."""
    public_dir = tmp_path / "public"
    _write_valid_public_dataset(public_dir)
    observations = public_dir / "observations.csv"
    rows = list(csv.DictReader(observations.open(encoding="utf-8", newline="")))
    rows = [row for row in rows if not (row["variant_id"] == "sigma_3m" and row["step"] == "117")]
    _write_csv(observations, PUBLIC_OBSERVATION_COLUMNS, rows)

    with pytest.raises(ContractError, match="301 rows"):
        validate_public_dataset(public_dir)


def test_validator_rejects_unknown_public_file_and_wrong_variant_sigma(tmp_path: Path) -> None:
    """Only the public allowlist and declared per-axis sigma meanings are accepted."""
    public_dir = tmp_path / "public"
    _write_valid_public_dataset(public_dir)
    (public_dir / "commands.csv").write_text("secret\n", encoding="utf-8")

    with pytest.raises(ContractError, match="allowlist"):
        validate_public_dataset(public_dir)

    (public_dir / "commands.csv").unlink()
    observations = public_dir / "observations.csv"
    rows = list(csv.DictReader(observations.open(encoding="utf-8", newline="")))
    rows[0]["sigma_z_m"] = "9"
    _write_csv(observations, PUBLIC_OBSERVATION_COLUMNS, rows)

    with pytest.raises(ContractError, match="sigma"):
        validate_public_dataset(public_dir)


def test_truth_reader_requires_explicit_evaluation_path(tmp_path: Path) -> None:
    """Truth data is readable only from an explicit evaluation file, never public input."""
    public_dir = tmp_path / "public"
    _write_valid_public_dataset(public_dir)
    evaluation_dir = tmp_path / "evaluation"
    evaluation_dir.mkdir()
    _write_csv(
        evaluation_dir / "truth.csv",
        TRUTH_COLUMNS,
        [
            {
                "episode_id": "episode-001",
                "step": str(step),
                "t_s": f"{step / 5:.1f}",
                "x_m": "1",
                "y_m": "2",
                "z_m": "100",
                "vx_mps": "18",
                "vy_mps": "0",
                "vz_mps": "0",
                "ax_mps2": "0",
                "ay_mps2": "0",
                "az_mps2": "0",
            }
            for step in range(301)
        ],
    )

    public_dataset = read_public_dataset(public_dir)
    truth = read_evaluation_truth(evaluation_dir / "truth.csv")

    assert len(public_dataset.observations) == 903
    assert truth[0].vx_mps == 18.0
    assert truth[0].z_m == 100.0
