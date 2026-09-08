"""CSV readers that preserve the public/evaluation data boundary."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from .types import PublicDataset, TruthPoint
from .validation import (
    PUBLIC_EPISODE_COLUMNS,
    TRUTH_COLUMNS,
    ContractError,
    _is_close,
    _parse_episodes,
    _read_exact_csv,
    load_validated_public_records,
)


def _finite_float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ContractError(f"{field} is not numeric") from error
    if not math.isfinite(parsed):
        raise ContractError(f"{field} is not finite")
    return parsed


def _strict_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ContractError(f"{field} is not an integer") from error
    if str(parsed) != value:
        raise ContractError(f"{field} is not an integer")
    return parsed


def read_public_dataset(directory: Path) -> PublicDataset:
    """Read the public-only dataset after strict schema validation."""
    episodes, observations = load_validated_public_records(directory)
    return PublicDataset(episodes=episodes, observations=observations)


def read_evaluation_truth(path: Path) -> tuple[TruthPoint, ...]:
    """Read truth only from the explicit ``evaluation/truth.csv`` location."""
    if path.name != "truth.csv" or path.parent.name != "evaluation":
        raise ContractError("truth must be read from an explicit evaluation/truth.csv path")
    if not path.is_file():
        raise ContractError(f"truth file is missing: {path}")
    metadata = _parse_episodes(
        _read_exact_csv(path.parent.parent / "public" / "episodes.csv", PUBLIC_EPISODE_COLUMNS)
    )
    episodes = {episode.episode_id: episode for episode in metadata}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != TRUTH_COLUMNS:
            raise ContractError("unexpected columns in truth.csv")
        rows = list(reader)
    points: list[TruthPoint] = []
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ContractError("incomplete row in truth.csv")
        points.append(
            TruthPoint(
                episode_id=row["episode_id"],
                step=_strict_int(row["step"], "step"),
                t_s=_finite_float(row["t_s"], "t_s"),
                x_m=_finite_float(row["x_m"], "x_m"),
                y_m=_finite_float(row["y_m"], "y_m"),
                z_m=_finite_float(row["z_m"], "z_m"),
                vx_mps=_finite_float(row["vx_mps"], "vx_mps"),
                vy_mps=_finite_float(row["vy_mps"], "vy_mps"),
                vz_mps=_finite_float(row["vz_mps"], "vz_mps"),
                ax_mps2=_finite_float(row["ax_mps2"], "ax_mps2"),
                ay_mps2=_finite_float(row["ay_mps2"], "ay_mps2"),
                az_mps2=_finite_float(row["az_mps2"], "az_mps2"),
            )
        )
    if not points:
        raise ContractError("truth.csv must contain at least one row")
    counts = dict.fromkeys(episodes, 0)
    previous_key = None
    for point in points:
        if point.episode_id not in episodes:
            raise ContractError("truth references unknown episode_id")
        episode = episodes[point.episode_id]
        key = (point.episode_id, point.step)
        if previous_key is not None and key <= previous_key:
            raise ContractError("truth must be sorted by episode_id and step")
        previous_key = key
        if point.step != counts[point.episode_id] or not _is_close(
            point.t_s, point.step * episode.dt_s
        ):
            raise ContractError("truth steps and times must match episode metadata")
        counts[point.episode_id] += 1
    if any(counts[e.episode_id] != e.sample_count for e in metadata):
        raise ContractError("truth row counts must match episode metadata")
    return tuple(points)
