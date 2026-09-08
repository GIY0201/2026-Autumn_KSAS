"""Single-process real-time simulation orchestration and durable run records."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import json
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path


def _json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


class SimulationService:
    """Own one active run; inference and rendering never advance its clock."""

    def __init__(self, project_root, engine_factory=None, background=True):
        self.root = Path(project_root).resolve()
        self.engine_factory = engine_factory
        self.lock = threading.RLock()
        self.engine = None
        self.predictor = None
        self.closed = threading.Event()
        self.error = None
        self.heartbeat = time.monotonic()
        self.dataset_id = self.run_id = None
        self.owner_client_id = None
        self.records = []
        self.predictions = []
        self.buffers = defaultdict(lambda: deque(maxlen=16))
        self.evaluator = None
        self.thread = None
        self.last_flush = time.monotonic()
        self.metrics_cache = {}
        self.metrics_at = 0
        if background:
            self.thread = threading.Thread(target=self._loop, name="simulation-clock", daemon=True)
            self.thread.start()

    def catalog(self):
        from data_generation.v1.simulation_engine import simulation_catalog
        from models.runtime.v1.simulation_inference import checkpoint_catalog

        result = simulation_catalog()
        result["models"] = checkpoint_catalog(self.root)
        return result

    def create(self, config):
        with self.lock:
            if self.engine and self.engine.snapshot()["status"] in ("running", "paused", "ready"):
                raise ValueError("End the current run before changing settings.")
            if not isinstance(config, dict):
                raise ValueError("Configuration must be an object.")
            config = copy.deepcopy(config)
            owner = config.pop("client_id", None)
            config.pop("run_id", None)
            if self.engine_factory is None:
                from data_generation.v1.simulation_engine import SimulationEngine

                factory = SimulationEngine
            else:
                factory = self.engine_factory
            from models.runtime.v1.simulation_inference import AsyncPredictor, PredictionEvaluator

            selection = config.get("model")
            if selection:
                from models.runtime.v1.simulation_inference import _artifacts

                _, artifact_root, _, _, fingerprint = _artifacts(self.root, selection)
                selection["checkpoint_sha256"] = fingerprint
                self.model_provenance = {
                    name: hashlib.sha256((artifact_root / name).read_bytes()).hexdigest()
                    for name in ("effective_config.yaml", "normalization.json")
                }
                self.model_provenance["checkpoint_sha256"] = fingerprint
            else:
                self.model_provenance = None
            engine = factory(config)
            predictor = AsyncPredictor(self.root, selection) if selection else None
            if self.predictor:
                self.predictor.close()
            self.engine, self.predictor = engine, predictor
            self.owner_client_id = owner
            self.evaluator = PredictionEvaluator()
            self.dataset_id, self.run_id = str(uuid.uuid4()), str(uuid.uuid4())
            self.dataset = self.root / "outputs/data_generation/v1" / self.dataset_id
            self.visual = self.root / "outputs/visualization/v1" / self.run_id
            (self.dataset / "public").mkdir(parents=True)
            (self.dataset / "evaluation").mkdir()
            self.visual.mkdir(parents=True)
            self.records, self.predictions = [], []
            self.persisted_records = self.persisted_events = self.persisted_predictions = 0
            self.buffers.clear()
            self.error = None
            self.metrics_cache = {}
            self.metrics_at = 0
            self.heartbeat = time.monotonic()
            self.inference = {"status": "loading" if predictor else "disabled", "skipped": 0}
            _json(self.dataset / "config.json", engine.config)
            _json(self.dataset / "settings.json", getattr(engine, "settings", {}))
            sources = [
                Path(__file__),
                Path(__file__).with_name("simulation_engine.py"),
                self.root / "models/runtime/v1/simulation_inference.py",
                self.root / "contracts/v1/simulation.py",
                self.root / "data_generation/v1/full_flight.py",
                self.root / "data_generation/v1/full_flight_config.py",
                self.root / "models/gru/v1/model.py",
                self.root / "models/gru/v1/plugin.py",
                self.root / "models/runtime/v1/registry.py",
                self.root / "uv.lock",
            ]
            for folder in ("data_generation/v1/configs", "visualization/v1/simulation_web"):
                sources.extend(path for path in (self.root / folder).rglob("*") if path.is_file())
            self.source_hashes = {
                str(path.relative_to(self.root))
                if path.is_relative_to(self.root)
                else path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources
                if path.is_file()
            }
            self._flush("partial")
            return self.state()

    def authorize(self, action, payload):
        """HTTP session/run isolation; local Python callers remain explicit owners."""
        try:
            uuid.UUID(payload.get("client_id", ""))
        except (ValueError, TypeError, AttributeError) as exc:
            raise PermissionError("A valid client_id is required.") from exc
        if action == "create" and (
            not self.engine or self.engine.status not in ("ready", "running", "paused")
        ):
            return
        if payload.get("client_id") != self.owner_client_id or payload.get("run_id") != self.run_id:
            raise PermissionError("This client or run does not own the simulation.")
        if action == "keys" and payload.get("object_id") != self.engine.selected_id:
            raise PermissionError("The selected control target changed.")

    def command(self, action, payload=None):
        payload = payload or {}
        with self.lock:
            if self.engine is None:
                raise ValueError("Create a simulation first.")
            if action == "heartbeat":
                self.heartbeat = time.monotonic()
            elif action == "keys":
                self.engine.set_keys(payload.get("keys", []))
                self.heartbeat = time.monotonic()
            elif action == "select":
                self.engine.select(payload["object_id"])
            elif action in ("start", "resume"):
                if self.inference["status"] in ("loading", "failed"):
                    raise ValueError("The selected model is not ready.")
                self.heartbeat = time.monotonic()
                getattr(self.engine, action)()
            elif action == "pause":
                self.engine.pause()
            elif action in ("stop", "reset"):
                self.engine.stop(reason=action)
                self._consume()
                self._flush("failed" if self.error else "complete")
                if self.predictor:
                    self.predictor.close()
                    self.predictor = None
                if action == "reset":
                    return self.create(dict(self.engine.config, client_id=self.owner_client_id))
            else:
                raise ValueError("Unknown simulation action.")
            return self.state()

    def state(self):
        with self.lock:
            if self.engine is None:
                return {"status": "idle", "objects": []}
            result = self.engine.snapshot()
            latest = {item["object_id"]: item for item in self.predictions}
            for obj in result["objects"]:
                obj["prediction"] = latest.get(obj["id"])
            if time.monotonic() - self.metrics_at > 1:
                self.metrics_cache = self.evaluator.summary() if self.evaluator else {}
                self.metrics_at = time.monotonic()
            result.update(
                dataset_id=self.dataset_id,
                run_id=self.run_id,
                owner_client_id=self.owner_client_id,
                error=self.error,
                inference=self.inference,
                predictions=self.predictions[-16:],
                metrics=self.metrics_cache,
                history={key: list(value) for key, value in self.buffers.items()},
            )
            return result

    def _consume(self):
        observations = self.engine.drain_observations()
        for row in observations:
            self.records.append(row)
            self.evaluator.observe(
                row["object_id"], row["t_s"], row["truth_position_enu_m"], row["position_enu_m"]
            )
            public = {k: row[k] for k in ("object_id", "t_s", "position_enu_m", "sigma_m", "valid")}
            buffer = self.buffers[row["object_id"]]
            if not row["valid"]:
                buffer.clear()
            else:
                buffer.append(public)
        if self.predictor:
            for result in self.predictor.poll():
                if result.get("ready"):
                    self.inference["status"] = "ready"
                    continue
                if result.get("error"):
                    self.inference.update(status="failed", error=result["error"])
                    self.error = result["error"]
                    if result.get("fatal"):
                        self.predictor.close()
                        self.predictor = None
                        break
                    continue
                meta = result["metadata"]
                prediction = result["prediction"]
                for index, item in enumerate(meta["objects"]):
                    positions = prediction["position_enu_m"][index]
                    horizon = prediction["horizon_s"]
                    record = dict(
                        item,
                        position_enu_m=positions.tolist()
                        if hasattr(positions, "tolist")
                        else positions,
                        horizon_s=horizon.tolist() if hasattr(horizon, "tolist") else horizon,
                        latency_ms=result["latency_ms"],
                    )
                    self.predictions.append(record)
                    self.evaluator.add_prediction(
                        item["object_id"],
                        item["issued_at_s"],
                        item["phase"],
                        record["position_enu_m"],
                        record["horizon_s"],
                    )
                self.inference.update(status="ready", latency_ms=result["latency_ms"])
            if self.predictor and observations:
                selected = [
                    r
                    for r in observations
                    if len(self.buffers[r["object_id"]]) == 16 and r["valid"]
                ]
                if selected:
                    batches = [list(self.buffers[r["object_id"]]) for r in selected]
                    batch = {
                        "position_enu_m": [[x["position_enu_m"] for x in b] for b in batches],
                        "timestamp_s": [[x["t_s"] for x in b] for b in batches],
                        "valid_mask": [[x["valid"] for x in b] for b in batches],
                    }
                    metadata = {
                        "objects": [
                            {
                                "object_id": r["object_id"],
                                "issued_at_s": r["t_s"],
                                "phase": r["phase"],
                            }
                            for r in selected
                        ]
                    }
                    self.inference["skipped"] = self.predictor.submit(batch, metadata)

    def _loop(self):
        deadline = time.monotonic()
        while not self.closed.wait(0.005):
            with self.lock:
                if self.engine and self.engine.status in ("ready", "paused") and self.predictor:
                    try:
                        self._consume()
                    except Exception as exc:
                        self.error = f"{type(exc).__name__}: {exc}"
                        self.inference.update(status="failed", error=self.error)
                if not self.engine or self.engine.status != "running":
                    deadline = time.monotonic()
                    continue
                try:
                    settings = getattr(self.engine, "settings", {})
                    if time.monotonic() - self.heartbeat > settings.get("heartbeat_timeout_s", 0.5):
                        self.engine.pause()
                        continue
                    dt = settings.get("dt_s", 0.02)
                    if time.monotonic() >= deadline + dt:
                        self.engine.advance()
                        deadline += dt
                        self._consume()
                    status = self.engine.status
                    if status not in ("running", "paused", "ready"):
                        self._flush("failed" if self.error else "complete")
                        if self.predictor:
                            self.predictor.close()
                            self.predictor = None
                    elif time.monotonic() - self.last_flush >= 2:
                        self._flush("partial")
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.engine.stop(reason="failure")
                    self._flush("failed")

    def _flush(self, status):
        self.last_flush = time.monotonic()
        public_fields = ["object_id", "t_s", "x_m", "y_m", "z_m", "sigma_m", "valid"]
        for relative, truth in [("public/observations.csv", False), ("evaluation/truth.csv", True)]:
            path = self.dataset / relative
            exists = path.exists()
            with path.open("a", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                if not exists:
                    writer.writerow(
                        public_fields
                        if not truth
                        else ["object_id", "t_s", "x_m", "y_m", "z_m", "phase"]
                    )
                for row in self.records[self.persisted_records :]:
                    xyz = row["truth_position_enu_m" if truth else "position_enu_m"]
                    writer.writerow(
                        [
                            row["object_id"],
                            row["t_s"],
                            *xyz,
                            *([row["phase"]] if truth else [row["sigma_m"], row["valid"]]),
                        ]
                    )
        self.persisted_records = len(self.records)
        for filename, rows, cursor in [
            ("events", self.engine.events, "persisted_events"),
            ("predictions", self.predictions, "persisted_predictions"),
        ]:
            with (self.dataset / f"evaluation/{filename}.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                for row in rows[getattr(self, cursor) :]:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
            setattr(self, cursor, len(rows))
            if status != "partial":
                _json(self.dataset / f"evaluation/{filename}.json", rows)
        _json(self.visual / "metrics.json", self.evaluator.summary())
        summary = self.evaluator.summary()
        with (self.visual / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["group", "id", "reference", "count", "ade_m", "fde_m"])
            groups = [("overall", "all", summary["overall"])]
            groups += [
                (group, key, value)
                for group in ("by_object", "by_phase")
                for key, value in summary[group].items()
            ]
            for group, key, values in groups:
                for reference, metrics in values.items():
                    writer.writerow(
                        [
                            group,
                            key,
                            reference,
                            metrics["count"],
                            metrics["ade_m"],
                            metrics["fde_m"],
                        ]
                    )
        with (self.visual / "horizon_metrics.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(["group", "id", "reference", "horizon_s", "count", "error_m"])
            for group, key, values in groups:
                for reference, metrics in values.items():
                    for horizon, horizon_values in metrics["horizons"].items():
                        writer.writerow(
                            [
                                group,
                                key,
                                reference,
                                horizon,
                                horizon_values["count"],
                                horizon_values["error_m"],
                            ]
                        )
        manifest = {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "status": status,
            "seed": self.engine.config.get("seed"),
            "error": self.error,
            "model": self.engine.config.get("model"),
            "model_provenance": self.model_provenance,
            "source_hashes": self.source_hashes,
            "inference": dict(self.inference),
            "settings": "settings.json",
        }
        _json(self.dataset / "manifest.json", manifest)
        _json(self.visual / "manifest.json", manifest)
        with (self.dataset / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["dataset_id", "run_id", "status", "seed", "config"])
            writer.writerow([self.dataset_id, self.run_id, status, manifest["seed"], "config.json"])
        if status != "partial":
            _json(self.dataset / "receipt.json", dict(manifest, final_state=self.engine.snapshot()))
            for directory in (self.dataset, self.visual):
                with (directory / "files.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["path", "sha256"])
                    for path in sorted(directory.rglob("*")):
                        if path.is_file() and path.name != "files.csv":
                            writer.writerow(
                                [
                                    path.relative_to(directory).as_posix(),
                                    hashlib.sha256(path.read_bytes()).hexdigest(),
                                ]
                            )

    def export_png(self, data):
        with self.lock:
            limit = (
                getattr(self.engine, "settings", {}).get("ui", {}).get("max_png_bytes", 10_000_000)
            )
            if not self.engine or not isinstance(data, str) or len(data) > limit * 4 // 3 + 64:
                raise ValueError("Invalid PNG export.")
            try:
                raw = base64.b64decode(data.removeprefix("data:image/png;base64,"), validate=True)
            except Exception as exc:
                raise ValueError("Invalid PNG encoding.") from exc
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG signature.")
            if len(raw) > limit:
                raise ValueError("PNG exceeds configured limit.")
            path = self.visual / f"{uuid.uuid4()}.png"
            path.write_bytes(raw)
            _json(
                path.with_suffix(".json"),
                {
                    "run_id": self.run_id,
                    "dataset_id": self.dataset_id,
                    "state": self.engine.snapshot(),
                    "png_sha256": hashlib.sha256(raw).hexdigest(),
                },
            )
            if self.engine.status not in ("ready", "running", "paused"):
                self._flush("failed" if self.error else "complete")
            return {"path": str(path), "run_id": self.run_id}

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=2)
        with self.lock:
            if self.engine and self.engine.snapshot()["status"] in ("running", "paused", "ready"):
                self.engine.stop(reason="shutdown")
                self._flush("partial")
            if self.predictor:
                self.predictor.close()
