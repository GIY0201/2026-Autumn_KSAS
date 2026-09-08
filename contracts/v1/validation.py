"""Strict validation for public X8-GEN-V1 CSV files."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .types import EpisodeMetadata, Observation

PUBLIC_OBSERVATION_COLUMNS = (
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
)
PUBLIC_EPISODE_COLUMNS = (
    "episode_id",
    "split",
    "duration_s",
    "sample_count",
    "dt_s",
    "identity_label",
)
TRUTH_COLUMNS = (
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
)

PUBLIC_ALLOWLIST = frozenset({"observations.csv", "episodes.csv"})
VARIANT_SIGMAS = {"sigma_1m": 1.0, "sigma_3m": 3.0, "sigma_5m": 5.0}
DURATION_S = 60.0
OUTPUT_DT_S = 0.2
SAMPLE_COUNT = 301
MAX_DURATION_S = 600.0
MIN_SAMPLE_COUNT = 2
MAX_SAMPLE_COUNT = int(MAX_DURATION_S / OUTPUT_DT_S) + 1
ALLOWED_SPLITS = frozenset({"train", "validation", "test", "diagnostic"})

assert SAMPLE_COUNT == int(DURATION_S / OUTPUT_DT_S) + 1
assert set(VARIANT_SIGMAS.values()) == {1.0, 3.0, 5.0}


class ContractError(ValueError):
    """Raised when a file violates the explicit v1 contract."""


def _read_exact_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ContractError(f"required file is missing: {path.name}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise ContractError(f"unexpected columns in {path.name}")
        rows = list(reader)

    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ContractError(f"incomplete row in {path.name}")
    return rows


def _float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ContractError(f"{field} is not numeric") from error
    if not math.isfinite(parsed):
        raise ContractError(f"{field} is not finite")
    return parsed


def _int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ContractError(f"{field} is not an integer") from error
    if str(parsed) != value:
        raise ContractError(f"{field} is not an integer")
    return parsed


def _is_close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)


def _parse_episodes(rows: Iterable[dict[str, str]]) -> tuple[EpisodeMetadata, ...]:
    episodes: list[EpisodeMetadata] = []
    seen_ids: set[str] = set()
    for row in rows:
        episode_id = row["episode_id"]
        if not episode_id or episode_id in seen_ids:
            raise ContractError("episode_id must be non-empty and unique")
        split = row["split"]
        if split not in ALLOWED_SPLITS:
            raise ContractError(f"unsupported split: {split}")
        duration_s = _float(row["duration_s"], "duration_s")
        sample_count = _int(row["sample_count"], "sample_count")
        dt_s = _float(row["dt_s"], "dt_s")
        if (
            not MIN_SAMPLE_COUNT <= sample_count <= MAX_SAMPLE_COUNT
            or not _is_close(duration_s, (sample_count - 1) * OUTPUT_DT_S)
            or not _is_close(dt_s, OUTPUT_DT_S)
        ):
            raise ContractError("episode metadata must agree on sample_count, duration and 5 Hz")
        identity_label = row["identity_label"]
        if not identity_label:
            raise ContractError("identity_label must be non-empty")
        seen_ids.add(episode_id)
        episodes.append(
            EpisodeMetadata(
                episode_id=episode_id,
                split=split,
                duration_s=duration_s,
                sample_count=sample_count,
                dt_s=dt_s,
                identity_label=identity_label,
            )
        )
    if not episodes:
        raise ContractError("episodes.csv must contain at least one episode")
    return tuple(episodes)


def _parse_observations(
    rows: Iterable[dict[str, str]], episodes: dict[str, EpisodeMetadata]
) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    previous_key: tuple[str, str, int] | None = None
    for row in rows:
        episode_id = row["episode_id"]
        variant_id = row["variant_id"]
        if episode_id not in episodes:
            raise ContractError(f"observation references unknown episode_id: {episode_id}")
        if variant_id not in VARIANT_SIGMAS:
            raise ContractError(f"unsupported variant_id: {variant_id}")
        step = _int(row["step"], "step")
        t_s = _float(row["t_s"], "t_s")
        values = {
            field: _float(row[field], field)
            for field in (
                "x_m",
                "y_m",
                "z_m",
                "sigma_x_m",
                "sigma_y_m",
                "sigma_z_m",
            )
        }
        valid = _int(row["valid"], "valid")
        if valid != 1:
            raise ContractError("public v1 observations require valid=1")
        expected_sigma = VARIANT_SIGMAS[variant_id]
        if not all(
            _is_close(values[field], expected_sigma)
            for field in ("sigma_x_m", "sigma_y_m", "sigma_z_m")
        ):
            raise ContractError(f"sigma fields do not match {variant_id}")
        expected_time_s = step * OUTPUT_DT_S
        if not _is_close(t_s, expected_time_s):
            raise ContractError("t_s does not match the declared 0.2-second step")
        key = (episode_id, variant_id, step)
        if previous_key is not None and key <= previous_key:
            raise ContractError("observations.csv is not sorted by episode_id, variant_id, step")
        previous_key = key
        observation = Observation(
            episode_id=episode_id,
            variant_id=variant_id,
            step=step,
            t_s=t_s,
            x_m=values["x_m"],
            y_m=values["y_m"],
            z_m=values["z_m"],
            sigma_x_m=values["sigma_x_m"],
            sigma_y_m=values["sigma_y_m"],
            sigma_z_m=values["sigma_z_m"],
            valid=valid,
        )
        observations.append(observation)
        grouped[(episode_id, variant_id)].append(observation)

    for episode_id in sorted(episodes):
        sample_count = episodes[episode_id].sample_count
        for variant_id in VARIANT_SIGMAS:
            sequence = grouped.get((episode_id, variant_id), [])
            if len(sequence) != sample_count:
                message = (
                    f"{episode_id}/{variant_id} must contain {sample_count} rows, "
                    f"got {len(sequence)}"
                )
                raise ContractError(message)
            if [observation.step for observation in sequence] != list(range(sample_count)):
                raise ContractError(f"{episode_id}/{variant_id} steps must match metadata")
    return tuple(observations)


def load_validated_public_records(
    directory: Path,
) -> tuple[tuple[EpisodeMetadata, ...], tuple[Observation, ...]]:
    """Read public files after enforcing the full v1 allowlist and row contract."""
    if not directory.is_dir():
        raise ContractError(f"public directory does not exist: {directory}")
    names = {entry.name for entry in directory.iterdir()}
    if names != PUBLIC_ALLOWLIST:
        extra = sorted(names - PUBLIC_ALLOWLIST)
        missing = sorted(PUBLIC_ALLOWLIST - names)
        raise ContractError(
            f"public directory violates allowlist; extra={extra}, missing={missing}"
        )
    episodes = _parse_episodes(_read_exact_csv(directory / "episodes.csv", PUBLIC_EPISODE_COLUMNS))
    observations = _parse_observations(
        _read_exact_csv(directory / "observations.csv", PUBLIC_OBSERVATION_COLUMNS),
        {episode.episode_id: episode for episode in episodes},
    )
    return episodes, observations


def validate_public_dataset(directory: Path) -> None:
    """Validate public records without exposing evaluation-only data."""
    load_validated_public_records(directory)
