"""Synthetic class-availability inputs, separate from position validity and truth."""

from __future__ import annotations

import csv
import hashlib
import math
from contextlib import ExitStack
from numbers import Real

import numpy as np

from contracts.v1.validation import PUBLIC_OBSERVATION_COLUMNS

PATTERNS = ("acquired", "unknown", "lost")
IDENTITY_COLUMNS = (*PUBLIC_OBSERVATION_COLUMNS, "identity_known", "observed_object_type")


def validate_identity_config(config):
    """Require an explicit margin; never invent switching probabilities."""
    if not isinstance(config, dict) or set(config) != {"transition_margin_s"}:
        raise ValueError("identity requires transition_margin_s")
    margin = config["transition_margin_s"]
    if (
        isinstance(margin, bool)
        or not isinstance(margin, Real)
        or not math.isfinite(margin)
        or margin <= 0
    ):
        raise ValueError("identity transition margin must be finite and positive")


def transition_steps(episodes, *, seed, margin_s):
    """Draw once per parent on its saved grid, without consulting motion events."""
    validate_identity_config({"transition_margin_s": margin_s})
    transitions = {}
    for episode in episodes:
        margin = math.ceil(margin_s / episode.dt_s)
        end = episode.sample_count - 1 - margin
        if end < margin:
            raise ValueError("episode too short for identity transition margins")
        token = int.from_bytes(hashlib.sha256(episode.episode_id.encode()).digest()[:8], "little")
        rng = np.random.default_rng(np.random.SeedSequence([seed, token, 731]))
        step = int(rng.integers(margin, end + 1))
        assert margin <= step <= end
        transitions[episode.episode_id] = step
    return transitions


def write_identity_exports(path, episodes, index_rows, *, seed, config, object_type):
    """Write three input views. Only known rows contain the configured object type."""
    if not isinstance(object_type, str) or not object_type.strip() or object_type == "unknown":
        raise ValueError("identity export requires an explicit object_type")
    transitions = transition_steps(episodes, seed=seed, margin_s=config["transition_margin_s"])
    training = path / "training"
    with (training / "identity_schedule.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("episode_id", "transition_step", "transition_s")
        )
        writer.writeheader()
        writer.writerows(
            dict(
                episode_id=e.episode_id,
                transition_step=transitions[e.episode_id],
                transition_s=transitions[e.episode_id] * e.dt_s,
            )
            for e in episodes
        )
    parent_split = {e.episode_id: e.split for e in episodes}
    # Routing metadata and future switch times are never copied into input columns.
    splits = ("train", "validation", "test", "diagnostic")
    with ExitStack() as stack:
        writers = {}
        for split in splits:
            folder = training / ("valid" if split == "validation" else split)
            for pattern in PATTERNS:
                directory = folder / pattern
                directory.mkdir(exist_ok=False)
                output = stack.enter_context(
                    (directory / "observations.csv").open("w", encoding="utf-8", newline="")
                )
                writers[split, pattern] = csv.DictWriter(output, fieldnames=IDENTITY_COLUMNS)
                writers[split, pattern].writeheader()
                with (directory / "sequences.csv").open(
                    "w", encoding="utf-8", newline=""
                ) as stream:
                    writer = csv.DictWriter(stream, fieldnames=tuple(index_rows[0]))
                    writer.writeheader()
                    for row in index_rows:
                        if row["split"] == split:
                            identifier = hashlib.sha256(
                                f"{row['sequence_id']}/{pattern}".encode()
                            ).hexdigest()
                            writer.writerow({**row, "sequence_id": identifier})
        with (path / "public" / "observations.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != PUBLIC_OBSERVATION_COLUMNS:
                raise ValueError("invalid identity source columns")
            for row in reader:
                parent = row["episode_id"]
                step = int(row["step"])
                for pattern in PATTERNS:
                    known = (pattern == "acquired" and step >= transitions[parent]) or (
                        pattern == "lost" and step < transitions[parent]
                    )
                    writers[parent_split[parent], pattern].writerow(
                        {
                            **row,
                            "identity_known": int(known),
                            "observed_object_type": object_type if known else "unknown",
                        }
                    )
    with (training / "identity_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("split", "pattern", "episodes", "sequences"))
        writer.writeheader()
        for split in splits:
            selected = [r for r in index_rows if r["split"] == split]
            for pattern in PATTERNS:
                writer.writerow(
                    dict(
                        split=split,
                        pattern=pattern,
                        episodes=len({r["episode_id"] for r in selected}),
                        sequences=len(selected),
                    )
                )
