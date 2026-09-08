"""Run identity, registry and immutable preset behavior."""

import pytest

from models.runtime.v1.registry import Registry
from models.runtime.v1.service import TrainingService


def test_registry_rejects_unknown_and_duplicate():
    registry = Registry()
    registry.register({"id": "fixture"})
    with pytest.raises(ValueError):
        registry.register({"id": "fixture"})
    with pytest.raises(ValueError):
        registry.get("missing")


def test_run_paths_cannot_escape(tmp_path):
    service = TrainingService(tmp_path)
    with pytest.raises(ValueError):
        service.status("../outside")


def test_named_presets_are_immutable_revisions(tmp_path):
    service = TrainingService(tmp_path)
    config = service.plugins()[0]["defaults"]
    first = service.save_preset("실험 설정", config)
    second = service.save_preset("실험 설정", config)
    assert first != second
    presets = service.presets(config["plugin_id"])
    assert len(presets) == 2
    assert all(p["name"] == "실험 설정" for p in presets)


def test_tensorboard_scalars_are_visible(tmp_path):
    from torch.utils.tensorboard import SummaryWriter

    from models.runtime.v1.io import atomic_json

    directory = tmp_path / "outputs/models/gru/v1/example"
    directory.mkdir(parents=True)
    atomic_json(directory / "run.json", {"run_id": "example"})
    with SummaryWriter(str(directory / "tensorboard")) as writer:
        writer.add_scalar("eval/ade_m", 12.5, 4)
        writer.add_scalar("eval/epoch", 1.0, 4)
    rows = TrainingService(tmp_path).metrics("example")
    assert any(row.get("eval_ade_m") == 12.5 and row["global_step"] == 4 for row in rows)
