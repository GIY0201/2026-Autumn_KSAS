import numpy as np
import pytest

from contracts.v1.simulation import validate_public_batch
from models.runtime.v1.simulation_inference import PredictionEvaluator


def test_public_rejects_truth_and_invalid_history():
    batch = dict(
        position_enu_m=np.zeros((1, 16, 3)),
        timestamp_s=np.arange(16)[None, :] * 0.2,
        valid_mask=np.ones((1, 16), bool),
    )
    assert validate_public_batch(batch)["position_enu_m"].shape == (1, 16, 3)
    with pytest.raises(ValueError):
        validate_public_batch(dict(batch, truth=np.zeros(3)))
    batch["valid_mask"][0, 0] = False
    with pytest.raises(ValueError):
        validate_public_batch(batch)


def test_evaluator_late_arrival_and_maturity():
    evaluator = PredictionEvaluator()
    horizons = np.arange(1, 76) * 0.2
    for t in horizons[:5]:
        evaluator.observe("a", float(t), [0, 0, 0], [1, 0, 0])
    evaluator.add_prediction("a", 0.0, "cruise", np.zeros((75, 3)), horizons)
    result = evaluator.summary()
    assert result["overall"]["truth"]["count"] == 0
    assert result["overall"]["truth"]["horizons"]["1"]["count"] == 1
    for t in horizons[5:]:
        evaluator.observe("a", float(t), [0, 0, 0], [1, 0, 0])
    result = evaluator.finalize()
    assert result["overall"]["truth"]["ade_m"] == 0
    assert result["overall"]["observed"]["fde_m"] == 1
    assert result["incomplete_predictions"] == 0


def test_summary_uses_mature_aggregates_not_history_rescans(monkeypatch):
    evaluator = PredictionEvaluator()
    horizons = np.arange(1, 76) * 0.2
    evaluator.add_prediction("a", 0, "cruise", np.zeros((75, 3)), horizons)
    for t in horizons:
        evaluator.observe("a", float(t), [0, 0, 0], [1, 0, 0])

    def forbidden(*args, **kwargs):
        raise AssertionError("Summary must not recompute trajectory distances")

    monkeypatch.setattr(np.linalg, "norm", forbidden)
    for _ in range(10):
        assert evaluator.summary()["overall"]["observed"]["ade_m"] == 1


def test_evaluator_duplicate_observation_does_not_double_count():
    evaluator = PredictionEvaluator()
    h = np.arange(1, 76) * 0.2
    evaluator.add_prediction("a", 0, "ground", np.zeros((75, 3)), h)
    for _ in range(2):
        for t in h:
            evaluator.observe("a", float(t), [0, 0, 0], [1, 0, 0])
    assert evaluator.summary()["overall"]["truth"]["count"] == 1


def test_checkpoint_integrity_and_loader(tmp_path):
    import json

    from omegaconf import OmegaConf
    from safetensors.torch import save_file

    from models.gru.v1.plugin import get_plugin
    from models.runtime.v1.io import write_manifest
    from models.runtime.v1.simulation_inference import checkpoint_catalog, load_predictor

    plugin = get_plugin()
    root = tmp_path / "outputs/models/gru/v1/example"
    checkpoint = root / "checkpoints/best"
    checkpoint.mkdir(parents=True)
    config = plugin["defaults"]
    config["model"].update(hidden_size=4, head_hidden_size=4, num_layers=1)
    (root / "effective_config.yaml").write_text(OmegaConf.to_yaml(config))
    (root / "normalization.json").write_text(json.dumps(dict(scale_m=10.0)))
    (root / "run.json").write_text(json.dumps(dict(run_id="example")))
    (root / "status.json").write_text(json.dumps(dict(status="completed")))
    save_file(plugin["factory"](config).state_dict(), str(checkpoint / "model.safetensors"))
    write_manifest(root)
    catalog = checkpoint_catalog(tmp_path)
    assert len(catalog) == 1
    predictor = load_predictor(tmp_path, catalog[0])
    result = predictor.predict(
        dict(
            position_enu_m=np.zeros((1, 16, 3)),
            timestamp_s=np.arange(16)[None, :] * 0.2,
            valid_mask=np.ones((1, 16), bool),
        )
    )
    assert result["position_enu_m"].shape == (1, 75, 3)
    import time

    from models.runtime.v1.simulation_inference import AsyncPredictor

    worker = AsyncPredictor(tmp_path, catalog[0])
    try:
        worker.submit(
            dict(
                position_enu_m=np.zeros((1, 16, 3)),
                timestamp_s=np.arange(16)[None, :] * 0.2,
                valid_mask=np.ones((1, 16), bool),
            ),
            {"object_ids": ["a"]},
        )
        deadline = time.monotonic() + 20
        received = []
        while not received and time.monotonic() < deadline:
            received = [item for item in worker.poll() if not item.get("ready")]
            time.sleep(0.01)
        assert received and "error" not in received[0]
        assert received[0]["metadata"]["object_ids"] == ["a"]
    finally:
        worker.close()
    (root / "normalization.json").write_text('{"scale_m": 1}')
    assert checkpoint_catalog(tmp_path) == []
    with pytest.raises(ValueError, match="verification"):
        load_predictor(tmp_path, catalog[0])
