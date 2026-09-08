"""Behavioral tests for isolated training and checkpoint lifecycle."""

import json
from types import SimpleNamespace

import numpy as np
import pytest


def test_constant_velocity_uses_all_observed_points():
    from models.runtime.v1.worker import constant_velocity

    times = np.arange(16)[None, :] * 0.2
    velocity = np.array([2.0, -1.0, 0.5])
    positions = times[..., None] * velocity
    result = constant_velocity(positions, times, np.arange(1, 76) * 0.2)
    np.testing.assert_allclose(result[0], np.arange(1, 76)[:, None] * 0.2 * velocity)


def test_failed_worker_writes_failure_receipt(tmp_path):
    from models.runtime.v1.worker import run_worker

    assert run_worker(tmp_path) == 1
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "failed"
    assert not (tmp_path / "success.json").exists()


def test_atomic_checkpoint_requires_full_resume_state(tmp_path):
    from models.runtime.v1.worker import publish_checkpoint

    source = tmp_path / "source"
    source.mkdir()
    (source / "model.safetensors").write_bytes(b"model")
    with pytest.raises(ValueError, match="resume"):
        publish_checkpoint(source, tmp_path / "last")
    for filename in ("optimizer.pt", "scheduler.pt", "rng_state.pth", "trainer_state.json"):
        (source / filename).write_text("{}")
    publish_checkpoint(source, tmp_path / "last")
    assert (tmp_path / "last" / "rng_state.pth").exists()


def test_resume_rejects_changed_validation_observations():
    from models.runtime.v1.worker import validate_resume_sources

    original = {"train": "train_hash", "validation": "validation_hash", "test": "sealed"}
    validate_resume_sources(original, {"train": "train_hash", "validation": "validation_hash"})
    with pytest.raises(ValueError, match="validation"):
        validate_resume_sources(original, {"train": "train_hash", "validation": "modified"})


def test_unknown_evaluation_mode_rejected_before_data_loading(tmp_path, monkeypatch):
    from models.runtime.v1 import registry, worker

    def forbidden_plugin(name):
        raise AssertionError("Data/model plugin accessed before evaluation mode validation")

    monkeypatch.setattr(registry, "get_plugin", forbidden_plugin)
    with pytest.raises(ValueError, match="evaluation mode"):
        worker.train(tmp_path, {"training": {}, "evaluation": {"mode": "typo"}}, {})


def test_real_validation_only_training_without_test_observations(tmp_path):
    from omegaconf import OmegaConf

    from models.gru.v1.model import resolve_config
    from models.gru.v1.tests.test_direct import make_corpus
    from models.runtime.v1.worker import run_worker

    make_corpus(tmp_path)
    assert not (tmp_path / "training/test/observations.csv").exists()
    config = resolve_config(
        {
            "evaluation": {"mode": "validation_only"},
            "data": {"dataset_path": str(tmp_path), "train_windows_per_episode": 2},
            "training": {"device": "cpu", "max_epochs": 1, "batch_size": 2},
        }
    )
    initializations = []
    for index in range(2):
        run = tmp_path / f"real_run_{index}"
        run.mkdir()
        OmegaConf.save(OmegaConf.create(config), run / "effective_config.yaml")
        (run / "run.json").write_text(
            json.dumps(
                {
                    "dataset_path": str(tmp_path),
                    "run_id": run.name,
                }
            )
        )
        assert run_worker(run) == 0
        initializations.append(json.loads((run / "initialization.json").read_text()))
        sources = json.loads((run / "data_sources.json").read_text())
        assert set(sources) == {"train", "validation"}
        logs = [json.loads(line) for line in (run / "logs/metrics.jsonl").read_text().splitlines()]
        for prefix in ("train_eval", "eval"):
            row = next(row for row in logs if f"{prefix}_huber_x" in row)
            for metric in (
                "huber_x",
                "huber_y",
                "huber_z",
                "axis_rmse_x_m",
                "axis_rmse_y_m",
                "axis_rmse_z_m",
                "ade_m",
                "fde_m",
            ):
                assert np.isfinite(row[f"{prefix}_{metric}"])
        assert json.loads((run / "success.json").read_text())["test_evaluated"] is False
        assert (run / "diagnostics/subset_index.csv").is_file()
        for checkpoint in ("best", "last"):
            receipt = json.loads(
                (run / "diagnostics" / checkpoint / "validation/provenance.json").read_text()
            )
            assert receipt["window_count"] == 2
            assert receipt["commands_status"] == "unavailable"
        assert not (run / "diagnostics/best/test").exists()
    assert initializations[0]["state_sha256"] == initializations[1]["state_sha256"]


@pytest.mark.parametrize("evaluation_mode", ["test_after_training", "validation_only"])
def test_trainer_stop_resume_matches_uninterrupted_run(tmp_path, monkeypatch, evaluation_mode):
    """Stop after step one, resume RNG/optimizer and match the uninterrupted weights."""
    import torch
    from omegaconf import OmegaConf
    from safetensors.torch import load_file

    from models.runtime.v1 import registry, worker

    class Synthetic(torch.utils.data.Dataset):
        def __init__(self):
            self.source_sha256 = "synthetic-fixture"
            self.timestamps_s = np.tile(np.arange(16) * 0.2, (8, 1))
            self.input_positions_m = np.zeros((8, 16, 3), dtype=np.float32)
            self.input_positions_m[:, :, 0] = self.timestamps_s
            self.anchors = self.input_positions_m[:, -1].copy()
            self.targets_m = np.zeros((8, 75, 3), dtype=np.float32)
            self.targets_m[:, :, 0] = np.arange(1, 76) * 0.2
            self.labels = self.targets_m / 15.0

        def __len__(self):
            return 8

        def __getitem__(self, index):
            features = np.column_stack(
                (
                    (self.input_positions_m[index] - self.anchors[index]) / 15,
                    (self.timestamps_s[index] - 3) / 3,
                )
            ).astype(np.float32)
            return {
                "input_features": torch.from_numpy(features),
                "labels": torch.from_numpy(self.labels[index]),
            }

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = torch.nn.GRU(4, 4, batch_first=True)
            self.dropout = torch.nn.Dropout(0.1)
            self.head = torch.nn.Linear(4, 225)

        def forward(self, input_features, labels=None):
            value = self.head(self.dropout(self.gru(input_features)[1][-1])).reshape(-1, 75, 3)
            return {"loss": torch.nn.functional.huber_loss(value, labels), "logits": value}

    def prepare(path, config, run_dir):
        (run_dir / "window_index.csv").write_text("identical\n")
        (run_dir / "normalization.json").write_text('{"scale_m": 15.0}')
        return SimpleNamespace(
            train=Synthetic(), validation=Synthetic(), normalization={"scale_m": 15.0}
        )

    test_calls = []

    def load_test(*args):
        if evaluation_mode == "validation_only":
            raise AssertionError("Sealed test loader accessed")
        test_calls.append(args[1])
        return Synthetic()

    plugin = {
        "factory": lambda config: Tiny(),
        "prepare_data": prepare,
        "load_split": load_test,
        "metrics": lambda pred, target, scale: {
            "ade_m": float(np.linalg.norm((pred - target) * scale, axis=-1).mean())
        },
    }
    monkeypatch.setattr(registry, "get_plugin", lambda name: plugin)
    config = {
        "evaluation": {"mode": evaluation_mode},
        "plugin_id": "synthetic",
        "model": {},
        "data": {"dataset_path": str(tmp_path)},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "seed": 17,
            "batch_size": 4,
            "max_epochs": 3,
            "learning_rate": 0.001,
            "weight_decay": 0.0001,
            "scheduler": "cosine",
            "warmup_ratio": 0.05,
            "max_grad_norm": 1.0,
            "early_stopping_patience": 20,
        },
    }

    def make_run(name, parent=None):
        path = tmp_path / name
        path.mkdir()
        OmegaConf.save(OmegaConf.create(config), path / "effective_config.yaml")
        meta = {"run_id": name, "display_name": name, "dataset_path": str(tmp_path)}
        if parent:
            meta.update(
                parent_run=str(parent), parent_checkpoint=str(parent / "checkpoints" / "last")
            )
        (path / "run.json").write_text(json.dumps(meta))
        return path

    uninterrupted = make_run("uninterrupted")
    assert worker.run_worker(uninterrupted) == 0
    receipt = json.loads((uninterrupted / "success.json").read_text())
    assert receipt["evaluation_mode"] == evaluation_mode
    assert receipt["test_evaluated"] == (evaluation_mode == "test_after_training")
    initialization = json.loads((uninterrupted / "initialization.json").read_text())
    assert len(initialization["state_sha256"]) == 64
    logs = [
        json.loads(line) for line in (uninterrupted / "logs/metrics.jsonl").read_text().splitlines()
    ]
    for prefix in ("train_eval", "eval"):
        rows = [row for row in logs if f"{prefix}_huber_x" in row]
        assert {round(row["epoch"]) for row in rows} == {1, 2, 3}
        assert all(f"{prefix}_huber_z" in row for row in rows)
    expected_calls = int(evaluation_mode == "test_after_training")
    if not expected_calls:
        protocol = json.loads((uninterrupted / "evaluation/protocol.json").read_text())
        assert protocol["test_passes"] == 0
        assert protocol["test_evaluated"] is False
        assert not (uninterrupted / "evaluation/test_evaluation_started.json").exists()
    parent = make_run("parent")
    (parent / "stop.request").touch()
    assert worker.run_worker(parent) == 0
    assert json.loads((parent / "status.json").read_text())["status"] == "stopped"
    assert len(test_calls) == expected_calls
    child = make_run("child", parent)
    assert worker.run_worker(child) == 0
    assert len(test_calls) == 2 * expected_calls
    assert (child / "success.json").exists()
    for key, value in load_file(str(uninterrupted / "checkpoints/last/model.safetensors")).items():
        torch.testing.assert_close(
            value,
            load_file(str(child / "checkpoints/last/model.safetensors"))[key],
            rtol=0,
            atol=1e-7,
        )
    config["training"]["max_epochs"] = 4
    extended = make_run("extended", child)
    assert worker.run_worker(extended) == 0
    schedule = json.loads((extended / "schedule.json").read_text())
    assert schedule["warm_restart"] is True
    assert schedule["segment_start"] == 6
    assert schedule["total_steps"] == 8
    assert len(test_calls) == 3 * expected_calls
    extended_weights = load_file(str(extended / "checkpoints/last/model.safetensors"))
    child_weights = load_file(str(child / "checkpoints/last/model.safetensors"))
    assert any(
        not torch.equal(value, child_weights[key]) for key, value in extended_weights.items()
    )
