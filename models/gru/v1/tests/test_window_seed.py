import csv
import hashlib

import pytest

from models.gru.v1.data import build_window_rows
from models.gru.v1.tests.test_direct import make_corpus


def test_explicit_window_seed_freezes_data_across_model_seeds(tmp_path):
    make_corpus(tmp_path)
    first = build_window_rows(tmp_path, {"data": {"window_seed": 17}, "training": {"seed": 17}})
    second = build_window_rows(tmp_path, {"data": {"window_seed": 17}, "training": {"seed": 29}})
    assert first == second
    assert first == build_window_rows(tmp_path, {"training": {"seed": 17}})


def test_balanced_dataset_index_is_required_and_used_without_evaluation_labels(tmp_path):
    make_corpus(tmp_path)
    (tmp_path / "training/settings.yaml").write_text(
        "window_samples: 91\nbehavior_balance:\n  schema: behavior-window-balance-v1\n"
    )
    fields = ("sequence_id", "episode_id", "variant_id", "split", "start_step", "end_step")
    rows = []
    for episode, split, start in (("train", "train", 5), ("validation", "validation", 0)):
        end = start + 90
        key = f"{episode}/sigma_1m/{start}/{end}"
        rows.append(
            dict(
                sequence_id=hashlib.sha256(key.encode()).hexdigest(),
                episode_id=episode,
                variant_id="sigma_1m",
                split=split,
                start_step=start,
                end_step=end,
            )
        )
    with (tmp_path / "training/balanced_windows.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    index_path = tmp_path / "training/balanced_windows.csv"
    digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    with (tmp_path / "files.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("relative_path", "sha256", "size_bytes"))
        writer.writeheader()
        writer.writerow(
            {
                "relative_path": "training/balanced_windows.csv",
                "sha256": digest,
                "size_bytes": index_path.stat().st_size,
            }
        )
    selected = build_window_rows(tmp_path, {})
    assert [(row["episode_id"], row["start_step"]) for row in selected] == [
        ("train", 5),
        ("validation", 0),
    ]
    (tmp_path / "training/settings.yaml").unlink()
    assert build_window_rows(tmp_path, {}) == selected
    index_path.write_text(index_path.read_text() + "\n")
    with pytest.raises(ValueError, match="hash"):
        build_window_rows(tmp_path, {})
