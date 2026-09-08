"""Read saved validation diagnostics without inference or dataset access."""

import csv
import json

import pytest

from models.runtime.v1.service import TrainingService


@pytest.fixture
def diagnostic_service(tmp_path):
    service = TrainingService(tmp_path)
    run = tmp_path / "outputs/models/gru/v1/fixture"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": "fixture"}))
    folder = run / "diagnostics/best/validation"
    folder.mkdir(parents=True)
    rows = [
        dict(sequence_id="window_a", episode_id="episode", observed_vertical="up",
             observed_xy_movement="moving", axis_rmse_z_m=3, ade_m=9,
             anchor_t_s=3, generator_transitions="[]"),
        dict(sequence_id="window_b", episode_id="episode", observed_vertical="down",
             observed_xy_movement="small", axis_rmse_z_m=8, ade_m=4,
             anchor_t_s=18, generator_transitions="[]"),
    ]
    for name, records in (("windows.csv", rows), ("points.csv", [
        dict(sequence_id="window_a", sample_role="input", sample_index=0,
             t_s=0, observed_z_m=1, predicted_z_m="", generator_event_ids="[]"),
        dict(sequence_id="window_b", sample_role="future", sample_index=0,
             t_s=18.2, observed_z_m=2, predicted_z_m=3, generator_event_ids='["climb"]'),
    ])):
        with (folder / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    (folder / "provenance.json").write_text(json.dumps({"label_source": "observed"}))
    return service


def test_saved_diagnostics_sort_filter_and_point_lookup(diagnostic_service):
    result = diagnostic_service.diagnostic_windows("fixture", "best")
    assert result["available"]
    assert [r["sequence_id"] for r in result["windows"]] == ["window_b", "window_a"]
    by_ade = diagnostic_service.diagnostic_windows("fixture", "best", sort_by="ade_m")
    assert by_ade["windows"][0]["sequence_id"] == "window_a"
    filtered = diagnostic_service.diagnostic_windows("fixture", "best", vertical="up")
    assert len(filtered["windows"]) == 1
    window = diagnostic_service.diagnostic_window("fixture", "best", "window_b")
    assert len(window["points"]) == 1
    assert window["points"][0]["predicted_z_m"] == 3.0
    assert window["points"][0]["generator_event_ids"] == ["climb"]
    assert window["provenance"]["label_source"] == "observed"


def test_old_run_has_no_fabricated_diagnostics(diagnostic_service):
    result = diagnostic_service.diagnostic_windows("fixture", "last")
    assert result["available"] is False
    assert result["windows"] == []


@pytest.mark.parametrize("selector", ["../best", "best/../../", "latest"])
def test_checkpoint_selector_rejected(diagnostic_service, selector):
    with pytest.raises(ValueError):
        diagnostic_service.diagnostic_windows("fixture", selector)


@pytest.mark.parametrize("selector", ["../window_a", "a/b", "", "unknown"])
def test_window_selector_rejected(diagnostic_service, selector):
    with pytest.raises(ValueError):
        diagnostic_service.diagnostic_window("fixture", "best", selector)
