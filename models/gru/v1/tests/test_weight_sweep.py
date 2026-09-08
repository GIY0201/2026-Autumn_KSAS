import copy

import pytest

from models.gru.v1.weight_sweep import build_config, summarize


def test_configs_change_only_weight_initialization_and_fixed_window_seed():
    reference = {
        "model": {"axis_loss_weights": [1, 1, 1]},
        "training": {"seed": 17, "max_epochs": 200},
        "data": {},
        "evaluation": {"mode": "test_after_training"},
    }
    untouched = copy.deepcopy(reference)
    actual = build_config(reference, 3, 43, 17)
    assert actual["model"]["axis_loss_weights"] == [1, 1, 3]
    assert actual["training"] == {"seed": 43, "max_epochs": 200}
    assert actual["data"]["window_seed"] == 17
    assert actual["evaluation"]["mode"] == "validation_only"
    assert reference == untouched


def test_selection_uses_best_equal_seed_means_and_lower_weight_tie():
    rows = [
        {
            "z_weight": weight,
            "seed": seed,
            "checkpoint": checkpoint,
            "ade_m": value,
            "fde_m": value + 1,
            "axis_rmse_x_m": value,
            "axis_rmse_y_m": value,
            "axis_rmse_z_m": value,
        }
        for weight, seed, checkpoint, value in [
            (1, 17, "best", 2),
            (1, 29, "best", 4),
            (2, 17, "best", 1),
            (2, 29, "best", 5),
            (1, 17, "last", 100),
            (1, 29, "last", 100),
            (2, 17, "last", 0),
            (2, 29, "last", 0),
        ]
    ]
    summary, selected = summarize(rows, [1, 2], [17, 29])
    assert selected == 1
    assert summary[0]["ade_m_mean"] == 3
    assert summary[0]["ade_m_std"] == pytest.approx(2**0.5)
    with pytest.raises(ValueError, match="complete"):
        summarize(rows[:-1], [1, 2], [17, 29])


def test_summary_rejects_duplicate_or_nonfinite_measurements():
    row = {"z_weight": 1, "seed": 17, "checkpoint": "best", "ade_m": float("nan")}
    with pytest.raises(ValueError):
        summarize([row], [1], [17])


def test_sweep_runs_fresh_complete_grid_with_test_disabled(tmp_path, monkeypatch):
    import json

    from omegaconf import OmegaConf

    from models.gru.v1 import weight_sweep as sweep
    from models.gru.v1.model import resolve_config

    reference = tmp_path / "reference"
    reference.mkdir()
    for name in ("window_index.csv", "normalization.json", "manifest.csv"):
        (reference / name).write_text("reference")
    (reference / "data_sources.json").write_text(json.dumps({"train": "a", "validation": "b"}))
    OmegaConf.save(OmegaConf.create(resolve_config({})), reference / "effective_config.yaml")
    started = []

    class FakeService:
        def __init__(self, root):
            self.root = root
            self._processes = {}

        def start(self, config, display_name):
            assert config["evaluation"]["mode"] == "validation_only"
            assert config["data"]["window_seed"] == 17
            started.append(config)
            run_id = f"run_{len(started)}"
            from types import SimpleNamespace

            self._processes[run_id] = SimpleNamespace(wait=lambda timeout: 0)
            path = self.root / run_id
            path.mkdir()
            for name in ("window_index.csv", "normalization.json", "data_sources.json"):
                (path / name).write_bytes((reference / name).read_bytes())
            (path / "success.json").write_text(json.dumps({"test_evaluated": False}))
            (path / "initialization.json").write_text(
                json.dumps({"state_sha256": str(config["training"]["seed"])})
            )
            return run_id

        def _run_path(self, run_id):
            return self.root / run_id

        def status(self, run_id):
            return {"status": "completed"}

    def evaluate(directory, config, sources):
        weight = config["model"]["axis_loss_weights"][2]
        rows = [
            dict(
                z_weight=weight,
                seed=config["training"]["seed"],
                checkpoint=checkpoint,
                **{key: float(weight) for key in sweep.METRICS},
            )
            for checkpoint in ("best", "last")
        ]
        return rows, [{"run_id": directory.name, "phase": "level"}]

    monkeypatch.setattr(sweep, "TrainingService", FakeService)
    monkeypatch.setattr(sweep, "verify_manifest", lambda path: None)
    monkeypatch.setattr(sweep, "evaluate_run", evaluate)
    output = sweep.run_sweep(tmp_path, reference)
    assert len(started) == 12
    receipt = json.loads((output / "success.json").read_text())
    assert receipt["selected_z_weight"] == 1
    assert receipt["test_evaluated"] is False
    assert (output / "manifest.csv").is_file()


def test_replay_requires_matching_worker_validation_ade(tmp_path):
    import json

    from models.gru.v1.weight_sweep import validate_logged_ade

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/metrics.jsonl").write_text(
        json.dumps({"global_step": 44, "eval_ade_m": 32.0}) + "\n"
    )
    assert validate_logged_ade(tmp_path, 44, 32.00001) == 32.0
    with pytest.raises(ValueError, match="mismatch"):
        validate_logged_ade(tmp_path, 44, 35)
    with pytest.raises(ValueError, match="Missing"):
        validate_logged_ade(tmp_path, 88, 32)
