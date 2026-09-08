"""Model-neutral public observation sequences; evaluation files are never read."""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from contextlib import ExitStack
from numbers import Integral
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf

from contracts.v1.csv_io import read_public_dataset
from contracts.v1.types import EpisodeMetadata
from contracts.v1.validation import MAX_SAMPLE_COUNT, PUBLIC_OBSERVATION_COLUMNS, VARIANT_SIGMAS

INDEX_COLUMNS = ("sequence_id", "episode_id", "variant_id", "split", "start_step", "end_step")
SPLITS = ("train", "validation", "test", "diagnostic")


def validate_training_config(config: dict) -> None:
    """No hidden model context length or silent truncation of a training request."""
    expected = {"window_samples", "train_windows_per_episode", "evaluation_stride_samples"}
    if set(config) not in (expected, expected | {"identity"}):
        raise ValueError(f"training settings must contain exactly {sorted(expected)}")
    for key, value in config.items():
        if key == "identity":
            from .identity_training import validate_identity_config

            validate_identity_config(value)
            continue
        if key == "window_samples" and value is None:
            continue
        minimum = 2 if key == "window_samples" else 1
        if (
            isinstance(value, bool)
            or not isinstance(value, Integral)
            or not minimum <= value <= MAX_SAMPLE_COUNT
        ):
            raise ValueError(f"invalid training setting: {key}")


def sequence_rows(episodes, config: dict, *, seed: int) -> list[dict]:
    """Choose intervals once per parent, then reuse them across observation variants."""
    validate_training_config(config)
    episodes = tuple(episodes)
    transitions = {}
    if "identity" in config:
        from .identity_training import transition_steps

        transitions = transition_steps(
            episodes, seed=seed, margin_s=config["identity"]["transition_margin_s"]
        )
    rows = []
    for episode in episodes:
        if episode.split not in SPLITS:
            raise ValueError("unsupported sequence split")
        length = config["window_samples"] or episode.sample_count
        if length > episode.sample_count:
            raise ValueError(f"window exceeds episode length: {episode.episode_id}")
        last_start = episode.sample_count - length
        if config["window_samples"] is None:
            starts = [0]
        elif episode.split == "train":
            identity = int.from_bytes(
                hashlib.sha256(episode.episode_id.encode()).digest()[:4], "little"
            )
            rng = np.random.default_rng(np.random.SeedSequence([seed, identity]))
            starts = sorted(
                int(s)
                for s in rng.choice(
                    last_start + 1,
                    size=min(config["train_windows_per_episode"], last_start + 1),
                    replace=False,
                )
            )
        else:
            starts = sorted(
                set(range(0, last_start + 1, config["evaluation_stride_samples"])) | {last_start}
            )
        if transitions and config["window_samples"] is not None:
            # Add a boundary-containing window; never replace the ordinary coverage.
            margin_steps = int(np.ceil(config["identity"]["transition_margin_s"] / episode.dt_s))
            before = min(margin_steps // 2 or 1, length - 1)
            boundary_start = max(0, min(last_start, transitions[episode.episode_id] - before))
            starts = sorted(set(starts) | {boundary_start})
        for variant_id in VARIANT_SIGMAS:
            for start in starts:
                end = start + length - 1
                assert 0 <= start <= end < episode.sample_count
                identifier = hashlib.sha256(
                    f"{episode.episode_id}/{variant_id}/{start}/{end}".encode()
                ).hexdigest()
                rows.append(
                    dict(
                        sequence_id=identifier,
                        episode_id=episode.episode_id,
                        variant_id=variant_id,
                        split=episode.split,
                        start_step=start,
                        end_step=end,
                    )
                )
    return rows


def write_sequence_index(
    path: Path, episodes, config: dict, *, seed: int, object_type=None
) -> None:
    """Publish indexes and human-readable split exports without evaluation inputs."""
    episodes = tuple(episodes)
    rows = sequence_rows(episodes, config, seed=seed)
    directory = path / "training"
    directory.mkdir(exist_ok=False)
    OmegaConf.save(OmegaConf.create(config), directory / "settings.yaml")
    with (directory / "sequences.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(row["split"] for row in rows)
    parents = {
        split: {row["episode_id"] for row in rows if row["split"] == split} for split in SPLITS
    }
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("split", "episodes", "sequences", "sample_rows")
        )
        writer.writeheader()
        writer.writerows(
            dict(
                split=split,
                episodes=len(parents[split]),
                sequences=counts[split],
                sample_rows=sum(
                    row["end_step"] - row["start_step"] + 1 for row in rows if row["split"] == split
                ),
            )
            for split in SPLITS
        )
    _write_split_exports(path, episodes, rows)
    if "identity" in config:
        from .identity_training import write_identity_exports

        write_identity_exports(
            path, episodes, rows, seed=seed, config=config["identity"], object_type=object_type
        )


def _write_split_exports(path: Path, episodes, rows) -> None:
    """Preserve parent assignments and stream each observation into exactly one split."""
    parent_split = {episode.episode_id: episode.split for episode in episodes}
    with ExitStack() as stack:
        writers = {}
        for split in SPLITS:
            directory = path / "training" / ("valid" if split == "validation" else split)
            directory.mkdir(exist_ok=False)
            index_file = stack.enter_context(
                (directory / "sequences.csv").open("w", encoding="utf-8", newline="")
            )
            index_writer = csv.DictWriter(index_file, fieldnames=INDEX_COLUMNS)
            index_writer.writeheader()
            index_writer.writerows(row for row in rows if row["split"] == split)
            output = stack.enter_context(
                (directory / "observations.csv").open("w", encoding="utf-8", newline="")
            )
            writers[split] = csv.DictWriter(output, fieldnames=PUBLIC_OBSERVATION_COLUMNS)
            writers[split].writeheader()
        with (path / "public" / "observations.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != PUBLIC_OBSERVATION_COLUMNS:
                raise ValueError("invalid observation export columns")
            for row in reader:
                split = parent_split.get(row["episode_id"])
                if split not in writers:
                    raise ValueError("observation export references an unknown parent")
                writers[split].writerow(row)


def read_sequence_index(path: Path, episodes) -> list[dict]:
    """Validate the entire index before returning even one requested training sample."""
    metadata = {item.episode_id: item for item in episodes}
    with (path / "training" / "sequences.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != INDEX_COLUMNS:
            raise ValueError("invalid training index columns")
        rows = list(reader)
    if not rows:
        raise ValueError("empty training index")
    seen = set()
    for row in rows:
        episode = metadata.get(row["episode_id"])
        if episode is None or row["split"] != episode.split:
            raise ValueError("training index parent/split mismatch")
        if row["variant_id"] not in VARIANT_SIGMAS:
            raise ValueError("invalid training variant")
        start, end = int(row["start_step"]), int(row["end_step"])
        if not 0 <= start < end < episode.sample_count:
            raise ValueError("training window is outside stored episode")
        key = (row["episode_id"], row["variant_id"], start, end)
        expected = hashlib.sha256("/".join(map(str, key)).encode()).hexdigest()
        if key in seen or row["sequence_id"] != expected:
            raise ValueError("duplicate or invalid training sequence identity")
        seen.add(key)
        row["start_step"], row["end_step"] = start, end
    return rows


def iter_training_sequences(dataset_path: Path, *, split: str):
    """Yield (index row, tuple[Observation]); no truth, commands or diagnostics in API."""
    if split not in SPLITS:
        raise ValueError("unsupported training split")
    path = Path(dataset_path)
    public = read_public_dataset(path / "public")
    rows = read_sequence_index(path, public.episodes)
    grouped = {}
    for record in public.observations:
        grouped.setdefault((record.episode_id, record.variant_id), []).append(record)
    for values in grouped.values():
        values.sort(key=lambda record: record.step)
    for row in rows:
        if row["split"] == split:
            records = grouped[(row["episode_id"], row["variant_id"])]
            yield row, tuple(records[row["start_step"] : row["end_step"] + 1])


def episode_metadata(rows: list[dict]) -> tuple[EpisodeMetadata, ...]:
    """Convert writer metadata without rereading all stored observations."""
    return tuple(
        EpisodeMetadata(
            row["episode_id"],
            row["split"],
            float(row["duration_s"]),
            int(row["sample_count"]),
            float(row["dt_s"]),
            row["identity_label"],
        )
        for row in rows
    )
