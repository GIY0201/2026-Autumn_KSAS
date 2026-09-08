"""Position diagnostics keep generator annotations out of predictor inputs."""

import csv
import json
from types import SimpleNamespace

import numpy as np

from models.gru.v1.tests.test_direct import make_corpus


def test_subset_covers_six_observed_groups_and_is_order_independent():
    from models.gru.v1.diagnostics import select_indices

    targets, rows = [], []
    for xy in (0, 30):
        for dz in (-30, 0, 30):
            for sample in range(3):
                target = np.zeros((75, 3))
                target[:, 0] = xy
                target[:, 2] = dz
                targets.append(target)
                rows.append({"sequence_id": f"{xy}_{dz}_{sample}"})
    data = SimpleNamespace(targets_m=np.asarray(targets), window_rows=rows)
    chosen = select_indices(data)
    assert len(chosen) == 12
    assert all(not rows[index]["sequence_id"].endswith("_2") for index in chosen)
    reversed_data = SimpleNamespace(targets_m=data.targets_m[::-1], window_rows=rows[::-1])
    assert {rows[index]["sequence_id"] for index in chosen} == {
        reversed_data.window_rows[index]["sequence_id"] for index in select_indices(reversed_data)
    }


def test_diagnostic_roundtrip_and_halfopen_transition(tmp_path):
    from models.gru.v1.data import prepare_data
    from models.gru.v1.diagnostics import select_indices, write_diagnostics

    make_corpus(tmp_path)
    prepared = prepare_data(tmp_path, {}, tmp_path / "run")
    data = prepared.validation
    features = data.input_features.copy()
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation/commands.csv").write_text(
        "episode_id,event_id,start_s,end_s,trigger_kind,completed,completion_reason\n"
        "validation,rise,0,4,flight,1,done\nvalidation,turn,4,20,flight,1,done\n"
        "test,sealed,0,20,flight,1,done\n",
        encoding="utf-8",
    )
    assert set(select_indices(data)) == {0, 1}
    assert select_indices(data) == select_indices(data)
    out = tmp_path / "detail"
    receipt = write_diagnostics(
        out,
        data,
        data.labels,
        prepared.normalization["scale_m"],
        run_id="fixture",
        checkpoint="best",
        epoch=1,
        dataset_path=tmp_path,
    )
    assert receipt["status"] == "completed"
    with (out / "points.csv").open(encoding="utf-8", newline="") as f:
        points = list(csv.DictReader(f))
    assert len(points) == len(data) * 91
    first = [r for r in points if r["sequence_id"] == data.window_rows[0]["sequence_id"]]
    assert [float(r["t_s"]) for r in first[:16]] == data.timestamps_s[0].tolist()
    assert first[0]["predicted_x_m"] == ""
    future = first[16:]
    np.testing.assert_allclose(
        [float(r["observed_z_m"]) for r in future], data.targets_m[0, :, 2] + data.anchors[0, 2]
    )
    assert max(float(r["error_3d_m"]) for r in future) < 1e-4
    boundary = next(r for r in first if abs(float(r["t_s"]) - 4) < 1e-6)
    assert json.loads(boundary["generator_event_ids"]) == ["turn"]
    assert "sealed" not in (out / "points.csv").read_text(encoding="utf-8")
    np.testing.assert_array_equal(features, data.input_features)


def test_missing_commands_are_unknown(tmp_path):
    from models.gru.v1.data import prepare_data
    from models.gru.v1.diagnostics import write_diagnostics

    make_corpus(tmp_path)
    prepared = prepare_data(tmp_path, {}, tmp_path / "run")
    out = tmp_path / "detail"
    write_diagnostics(
        out,
        prepared.validation,
        prepared.validation.labels,
        prepared.normalization["scale_m"],
        run_id="fixture",
        checkpoint="best",
        epoch=1,
        dataset_path=tmp_path,
    )
    assert json.loads((out / "provenance.json").read_text())["commands_status"] == "unavailable"
