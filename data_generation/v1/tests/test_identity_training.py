"""Identity variants change available class information, never trajectory or split."""

import csv
from dataclasses import replace

import pytest

from contracts.v1.csv_io import read_public_dataset
from data_generation.v1.tests.test_training_data import settings
from data_generation.v1.training_data import write_sequence_index
from visualization.v1.tests.test_variable_playback import variable_path


def read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_three_patterns_keep_positions_noise_and_parent_splits(tmp_path):
    path = variable_path(tmp_path, (101, 101, 101))
    public = read_public_dataset(path / "public")
    episodes = tuple(
        replace(e, split=s)
        for e, s in zip(public.episodes, ("train", "validation", "test"), strict=True)
    )
    config = {**settings(), "identity": {"transition_margin_s": 3.0}}
    write_sequence_index(path, episodes, config, seed=17, object_type="helicopter")
    source = read(path / "public" / "observations.csv")
    switches = read(path / "training" / "identity_schedule.csv")
    for episode, folder in zip(episodes, ("train", "valid", "test"), strict=True):
        transition = int(
            next(r["transition_step"] for r in switches if r["episode_id"] == episode.episode_id)
        )
        assert 15 <= transition <= 85
        original = [r for r in source if r["episode_id"] == episode.episode_id]
        for pattern in ("acquired", "unknown", "lost"):
            rows = read(path / "training" / folder / pattern / "observations.csv")
            assert len(rows) == 303
            for row, base in zip(rows, original, strict=True):
                assert {k: row[k] for k in base} == base
                step = int(row["step"])
                known = (pattern == "acquired" and step >= transition) or (
                    pattern == "lost" and step < transition
                )
                assert row["identity_known"] == str(int(known))
                assert row["observed_object_type"] == ("helicopter" if known else "unknown")
                assert "identity_label" not in row
            indexes = read(path / "training" / folder / pattern / "sequences.csv")
            assert len(indexes) == 3
            assert {r["split"] for r in indexes} == {episode.split}


def test_identity_transition_is_repeatable_and_window_includes_boundary(tmp_path):
    from data_generation.v1.identity_training import transition_steps

    path = variable_path(tmp_path, (101,))
    episodes = read_public_dataset(path / "public").episodes
    assert transition_steps(episodes, seed=17, margin_s=3) == transition_steps(
        episodes, seed=17, margin_s=3
    )
    config = {**settings(20), "identity": {"transition_margin_s": 3.0}}
    write_sequence_index(path, episodes, config, seed=17, object_type="vtol")
    switch = int(read(path / "training" / "identity_schedule.csv")[0]["transition_step"])
    rows = read(path / "training" / "diagnostic" / "acquired" / "sequences.csv")
    assert any(int(r["start_step"]) < switch <= int(r["start_step"]) + 15 for r in rows)
    with pytest.raises(ValueError):
        transition_steps(episodes, seed=17, margin_s=float("nan"))
