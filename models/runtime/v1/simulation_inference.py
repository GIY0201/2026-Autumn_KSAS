"""Verified live predictors, isolated asynchronous inference and mature evaluation."""

from __future__ import annotations

import copy
import csv
import multiprocessing as mp
import queue
import re
import time
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf

from contracts.v1.simulation import DT_S, FUTURE_SAMPLES, validate_public_batch

from .io import read_json, sha256
from .registry import get_plugin, list_plugins


def _artifacts(project_root, selection):
    plugin = get_plugin(selection["plugin_id"])
    run_id = selection["run_id"]
    checkpoint = selection["checkpoint"]
    if (
        not isinstance(run_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id)
        or checkpoint not in {"best", "last"}
    ):
        raise ValueError("Invalid explicit checkpoint selection")
    base = (
        Path(project_root) / "outputs/models" / plugin["model_name"] / plugin["version"]
    ).resolve()
    root = (base / run_id).resolve()
    if root.parent != base:
        raise ValueError("Run escapes model root")
    if read_json(root / "status.json").get("status") not in {"completed", "stopped"}:
        raise ValueError("Checkpoint run is not terminal")
    with (root / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    paths = [row["path"] for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate manifest path")
    manifest = {row["path"]: row for row in rows}
    required = [
        "effective_config.yaml",
        "normalization.json",
        f"checkpoints/{checkpoint}/model.safetensors",
        "run.json",
        "status.json",
    ]
    for name in required:
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root)
            or name not in manifest
            or not path.is_file()
            or sha256(path) != manifest[name]["sha256"]
        ):
            raise ValueError(f"Artifact verification failed: {name}")
    config = OmegaConf.to_container(OmegaConf.load(root / required[0]), resolve=True)
    if read_json(root / "run.json").get("run_id") != run_id:
        raise ValueError("Run identity mismatch")
    if config.get("plugin_id") != plugin["id"]:
        raise ValueError("Config plugin identity mismatch")
    scale = float(read_json(root / required[1])["scale_m"])
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Invalid normalization scale")
    plugin["validate_checkpoint"](root / "checkpoints" / checkpoint, config)
    fingerprint = manifest[required[2]]["sha256"]
    if selection.get("checkpoint_sha256", fingerprint) != fingerprint:
        raise ValueError("Selected checkpoint fingerprint changed")
    return plugin, root, config, scale, fingerprint


def checkpoint_catalog(project_root):
    result = []
    for plugin in list_plugins():
        base = Path(project_root) / "outputs/models" / plugin["model_name"] / plugin["version"]
        for root in sorted(base.iterdir()) if base.exists() else []:
            if not root.is_dir() or root.name.startswith("_"):
                continue
            for checkpoint in ("best", "last"):
                selection = dict(plugin_id=plugin["id"], run_id=root.name, checkpoint=checkpoint)
                try:
                    *_, fingerprint = _artifacts(project_root, selection)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                result.append(
                    dict(
                        selection,
                        label=f"{root.name} / {checkpoint}",
                        checkpoint_sha256=fingerprint,
                    )
                )
    return result


class Predictor:
    def __init__(self, model, scale, fingerprint):
        self.model, self.scale_m, self.checkpoint_sha256 = model, scale, fingerprint

    def predict(self, batch):
        public = validate_public_batch(batch)
        values = self.model.predict_positions(
            public["position_enu_m"], public["timestamp_s"], self.scale_m
        )
        result = {key: value.detach().cpu().numpy() for key, value in values.items()}
        if not all(np.isfinite(value).all() for value in result.values()):
            raise ValueError("Nonfinite prediction")
        assert result["position_enu_m"].shape[1:] == (FUTURE_SAMPLES, 3)
        return result


def load_predictor(project_root, selection):
    import torch
    from safetensors.torch import load_file

    plugin, root, config, scale, fingerprint = _artifacts(project_root, selection)
    device = selection.get("device", "cpu")
    if device != "cpu" and not re.fullmatch(r"cuda(?::[0-9]+)?", str(device)):
        raise ValueError("Unsupported inference device")
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("Requested CUDA is unavailable")
    model = plugin["factory"](config)
    model.load_state_dict(
        load_file(str(root / "checkpoints" / selection["checkpoint"] / "model.safetensors")),
        strict=True,
    )
    model.to(device).eval()
    return Predictor(model, scale, fingerprint)


def _worker(root, selection, inputs, outputs):
    try:
        predictor = load_predictor(root, selection)
    except Exception as exc:
        outputs.put(dict(error=f"{type(exc).__name__}: {exc}", fatal=True, metadata=None))
        return
    outputs.put(dict(ready=True, checkpoint_sha256=predictor.checkpoint_sha256))
    while True:
        item = inputs.get()
        if item is None:
            return
        batch, metadata = item
        started = time.perf_counter()
        try:
            result = dict(prediction=predictor.predict(batch))
        except Exception as exc:
            result = dict(error=f"{type(exc).__name__}: {exc}")
        outputs.put(
            dict(result, metadata=metadata, latency_ms=(time.perf_counter() - started) * 1000)
        )


class AsyncPredictor:
    """One in-flight batch and one replaceable pending batch."""

    def __init__(self, project_root, selection):
        context = mp.get_context("spawn")
        self.inputs, self.outputs = context.Queue(1), context.Queue()
        self.process = context.Process(
            target=_worker,
            args=(str(project_root), selection, self.inputs, self.outputs),
            daemon=True,
        )
        self.skipped = 0
        self.closed = False
        self.pending = None
        self.death_reported = False
        self.process.start()

    def submit(self, batch, metadata):
        if self.closed or not self.process.is_alive():
            raise RuntimeError("Inference process unavailable")
        item = (validate_public_batch(batch), copy.deepcopy(metadata))
        if self.pending is not None:
            self.skipped += 1
        self.pending = item
        self._flush()
        return self.skipped

    def _flush(self):
        if self.pending is None or self.closed:
            return
        try:
            self.inputs.put_nowait(self.pending)
            self.pending = None
        except queue.Full:
            pass

    def poll(self):
        self._flush()
        values = []
        while True:
            try:
                values.append(self.outputs.get_nowait())
            except queue.Empty:
                break
        if any(value.get("fatal") for value in values):
            self.death_reported = True
        if not self.closed and not self.process.is_alive() and not self.death_reported:
            values.append(
                dict(
                    error=f"Inference process exited: {self.process.exitcode}",
                    fatal=True,
                    metadata=None,
                )
            )
            self.death_reported = True
        return values

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.inputs.put_nowait(None)
        except queue.Full:
            pass
        self.process.join(timeout=0.5)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        self.inputs.cancel_join_thread()
        self.inputs.close()
        self.outputs.close()


class PredictionEvaluator:
    """Score each reached target once; UI summary reads only accumulated statistics."""

    def __init__(self):
        from collections import defaultdict

        self.observations = {}
        self.predictions = []
        self.waiting = defaultdict(list)
        self.aggregates = {}
        self.horizon_indices = {round(h / DT_S) - 1: str(h) for h in (1, 3, 5, 10, 15)}
        self._aggregate(("overall", "all"))

    def _aggregate(self, key):
        if key not in self.aggregates:
            self.aggregates[key] = {
                "count": 0,
                "ade_sum": np.zeros(2),
                "fde_sum": np.zeros(2),
                "horizon": {
                    h: {"count": 0, "sum": np.zeros(2)} for h in self.horizon_indices.values()
                },
            }
        return self.aggregates[key]

    def observe(self, object_id, t, truth_xyz, observation_xyz):
        values = np.asarray([truth_xyz, observation_xyz], dtype=float)
        if values.shape != (2, 3) or not np.isfinite(values).all() or not np.isfinite(t):
            raise ValueError("Invalid evaluation observation")
        tick = round(float(t), 4)
        samples = self.observations.setdefault(object_id, {})
        if tick in samples:
            if not np.array_equal(samples[tick], values):
                raise ValueError("Conflicting observation at already evaluated timestamp")
            return
        samples[tick] = values.copy()
        for index, horizon_index in self.waiting.pop((object_id, tick), []):
            self._score(self.predictions[index], horizon_index, values)

    def add_prediction(self, object_id, issued_at_s, phase, prediction, horizon_s):
        positions = np.asarray(prediction, dtype=float)
        horizon = np.asarray(horizon_s, dtype=float)
        if (
            positions.shape != (FUTURE_SAMPLES, 3)
            or horizon.shape != (FUTURE_SAMPLES,)
            or not np.isfinite(positions).all()
            or not np.isfinite(issued_at_s)
            or not np.allclose(horizon, np.arange(1, FUTURE_SAMPLES + 1) * DT_S, atol=1e-5)
        ):
            raise ValueError("Invalid evaluation prediction")
        keys = [("overall", "all"), ("object", object_id), ("phase", phase)]
        for key in keys:
            self._aggregate(key)
        item = dict(
            object_id=object_id,
            issued_at_s=float(issued_at_s),
            phase=phase,
            positions=positions.copy(),
            horizon=horizon.copy(),
            errors=np.full((FUTURE_SAMPLES, 2), np.nan),
            reached=0,
            groups=keys,
        )
        index = len(self.predictions)
        self.predictions.append(item)
        samples = self.observations.get(object_id, {})
        for i, offset in enumerate(horizon):
            tick = round(float(issued_at_s) + float(offset), 4)
            if tick in samples:
                self._score(item, i, samples[tick])
            else:
                self.waiting[(object_id, tick)].append((index, i))

    def _score(self, item, index, values):
        errors = np.linalg.norm(item["positions"][index] - values, axis=1)
        item["errors"][index] = errors
        item["reached"] += 1
        h = self.horizon_indices.get(index)
        for key in item["groups"]:
            aggregate = self.aggregates[key]
            if h is not None:
                aggregate["horizon"][h]["count"] += 1
                aggregate["horizon"][h]["sum"] += errors
            if item["reached"] == FUTURE_SAMPLES:
                aggregate["count"] += 1
                aggregate["ade_sum"] += item["errors"].mean(axis=0)
                aggregate["fde_sum"] += item["errors"][-1]
        assert np.isfinite(errors).all() and np.all(errors >= 0)

    @staticmethod
    def _render(aggregate):
        result = {}
        for axis, name in enumerate(("truth", "observed")):
            count = aggregate["count"]
            result[name] = {
                "count": count,
                "ade_m": float(aggregate["ade_sum"][axis] / count) if count else None,
                "fde_m": float(aggregate["fde_sum"][axis] / count) if count else None,
                "horizons": {
                    h: {
                        "count": v["count"],
                        "error_m": float(v["sum"][axis] / v["count"]) if v["count"] else None,
                    }
                    for h, v in aggregate["horizon"].items()
                },
            }
        return result

    def summary(self):
        overall = self._render(self.aggregates[("overall", "all")])
        return dict(
            overall=overall,
            by_object={
                name: self._render(v)
                for (kind, name), v in self.aggregates.items()
                if kind == "object"
            },
            by_phase={
                name: self._render(v)
                for (kind, name), v in self.aggregates.items()
                if kind == "phase"
            },
            prediction_count=len(self.predictions),
            incomplete_predictions=len(self.predictions) - overall["truth"]["count"],
        )

    def finalize(self):
        return self.summary()
