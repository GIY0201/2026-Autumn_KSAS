"""Saved completed datasets remain selectable after a service restart."""

import csv
import shutil

from data_generation.v1.service import GenerationService
from visualization.v1.app import create_app
from visualization.v1.tests.test_app import _component_by_id, _dataset_path


def test_startup_discovers_saved_results_without_loading_their_observations(tmp_path, monkeypatch):
    first = _dataset_path(tmp_path)
    second = _dataset_path(tmp_path)
    root = first.parent
    for name, kind, status in (
        ("analysis", "analysis", "complete"),
        ("failed", "data_generation", "failed"),
        ("in_progress", "data_generation", "writing"),
    ):
        target = root / name
        shutil.copytree(second, target)
        manifest = target / "manifest.csv"
        with manifest.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0].update(kind=kind, status=status)
        with manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
    incomplete = root / "incomplete"
    shutil.copytree(second, incomplete)
    (incomplete / "public" / "observations.csv").unlink()
    damaged = root / "damaged"
    shutil.copytree(second, damaged)
    (damaged / "public" / "episodes.csv").write_text(
        "episode_id,identity_label\ntruncated-row\n", encoding="utf-8"
    )

    def forbid_eager_load(*args, **kwargs):
        raise AssertionError("discovery must not load trajectory arrays")

    monkeypatch.setattr("visualization.v1.generation_ui.load_viewer_dataset", forbid_eager_load)
    service = GenerationService(output_root=root.parent.parent)
    app = create_app(first, generation_service=service)
    options = _component_by_id(app.layout, "dataset-select").options
    assert {item["value"] for item in options} == {first.name, second.name}
    label = next(item["label"] for item in options if item["value"] == second.name)
    assert "X8" in label and "1개" in label and "seed 19" in label
    assert _component_by_id(app.layout, "dataset-select").value == first.name
