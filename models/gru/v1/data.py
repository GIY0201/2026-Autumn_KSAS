"""Split-isolated observation loading and reproducible trajectory windows."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from omegaconf import OmegaConf
from torch.utils.data import Dataset

from contracts.v1.validation import PUBLIC_OBSERVATION_COLUMNS

from .model import DT_S, FUTURE_SAMPLES, HISTORY_S, HISTORY_SAMPLES, resolve_config

WINDOW_SAMPLES = HISTORY_SAMPLES + FUTURE_SAMPLES
INDEX_COLUMNS = ("sequence_id", "episode_id", "variant_id", "split", "start_step", "end_step")


def _metadata(path):
    with (Path(path) / "public/episodes.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [r["episode_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate episode metadata")
    for row in rows:
        if row["split"] not in ("train", "validation", "test", "diagnostic"):
            raise ValueError("unsupported split")
        if not np.isclose(float(row["dt_s"]), DT_S):
            raise ValueError("unsupported sampling period")
    return rows


def build_window_rows(path, config):
    """Read metadata only; future test observations remain sealed."""
    config = resolve_config(config)
    cfg = config["data"]
    dataset_path = Path(path)
    settings_path = dataset_path / "training" / "settings.yaml"
    inventory = _inventory(dataset_path)
    balanced_path = "training/balanced_windows.csv"
    has_frozen_balance = inventory is not None and balanced_path in inventory
    if settings_path.is_file():
        if inventory is not None and "training/settings.yaml" in inventory:
            _verify_inventory_file(dataset_path, "training/settings.yaml", inventory)
        settings = OmegaConf.to_container(OmegaConf.load(settings_path), resolve=True)
        if isinstance(settings, dict) and "behavior_balance" in settings:
            return _read_balanced_window_rows(dataset_path, cfg["variant_id"])
    if has_frozen_balance:
        return _read_balanced_window_rows(dataset_path, cfg["variant_id"])
    seed = cfg.get("window_seed", config["training"]["seed"])
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("window_seed must be a nonnegative integer")
    count = int(cfg["train_windows_per_episode"])
    stride = int(cfg["evaluation_stride_samples"])
    if count < 1 or stride < 1:
        raise ValueError("window count and stride must be positive")
    rows = []
    for episode in sorted(_metadata(path), key=lambda item: item["episode_id"]):
        split = episode["split"]
        if split == "diagnostic":
            continue
        last = int(episode["sample_count"]) - WINDOW_SAMPLES
        if last < 0:
            raise ValueError("episode is shorter than input plus forecast")
        if split == "train":
            identity = int.from_bytes(
                hashlib.sha256(episode["episode_id"].encode()).digest()[:4], "little"
            )
            rng = np.random.default_rng(np.random.SeedSequence([seed, identity]))
            if last + 1 < count:
                raise ValueError("episode has fewer distinct windows than requested")
            starts = sorted(rng.choice(last + 1, count, replace=False).tolist())
        else:
            starts = list(range(0, last + 1, stride))
        for start in starts:
            variant = cfg["variant_id"]
            end = start + WINDOW_SAMPLES - 1
            key = f"{episode['episode_id']}/{variant}/{start}/{end}"
            rows.append(
                dict(
                    sequence_id=hashlib.sha256(key.encode()).hexdigest(),
                    episode_id=episode["episode_id"],
                    variant_id=variant,
                    split=split,
                    start_step=start,
                    end_step=end,
                )
            )
    return rows


def _inventory(path: Path) -> dict[str, dict] | None:
    source = path / "files.csv"
    if not source.is_file():
        return None
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"relative_path", "sha256", "size_bytes"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("dataset file inventory has unexpected columns")
        rows = list(reader)
    inventory = {row["relative_path"]: row for row in rows}
    if len(inventory) != len(rows):
        raise ValueError("dataset file inventory contains duplicate paths")
    return inventory


def _verify_inventory_file(path: Path, relative_path: str, inventory: dict[str, dict]) -> Path:
    record = inventory.get(relative_path)
    if record is None:
        raise ValueError(f"dataset file inventory is missing {relative_path}")
    source = path / relative_path
    if not source.is_file():
        raise ValueError(f"dataset file is missing: {relative_path}")
    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_hash != record["sha256"]:
        raise ValueError(f"dataset file hash mismatch: {relative_path}")
    if source.stat().st_size != int(record["size_bytes"]):
        raise ValueError(f"dataset file size mismatch: {relative_path}")
    return source


def _read_balanced_window_rows(path: Path, variant_id: str) -> list[dict]:
    """Validate and consume the label-free index frozen by the dataset writer."""
    inventory = _inventory(path)
    if inventory is None:
        raise ValueError("balanced window index inventory is missing")
    source = _verify_inventory_file(path, "training/balanced_windows.csv", inventory)
    metadata = {row["episode_id"]: row for row in _metadata(path)}
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != INDEX_COLUMNS:
            raise ValueError("balanced window index has unexpected columns")
        raw_rows = list(reader)
    rows = []
    seen = set()
    for raw in raw_rows:
        if raw["variant_id"] != variant_id:
            continue
        episode = metadata.get(raw["episode_id"])
        if episode is None or raw["split"] != episode["split"]:
            raise ValueError("balanced window index parent/split mismatch")
        start, end = int(raw["start_step"]), int(raw["end_step"])
        if end - start != WINDOW_SAMPLES - 1 or not 0 <= start < end < int(
            episode["sample_count"]
        ):
            raise ValueError("balanced window index is outside its Episode")
        key = (raw["episode_id"], variant_id, start, end)
        expected = hashlib.sha256("/".join(map(str, key)).encode()).hexdigest()
        if key in seen or raw["sequence_id"] != expected:
            raise ValueError("balanced window index identity is invalid or duplicated")
        seen.add(key)
        rows.append(
            {
                **raw,
                "start_step": start,
                "end_step": end,
            }
        )
    if not rows or not {"train", "validation"} <= {row["split"] for row in rows}:
        raise ValueError("balanced window index lacks train or validation rows")
    return rows


def _observations(path, split, variant):
    directory = "valid" if split == "validation" else split
    source = Path(path) / "training" / directory / "observations.csv"
    metadata = {r["episode_id"]: r for r in _metadata(path) if r["split"] == split}
    grouped = {}
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != PUBLIC_OBSERVATION_COLUMNS:
            raise ValueError("unexpected observation columns")
        for row in reader:
            if row["episode_id"] not in metadata:
                raise ValueError("cross-split episode in observation export")
            if row["variant_id"] != variant:
                continue
            if row["valid"] != "1":
                raise ValueError("incomplete observation episode")
            grouped.setdefault(row["episode_id"], []).append(
                [
                    int(row["step"]),
                    float(row["t_s"]),
                    float(row["x_m"]),
                    float(row["y_m"]),
                    float(row["z_m"]),
                ]
            )
    if set(grouped) != set(metadata):
        raise ValueError("missing episode observations")
    for episode, values in grouped.items():
        array = np.asarray(values, dtype=np.float64)
        expected = int(metadata[episode]["sample_count"])
        if len(array) != expected or not np.isfinite(array).all():
            raise ValueError("nonfinite or incomplete episode")
        if not np.array_equal(array[:, 0], np.arange(expected)):
            raise ValueError("noncontiguous observation steps")
        if not np.allclose(np.diff(array[:, 1]), DT_S, rtol=0, atol=1e-7):
            raise ValueError("nonuniform or reversed timestamps")
        grouped[episode] = array
    return grouped, hashlib.sha256(source.read_bytes()).hexdigest()


class TrajectoryDataset(Dataset):
    """Only four public features and normalized relative observation targets."""

    def __init__(self, positions, timestamps, targets, rows, scale_m, source_sha256):
        if not np.isfinite(scale_m) or scale_m <= 0:
            raise ValueError("normalization scale must be positive and finite")
        self.input_positions_m = np.asarray(positions, dtype=np.float64)
        self.timestamps_s = np.asarray(timestamps, dtype=np.float64)
        self.anchors = self.input_positions_m[:, -1]
        self.targets_m = np.asarray(targets, dtype=np.float64)
        self.window_rows = rows
        self.source_sha256 = source_sha256
        self.input_features = np.concatenate(
            (
                (self.input_positions_m - self.anchors[:, None]) / scale_m,
                ((self.timestamps_s - self.timestamps_s[:, -1:]) / HISTORY_S)[..., None],
            ),
            axis=-1,
        ).astype(np.float32)
        self.labels = (self.targets_m / scale_m).astype(np.float32)
        assert self.input_features.shape == (len(rows), HISTORY_SAMPLES, 4)
        assert self.labels.shape == (len(rows), FUTURE_SAMPLES, 3)
        assert np.isfinite(self.labels).all() and np.isfinite(self.input_features).all()

    def __len__(self):
        return len(self.window_rows)

    def __getitem__(self, index):
        return {
            "input_features": torch.from_numpy(self.input_features[index]),
            "labels": torch.from_numpy(self.labels[index]),
        }


def _arrays(path, split, config, rows):
    grouped, source_hash = _observations(path, split, resolve_config(config)["data"]["variant_id"])
    selected = [r for r in rows if r["split"] == split]
    if not selected:
        raise ValueError(f"no windows for {split}")
    positions, timestamps, targets = [], [], []
    for row in selected:
        sequence = grouped[row["episode_id"]][row["start_step"] : row["end_step"] + 1]
        history = sequence[:HISTORY_SAMPLES]
        positions.append(history[:, 2:5])
        timestamps.append(history[:, 1])
        targets.append(sequence[HISTORY_SAMPLES:, 2:5] - history[-1, 2:5])
    return positions, timestamps, targets, selected, source_hash


def load_split(dataset_path, split, config, normalization):
    """Explicit test call is the sole path that opens the test observations."""
    if split not in ("train", "validation", "test"):
        raise ValueError("invalid split")
    arrays = _arrays(dataset_path, split, config, build_window_rows(dataset_path, config))
    return TrajectoryDataset(*arrays[:4], normalization["scale_m"], arrays[4])


@dataclass
class PreparedData:
    train: TrajectoryDataset
    validation: TrajectoryDataset
    window_rows: list
    normalization: dict


def prepare_data(dataset_path, config, output_dir):
    rows = build_window_rows(dataset_path, config)
    train = _arrays(dataset_path, "train", config, rows)
    targets = np.asarray(train[2], dtype=np.float64)
    scale = float(np.percentile(np.linalg.norm(targets, axis=-1), 95))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("train normalization scale is not positive")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "window_index.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    normalization = dict(
        scale_m=scale,
        method="train_future_relative_l2_percentile_95",
        train_observations_sha256=train[4],
        train_target_sha256=hashlib.sha256(targets.astype("<f8").tobytes()).hexdigest(),
        window_index_sha256=hashlib.sha256((output / "window_index.csv").read_bytes()).hexdigest(),
    )
    with (output / "normalization.json").open("x", encoding="utf-8") as handle:
        json.dump(normalization, handle, indent=2)
    valid = _arrays(dataset_path, "validation", config, rows)
    return PreparedData(
        TrajectoryDataset(*train[:4], scale, train[4]),
        TrajectoryDataset(*valid[:4], scale, valid[4]),
        rows,
        normalization,
    )
