"""Variable-duration storage retains strict metadata and evaluation boundaries."""

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from omegaconf import OmegaConf

from contracts.v1.csv_io import read_evaluation_truth, read_public_dataset
from contracts.v1.validation import ContractError
from data_generation.v1.dataset import write_dataset
from data_generation.v1.records import MotionEpisode
from data_generation.v1.tests.test_multi_object_dataset import _generic_request


def _episode(count: int, episode_id: str = "flight") -> MotionEpisode:
    time = np.arange(count) * 0.2
    return MotionEpisode(
        episode_id,
        "complete",
        None,
        41,
        np.arange(count),
        time,
        np.column_stack((time, time * 0, time * 0)),
        np.column_stack((np.ones(count), time * 0, time * 0)),
        {},
        (),
    )


def _write(tmp_path: Path, *episodes: MotionEpisode) -> Path:
    return write_dataset(
        episodes, request=_generic_request(), output_root=tmp_path / "outputs"
    ).path


def _mutate(path: Path, change) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize("counts", [(601,), (2,), (3001,), (301, 601)])
def test_round_trip_variable_lengths(tmp_path: Path, counts: tuple[int, ...]) -> None:
    path = _write(tmp_path, *(_episode(n, f"flight-{i}") for i, n in enumerate(counts)))
    public = read_public_dataset(path / "public")
    truth = read_evaluation_truth(path / "evaluation/truth.csv")
    assert [e.sample_count for e in public.episodes] == list(counts)
    assert [e.duration_s for e in public.episodes] == [(n - 1) * 0.2 for n in counts]
    assert len(public.observations) == 3 * sum(counts)
    assert len(truth) == sum(counts)
    assert not hasattr(public, "truth")
    config = OmegaConf.load(path / "effective_config.yaml")
    assert dict(config.episode_sample_counts) == {f"flight-{i}": n for i, n in enumerate(counts)}


@pytest.mark.parametrize("count", [0, 1, 3002])
def test_writer_rejects_out_of_range_count(tmp_path: Path, count: int) -> None:
    with pytest.raises(ValueError, match="samples"):
        _write(tmp_path, _episode(count))
    assert not (tmp_path / "outputs").exists()


def test_writer_rejects_nonuniform_time(tmp_path: Path) -> None:
    episode = _episode(601)
    time = episode.t_s.copy()
    time[100] += 0.01
    with pytest.raises(ValueError, match="step"):
        _write(tmp_path, replace(episode, t_s=time))


@pytest.mark.parametrize(
    "field,value",
    [("duration_s", "119"), ("sample_count", "602"), ("duration_s", "601"), ("dt_s", "0.1")],
)
def test_reader_rejects_inconsistent_metadata(tmp_path: Path, field: str, value: str) -> None:
    path = _write(tmp_path, _episode(601))
    _mutate(path / "public/episodes.csv", lambda rows: rows[0].update({field: value}))
    with pytest.raises(ContractError, match="metadata"):
        read_public_dataset(path / "public")


@pytest.mark.parametrize("filename", ["public/observations.csv", "evaluation/truth.csv"])
@pytest.mark.parametrize("mutation", ["truncate", "time", "unknown", "duplicate"])
def test_reader_rejects_corrupted_sequences(tmp_path: Path, filename: str, mutation: str) -> None:
    path = _write(tmp_path, _episode(601))

    def corrupt(rows):
        if mutation == "truncate":
            rows.pop()
        elif mutation == "time":
            rows[-1]["t_s"] = "119.9"
        elif mutation == "unknown":
            rows[-1]["episode_id"] = "unknown"
        else:
            rows.append(rows[-1])

    _mutate(path / filename, corrupt)
    with pytest.raises(ContractError):
        if filename.startswith("public"):
            read_public_dataset(path / "public")
        else:
            read_evaluation_truth(path / filename)
