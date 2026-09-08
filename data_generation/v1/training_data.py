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
CORE_BEHAVIORS = (
    "selected_climb_straight",
    "selected_climb_turn",
    "selected_climb_s_turn",
    "selected_climb_spiral",
    "cruise_straight",
    "cruise_turn",
    "cruise_s_turn",
    "cruise_orbit",
    "selected_descent_straight",
    "selected_descent_turn",
    "selected_descent_s_turn",
    "selected_descent_spiral",
)
TURNING_BEHAVIORS = {item for item in CORE_BEHAVIORS if "straight" not in item}
BALANCE_COLUMNS = (
    "split",
    "phase",
    "behavior",
    "direction",
    "episode_count",
    "left_episodes",
    "right_episodes",
    "duration_s",
    "window_role",
    "core_windows",
    "left_windows",
    "right_windows",
    "target_points",
)


def validate_training_config(config: dict) -> None:
    """No hidden model context length or silent truncation of a training request."""
    expected = {"window_samples", "train_windows_per_episode", "evaluation_stride_samples"}
    optional = {"identity", "behavior_balance"}
    if not expected <= set(config) or not set(config) <= expected | optional:
        raise ValueError(f"training settings must contain exactly {sorted(expected)}")
    for key, value in config.items():
        if key == "identity":
            from .identity_training import validate_identity_config

            validate_identity_config(value)
            continue
        if key == "behavior_balance":
            if set(value) != {
                "schema",
                "core_windows_per_behavior",
                "transition_windows_per_boundary",
            } or value["schema"] != "behavior-window-balance-v1":
                raise ValueError("invalid behavior balance settings")
            for number_key in ("core_windows_per_behavior", "transition_windows_per_boundary"):
                number = value[number_key]
                if isinstance(number, bool) or not isinstance(number, Integral) or number < 0:
                    raise ValueError("invalid behavior balance window count")
            if value["core_windows_per_behavior"] < 2 or value["core_windows_per_behavior"] % 2:
                raise ValueError("core behavior window count must be a positive even integer")
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


def _core_behavior(event_id: str) -> str | None:
    for behavior in CORE_BEHAVIORS:
        if event_id == behavior or event_id.startswith(behavior + "_"):
            return behavior
    return None


def _phase(behavior: str) -> str:
    if behavior.startswith("selected_climb_"):
        return "climb"
    if behavior.startswith("cruise_"):
        return "cruise"
    return "descent"


def _record_intervals(record) -> tuple[str | None, list[tuple[str, float, float]]]:
    direction = None
    raw = []
    for event in record.event_records:
        if event.event_id == "turn_direction:left":
            direction = "left"
        elif event.event_id == "turn_direction:right":
            direction = "right"
        behavior = _core_behavior(event.event_id)
        if behavior is not None and event.end_s > event.start_s:
            raw.append((behavior, float(event.start_s), float(event.end_s)))
    raw.sort(key=lambda item: item[1])
    merged = []
    for behavior, start, end in raw:
        if merged and merged[-1][0] == behavior and abs(merged[-1][2] - start) <= 1e-8:
            merged[-1] = (behavior, merged[-1][1], end)
        else:
            merged.append((behavior, start, end))
    return direction, merged


def _sample_candidates(candidates, count: int, *, seed: int, label: str):
    if len(candidates) < count:
        raise ValueError(f"insufficient balanced window candidates for {label}")
    identity = int.from_bytes(hashlib.sha256(label.encode()).digest()[:4], "little")
    rng = np.random.default_rng(np.random.SeedSequence([seed, identity]))
    indices = sorted(rng.choice(len(candidates), count, replace=False).tolist())
    return [candidates[index] for index in indices]


def balanced_sequence_rows(episodes, motion_records, config: dict, *, seed: int):
    """Select equal fully-contained core windows without exporting private labels."""
    validate_training_config(config)
    balance = config.get("behavior_balance")
    if balance is None or config["window_samples"] != 91:
        raise ValueError("balanced sequence generation requires explicit 91-sample settings")
    metadata = {episode.episode_id: episode for episode in episodes}
    records = {record.episode_id: record for record in motion_records}
    if set(metadata) != set(records):
        raise ValueError("balanced window records must exactly match Episode metadata")
    candidates = Counter()
    candidate_rows = {}
    durations = Counter()
    episode_ids = {}
    direction_episode_ids = {}
    corpus_stats = {}
    transition_candidates = []
    for episode_id, episode in metadata.items():
        record = records[episode_id]
        direction, intervals = _record_intervals(record)
        for behavior, start_s, end_s in intervals:
            corpus_key = (episode.split, behavior)
            stats = corpus_stats.setdefault(
                corpus_key,
                {"duration_s": 0.0, "episodes": set(), "left": set(), "right": set()},
            )
            stats["duration_s"] += end_s - start_s
            stats["episodes"].add(episode_id)
            if behavior in TURNING_BEHAVIORS:
                if direction not in {"left", "right"}:
                    raise ValueError(f"turn direction is missing for {episode_id}")
                stats[direction].add(episode_id)
            if episode.split != "train":
                continue
            key = (behavior, direction if behavior in TURNING_BEHAVIORS else None)
            durations[behavior] += end_s - start_s
            episode_ids.setdefault(behavior, set()).add(episode_id)
            if behavior in TURNING_BEHAVIORS:
                direction_episode_ids.setdefault((behavior, direction), set()).add(episode_id)
            first = int(np.ceil(start_s / episode.dt_s - 1e-9))
            final_inside = int(np.ceil(end_s / episode.dt_s - 1e-9)) - 1
            last = final_inside - 90
            values = [(episode_id, start) for start in range(first, last + 1)]
            candidates[key] += len(values)
            candidate_rows.setdefault(key, []).extend(values)
            for boundary_s in (start_s, end_s):
                boundary_step = int(round(boundary_s / episode.dt_s))
                if not 0 < boundary_step < episode.sample_count - 1:
                    continue
                transition_start = max(
                    0, min(episode.sample_count - 91, boundary_step - 15)
                )
                transition_candidates.append((episode_id, transition_start, behavior))

    quota = int(balance["core_windows_per_behavior"])
    selected = []
    audit = []
    for behavior in CORE_BEHAVIORS:
        if behavior in TURNING_BEHAVIORS:
            left = _sample_candidates(
                candidate_rows.get((behavior, "left"), []),
                quota // 2,
                seed=seed,
                label=behavior + "/left",
            )
            right = _sample_candidates(
                candidate_rows.get((behavior, "right"), []),
                quota // 2,
                seed=seed,
                label=behavior + "/right",
            )
            chosen = left + right
            left_count, right_count = len(left), len(right)
            direction_label = "balanced_left_right"
        else:
            chosen = _sample_candidates(
                candidate_rows.get((behavior, None), []), quota, seed=seed, label=behavior
            )
            left_count = right_count = 0
            direction_label = "not_applicable"
        selected.extend((episode_id, start, behavior) for episode_id, start in chosen)
        audit.append(
            {
                "split": "train",
                "phase": _phase(behavior),
                "behavior": behavior,
                "direction": direction_label,
                "episode_count": len(episode_ids.get(behavior, set())),
                "left_episodes": len(direction_episode_ids.get((behavior, "left"), set())),
                "right_episodes": len(direction_episode_ids.get((behavior, "right"), set())),
                "duration_s": float(durations[behavior]),
                "window_role": "core",
                "core_windows": len(chosen),
                "left_windows": left_count,
                "right_windows": right_count,
                "target_points": len(chosen) * 75,
            }
        )
    for split in ("validation", "test"):
        if not any(episode.split == split for episode in metadata.values()):
            continue
        for behavior in CORE_BEHAVIORS:
            stats = corpus_stats.get((split, behavior))
            if stats is None:
                raise ValueError(f"missing {split} behavior coverage: {behavior}")
            audit.append(
                {
                    "split": split,
                    "phase": _phase(behavior),
                    "behavior": behavior,
                    "direction": (
                        "balanced_left_right"
                        if behavior in TURNING_BEHAVIORS
                        else "not_applicable"
                    ),
                    "episode_count": len(stats["episodes"]),
                    "left_episodes": len(stats["left"]),
                    "right_episodes": len(stats["right"]),
                    "duration_s": float(stats["duration_s"]),
                    "window_role": "corpus",
                    "core_windows": 0,
                    "left_windows": 0,
                    "right_windows": 0,
                    "target_points": 0,
                }
            )
    transition_count = int(balance["transition_windows_per_boundary"])
    transition_by_key = {}
    for episode_id, start, behavior in dict.fromkeys(transition_candidates):
        episode = metadata[episode_id]
        for offset in range(transition_count):
            shifted = min(episode.sample_count - 91, start + offset)
            transition_by_key.setdefault((episode_id, shifted), behavior)
    selected_keys = {(episode_id, start) for episode_id, start, _ in selected}
    transition_selected = [
        (episode_id, start, behavior)
        for (episode_id, start), behavior in transition_by_key.items()
        if (episode_id, start) not in selected_keys
    ]
    selected.extend(transition_selected)
    if transition_count:
        audit.append(
            {
                "split": "train",
                "phase": "boundary",
                "behavior": "core_behavior_boundary",
                "direction": "mixed",
                "episode_count": len({item[0] for item in transition_selected}),
                "left_episodes": 0,
                "right_episodes": 0,
                "duration_s": 0.0,
                "window_role": "transition",
                "core_windows": len(transition_selected),
                "left_windows": 0,
                "right_windows": 0,
                "target_points": len(transition_selected) * 75,
            }
        )
    rows = []
    for episode_id, start, _behavior in sorted(selected):
        end = start + 90
        for variant_id in VARIANT_SIGMAS:
            identifier = hashlib.sha256(
                f"{episode_id}/{variant_id}/{start}/{end}".encode()
            ).hexdigest()
            rows.append(
                dict(
                    sequence_id=identifier,
                    episode_id=episode_id,
                    variant_id=variant_id,
                    split="train",
                    start_step=start,
                    end_step=end,
                )
            )
    ordinary = sequence_rows(tuple(metadata.values()), config, seed=seed)
    rows.extend(row for row in ordinary if row["split"] != "train")
    expected_train = (12 * quota + len(transition_selected)) * 3
    assert len([row for row in rows if row["split"] == "train"]) == expected_train
    return rows, audit


def write_sequence_index(
    path: Path,
    episodes,
    config: dict,
    *,
    seed: int,
    object_type=None,
    motion_records=None,
) -> None:
    """Publish indexes and human-readable split exports without evaluation inputs."""
    episodes = tuple(episodes)
    audit = None
    if "behavior_balance" in config:
        if motion_records is None:
            raise ValueError("balanced window generation requires motion records")
        rows, audit = balanced_sequence_rows(episodes, motion_records, config, seed=seed)
    else:
        rows = sequence_rows(episodes, config, seed=seed)
    directory = path / "training"
    directory.mkdir(exist_ok=False)
    OmegaConf.save(OmegaConf.create(config), directory / "settings.yaml")
    with (directory / "sequences.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    if audit is not None:
        with (directory / "balanced_windows.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        with (path / "evaluation" / "behavior_balance.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=BALANCE_COLUMNS)
            writer.writeheader()
            writer.writerows(audit)
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
