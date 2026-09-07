"""Tests for observation synthesis, split isolation, and durable dataset outputs."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from contracts.v1.validation import validate_public_dataset
from data_generation.v1.dataset import (
    GenerationRequest,
    assign_episode_splits,
    make_observation_variants,
    sample_variant_noise,
    write_dataset,
)
from data_generation.v1.motion_check import MotionCheckReport, ResidualSummary
from data_generation.v1.scenario import GeneratedEpisode


def _episode() -> GeneratedEpisode:
    steps = np.arange(301, dtype=int)
    position = np.column_stack((steps.astype(float), np.full(301, 2.0), np.full(301, 100.0)))
    velocity = np.column_stack((np.full(301, 5.0), np.zeros(301), np.zeros(301)))
    return GeneratedEpisode(
        episode_id="x8-00000017",
        status="complete",
        failure_reason=None,
        seed=17,
        steps=steps,
        t_s=steps.astype(float) * 0.2,
        position_enu_m=position,
        velocity_enu_mps=velocity,
        euler_rad=np.zeros((301, 3)),
        airspeed_mps=np.full(301, 18.0),
        alpha_rad=np.full(301, 0.1),
        beta_rad=np.zeros(301),
        raw_commands=np.column_stack((np.zeros(301), np.zeros(301), np.full(301, 0.5))),
        event_records=(),
    )


def test_independent_noise_has_declared_sigma_and_repeatable_variant_streams() -> None:
    """Gaussian noise must be zero-mean, independent, and deterministic by variant stream."""
    noise = sample_variant_noise(
        master_seed=123, episode_index=0, variant_id="sigma_3m", count=100_000
    )
    repeated = sample_variant_noise(
        master_seed=123, episode_index=0, variant_id="sigma_3m", count=100_000
    )
    correlation_xy = np.corrcoef(noise[:, 0], noise[:, 1])[0, 1]
    lag_correlation = np.corrcoef(noise[:-1, 2], noise[1:, 2])[0, 1]

    assert np.array_equal(noise, repeated)
    assert np.max(np.abs(noise.mean(axis=0))) < 0.06
    assert np.all(np.abs(noise.std(axis=0) - 3.0) < 0.06)
    assert abs(correlation_xy) < 0.02
    assert abs(lag_correlation) < 0.02


def test_episode_split_keeps_all_observation_variants_with_the_same_truth_parent() -> None:
    """A truth episode and its sigma variants must never cross train/validation/test boundaries."""
    episode_ids = [f"episode-{index:03d}" for index in range(100)]
    splits = assign_episode_splits(episode_ids, master_seed=7, diagnostic=False)
    truth = np.zeros((301, 3))
    variants = make_observation_variants(truth, master_seed=7, episode_index=4)

    assert list(splits.values()).count("train") == 70
    assert list(splits.values()).count("validation") == 15
    assert list(splits.values()).count("test") == 15
    assert set(variants) == {"sigma_1m", "sigma_3m", "sigma_5m"}
    assert all(variants[name].shape == (301, 3) for name in variants)
    assert len({splits[episode_ids[4]] for _ in variants}) == 1


def test_writer_creates_new_traceable_dataset_ids_with_only_public_allowed_files(
    tmp_path: Path,
) -> None:
    """Two identical requests must preserve separate result folders and a strict public boundary."""
    request = GenerationRequest(
        master_seed=17, mode="diagnostic", source_motion_check_status="NOT_RUN"
    )
    first = write_dataset([_episode()], request=request, output_root=tmp_path / "outputs")
    second = write_dataset([_episode()], request=request, output_root=tmp_path / "outputs")

    validate_public_dataset(first.path / "public")
    assert first.dataset_id != second.dataset_id
    assert first.path != second.path
    assert (first.path / "evaluation" / "truth.csv").is_file()
    assert (first.path / "manifest.csv").is_file()
    assert (first.path / "files.csv").is_file()
    assert not (first.path / "public" / "truth.csv").exists()
    manifest = next(
        csv.DictReader((first.path / "manifest.csv").open(encoding="utf-8", newline=""))
    )
    assert manifest["status"] == "complete"
    assert manifest["contract_version"] == "v1"
    assert len(manifest["code_hash"]) == 64


def test_writer_records_a_report_only_source_motion_check_in_evaluation(tmp_path: Path) -> None:
    """A source replay report belongs in evaluation and never upgrades to a public input."""
    report = MotionCheckReport(
        clips=(),
        full_metrics={"body_velocity_mps": ResidualSummary(rmse=1.0, bias=0.0, max_abs=2.0)},
        after_first_second_metrics={
            "body_velocity_mps": ResidualSummary(rmse=1.5, bias=0.1, max_abs=3.0)
        },
    )
    request = GenerationRequest(
        master_seed=17, mode="diagnostic", source_motion_check_status="REPORT_ONLY"
    )

    result = write_dataset(
        [_episode()],
        request=request,
        output_root=tmp_path / "outputs",
        source_motion_check_report=report,
    )

    assert (result.path / "evaluation" / "source_motion_check.csv").is_file()
    assert not (result.path / "public" / "source_motion_check.csv").exists()
