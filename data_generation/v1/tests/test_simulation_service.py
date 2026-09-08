import json

from data_generation.v1.simulation_service import SimulationService


class Engine:
    def __init__(self, config):
        self.config = config
        self.events = []
        self.status = "ready"

    def snapshot(self):
        return {"status": self.status, "time_s": 0, "step": 0, "objects": []}

    def start(self):
        self.status = "running"

    def pause(self):
        self.status = "paused"

    def resume(self):
        self.status = "running"

    def stop(self, reason="user"):
        self.status = "stopped"

    def drain_observations(self):
        return []

    def set_keys(self, keys):
        pass


def test_create_stop_persists_distinct_run_and_receipt(tmp_path):
    service = SimulationService(tmp_path, engine_factory=Engine, background=False)
    first = service.create({"seed": 4})
    service.command("start")
    service.command("stop")
    dataset = tmp_path / "outputs/data_generation/v1" / first["dataset_id"]
    assert json.loads((dataset / "receipt.json").read_text())["status"] == "complete"
    assert (dataset / "public/observations.csv").exists()
    assert (dataset / "evaluation/events.json").exists()
    receipt = json.loads((dataset / "receipt.json").read_text())
    assert receipt["inference"]["status"] == "disabled"
    assert (dataset / "settings.json").exists()
    assert (service.visual / "horizon_metrics.csv").exists()
    second = service.create({"seed": 4})
    assert first["dataset_id"] != second["dataset_id"]
    service.close()


def test_invalid_png_and_live_reconfiguration_rejected(tmp_path):
    import pytest

    service = SimulationService(tmp_path, engine_factory=Engine, background=False)
    service.create({})
    service.command("start")
    with pytest.raises(ValueError):
        service.create({})
    with pytest.raises(ValueError):
        service.export_png("not png")
    service.close()


def test_real_engine_public_storage_and_reset(tmp_path):
    service = SimulationService(tmp_path, background=False)
    first = service.create({"objects": [{"kind": "helicopter"}]})
    service.command("start")
    service.engine.advance(20)
    service._consume()
    assert len(service.records) >= 2
    service.command("stop")
    public = (service.dataset / "public/observations.csv").read_text()
    assert "truth" not in public and "phase" not in public
    reset = service.command("reset")
    assert reset["dataset_id"] != first["dataset_id"]
    service.close()


def test_background_clock_pauses_on_lost_heartbeat(tmp_path):
    import time

    service = SimulationService(tmp_path)
    try:
        service.create({"objects": [{"kind": "helicopter"}]})
        service.engine.settings["heartbeat_timeout_s"] = 0.05
        service.command("start")
        deadline = time.monotonic() + 2
        while service.engine.status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.engine.status == "paused"
        assert service.engine.step > 0
        step = service.engine.step
        time.sleep(0.04)
        assert service.engine.step == step
    finally:
        service.close()


def test_background_terminal_writes_receipt(tmp_path):
    import time

    service = SimulationService(tmp_path)
    try:
        service.create({"objects": [{"kind": "helicopter"}]})
        original = service.engine.advance

        def finish():
            original()
            service.engine.stop("all_finished")

        service.engine.advance = finish
        service.command("start")
        deadline = time.monotonic() + 2
        while not (service.dataset / "receipt.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        with service.lock:
            assert (
                json.loads((service.dataset / "receipt.json").read_text())["status"] == "complete"
            )
        assert service.engine.status == "completed"
    finally:
        service.close()


def test_source_hashes_cover_transitive_scenario_and_model_code(tmp_path):
    from pathlib import Path

    project = Path(__file__).resolve().parents[3]
    for relative in (
        "data_generation/v1/full_flight.py",
        "data_generation/v1/full_flight_config.py",
        "models/gru/v1/model.py",
        "models/gru/v1/plugin.py",
        "models/runtime/v1/registry.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / relative).read_bytes())
    service = SimulationService(tmp_path, engine_factory=Engine, background=False)
    service.create({})
    assert len(service.source_hashes) >= 7
    assert str(Path("models/gru/v1/model.py")) in service.source_hashes
    service.close()


def test_replay_uses_saved_settings_after_default_changes(monkeypatch):
    import copy

    from data_generation.v1 import simulation_engine as module

    settings = module.load_simulation_settings()
    engine = module.SimulationEngine(
        {"start_mode": "air", "objects": [{"kind": "helicopter"}]}, settings=settings
    )
    engine.start()
    engine.set_keys(["KeyW"])
    engine.advance(50)
    changed = copy.deepcopy(settings)
    changed["control"]["power_rate_per_s"] *= 2
    monkeypatch.setattr(module, "load_simulation_settings", lambda: changed)
    replay = module.replay_events(engine.config, engine.events, engine.step, settings=settings)
    assert replay.snapshot() == engine.snapshot()
