"""Sequence extraction preserves parent splits and never consumes evaluation files."""

import csv

import numpy as np
import pytest

from contracts.v1.types import EpisodeMetadata
from data_generation.v1 import dataset as writer
from data_generation.v1.records import MotionEpisode, MotionEvent
from data_generation.v1.tests.test_multi_object_dataset import _generic_request
from visualization.v1.tests.test_variable_playback import variable_path


def api():
    from importlib.util import find_spec

    assert find_spec("data_generation.v1.training_data"), "training preparation is missing"
    from data_generation.v1 import training_data

    return training_data


def settings(window=None):
    return {
        "window_samples": window,
        "train_windows_per_episode": 3,
        "evaluation_stride_samples": 10,
    }


def test_whole_sequences_and_variants_inherit_parent_split():
    module = api()
    metadata = [
        EpisodeMetadata("a", "train", 3.8, 20, 0.2, "object"),
        EpisodeMetadata("b", "test", 5.8, 30, 0.2, "object"),
    ]
    rows = module.sequence_rows(metadata, settings(), seed=17)
    assert len(rows) == 6
    assert {r["split"] for r in rows if r["episode_id"] == "a"} == {"train"}
    assert {r["end_step"] for r in rows if r["episode_id"] == "b"} == {29}
    assert not {"seed", "scenario_id", "vx_mps", "truth"} & set(rows[0])


def test_window_starts_reproducible_train_random_evaluation_fixed():
    module = api()
    metadata = [
        EpisodeMetadata("a", "train", 19.8, 100, 0.2, "object"),
        EpisodeMetadata("b", "validation", 19.8, 100, 0.2, "object"),
    ]
    one = module.sequence_rows(metadata, settings(20), seed=17)
    assert one == module.sequence_rows(metadata, settings(20), seed=17)
    two = module.sequence_rows(metadata, settings(20), seed=18)
    assert [r for r in one if r["split"] == "validation"] == [
        r for r in two if r["split"] == "validation"
    ]
    assert [r["start_step"] for r in one if r["episode_id"] == "a"] != [
        r["start_step"] for r in two if r["episode_id"] == "a"
    ]
    assert all(r["end_step"] - r["start_step"] == 19 for r in one)
    with pytest.raises(ValueError, match="window"):
        module.sequence_rows(metadata, settings(101), seed=17)


def test_reader_only_public_and_rejects_cross_split_index(tmp_path):
    module = api()
    path = variable_path(tmp_path, (101,))
    from contracts.v1.csv_io import read_public_dataset

    public = read_public_dataset(path / "public")
    # The fixture request is diagnostic; retain its actual split for valid extraction.
    module.write_sequence_index(path, public.episodes, settings(20), seed=17)
    (path / "evaluation" / "truth.csv").unlink()
    sequences = list(module.iter_training_sequences(path, split="diagnostic"))
    assert sequences
    assert all(len(records) == 20 for _, records in sequences)
    assert all(
        record.variant_id == info["variant_id"] for info, records in sequences for record in records
    )
    index_path = path / "training" / "sequences.csv"
    with index_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["split"] = "train"
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        changed = csv.DictWriter(handle, fieldnames=module.INDEX_COLUMNS)
        changed.writeheader()
        changed.writerows(rows)
    with pytest.raises(ValueError, match="split"):
        list(module.iter_training_sequences(path, split="train"))


def test_writer_includes_training_artifacts_in_inventory(tmp_path):
    module = api()
    from data_generation.v1.records import MotionEpisode
    from visualization.v1.tests.test_app import _episode

    episode = _episode()
    motion = MotionEpisode(
        episode_id=episode.episode_id,
        status="complete",
        failure_reason=None,
        seed=1,
        steps=episode.steps,
        t_s=episode.t_s,
        position_enu_m=episode.position_enu_m,
        velocity_enu_mps=episode.velocity_enu_mps,
        diagnostic_arrays={},
        event_records=(),
    )
    result = writer.write_dataset(
        [motion], request=_generic_request(), output_root=tmp_path, training_config=settings()
    )
    with (result.path / "files.csv").open(encoding="utf-8") as handle:
        inventory = list(csv.DictReader(handle))
    assert any(row["relative_path"] == "training/sequences.csv" for row in inventory)
    assert {f"training/{name}/observations.csv" for name in ("train", "valid", "test")} <= {
        row["relative_path"] for row in inventory
    }
    with (result.path / "training" / "diagnostic" / "observations.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == len(motion.steps) * 3
    assert {row["episode_id"] for row in exported} == {motion.episode_id}
    assert not (result.path / "training" / "train" / "truth.csv").exists()
    assert len(list(module.iter_training_sequences(result.path, split="diagnostic"))) == 3


def _core_episode(episode_id, action, direction=None):
    steps = np.arange(301)
    events = []
    if direction is not None:
        events.append(
            MotionEvent(
                f"turn_direction:{direction}", 0, 0, "schedule_control", True, "explicit"
            )
        )
    events.extend(
        (
            MotionEvent(action, 10, 30, "full_flight", True, "complete"),
            MotionEvent(action, 30, 50, "full_flight", True, "complete"),
        )
    )
    return MotionEpisode(
        episode_id,
        "complete",
        None,
        1,
        steps,
        steps * 0.2,
        np.zeros((301, 3)),
        np.zeros((301, 3)),
        {},
        tuple(events),
    )


def test_balanced_windows_equalize_core_actions_and_turn_directions():
    module = api()
    actions = (
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
    turning = {action for action in actions if "straight" not in action}
    records = []
    for action in actions:
        # Straight actions can belong to an Episode whose other phases turn.
        directions = ("left", "right") if action in turning else ("left",)
        for direction in directions:
            records.append(_core_episode(f"{action}-{direction}", action, direction))
    metadata = [
        EpisodeMetadata(record.episode_id, "train", 60, 301, 0.2, "VTOL")
        for record in records
    ]
    config = {
        "window_samples": 91,
        "train_windows_per_episode": 8,
        "evaluation_stride_samples": 75,
        "behavior_balance": {
            "schema": "behavior-window-balance-v1",
            "core_windows_per_behavior": 4,
            "transition_windows_per_boundary": 1,
        },
    }
    rows, audit = module.balanced_sequence_rows(metadata, records, config, seed=17)
    sigma_1m = [row for row in rows if row["variant_id"] == "sigma_1m"]
    assert len(sigma_1m) == len(actions) * 4 + 2 * len(records)
    core_starts = {
        (row["episode_id"], row["start_step"])
        for row in sigma_1m
        if 50 <= row["start_step"] <= 159
    }
    assert len(core_starts) == len(actions) * 4
    by_action = {row["behavior"]: row for row in audit if row["window_role"] == "core"}
    assert set(by_action) == set(actions)
    assert {row["core_windows"] for row in by_action.values()} == {4}
    assert {row["target_points"] for row in by_action.values()} == {300}
    for action in turning:
        assert by_action[action]["left_windows"] == 2
        assert by_action[action]["right_windows"] == 2
        assert by_action[action]["left_episodes"] == 1
        assert by_action[action]["right_episodes"] == 1
    for action in set(actions) - turning:
        assert by_action[action]["left_episodes"] == 0
        assert by_action[action]["right_episodes"] == 0
    transition = [row for row in audit if row["window_role"] == "transition"]
    assert len(transition) == 1
    assert transition[0]["core_windows"] == 2 * len(records)
    assert rows == module.balanced_sequence_rows(metadata, records, config, seed=17)[0]
