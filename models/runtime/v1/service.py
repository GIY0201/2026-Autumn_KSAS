"""Local training lifecycle, immutable presets and explicit run selection."""

from __future__ import annotations

import copy
import csv
import json
import math
import re
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psutil
from filelock import FileLock
from omegaconf import OmegaConf

from .io import atomic_json, read_json, sha256, write_provenance
from .registry import get_plugin, list_plugins

_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_ACTIVE = {"queued", "running", "stopping"}


def _diagnostic_rows(path):
    """Decode the saved diagnostic CSV contract, preserving absent predictions."""
    numeric = {"epoch", "start_step", "end_step", "step", "sample_index"}
    structured = {"generator_transitions", "generator_event_ids"}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in row.items():
            if key in structured:
                row[key] = json.loads(value or "[]")
            elif key in numeric or key.endswith(("_m", "_s")):
                row[key] = float(value) if value else None
    return rows


class TrainingService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.output_root = self.project_root / "outputs/models"
        self._processes = {}
        self._mutex = threading.RLock()

    def plugins(self):
        return list_plugins()

    def _plugin_root(self, plugin_id):
        plugin = get_plugin(plugin_id)
        return self.output_root / plugin["model_name"] / plugin["version"]

    def _run_path(self, run_id):
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise ValueError("Invalid run ID")
        matches = [
            root / run_id
            for root in {self._plugin_root(p["id"]) for p in self.plugins()}
            if (root / run_id / "run.json").is_file()
        ]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous run ID")
        return matches[0]

    def datasets(self):
        root = self.project_root / "outputs/data_generation/v1"
        result, fingerprints = [], set()
        for path in sorted(root.iterdir()) if root.exists() else []:
            if not (path / "training/train/observations.csv").is_file():
                continue
            if not (path / "training/test/observations.csv").is_file():
                continue
            fingerprint = (
                sha256(path / "files.csv") if (path / "files.csv").is_file() else str(path)
            )
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            result.append({"label": path.name, "value": str(path)})
        return result

    def presets(self, plugin_id):
        root = self._plugin_root(plugin_id) / "_presets"
        return [
            OmegaConf.to_container(OmegaConf.load(path), resolve=True)
            for path in sorted(root.glob("*.yaml"))
        ]

    def save_preset(self, name, config):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Preset name is required")
        self._validate_config(config, require_dataset=False)
        identifier = uuid.uuid4().hex
        root = self._plugin_root(config["plugin_id"]) / "_presets"
        root.mkdir(parents=True, exist_ok=True)
        record = {
            "id": identifier,
            "name": name.strip(),
            "config": config,
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with (root / f"{identifier}.yaml").open("x", encoding="utf-8") as handle:
            handle.write(OmegaConf.to_yaml(OmegaConf.create(record)))
        return identifier

    def _validate_config(self, config, *, require_dataset=True):
        plugin = get_plugin(config.get("plugin_id"))
        schema = plugin.get("parameter_schema", {})
        for key, spec in schema.items():
            value = config
            for part in key.split("."):
                if not isinstance(value, dict) or part not in value:
                    if "default" not in spec:
                        raise ValueError(f"Missing parameter: {key}")
                    value = spec["default"]
                    break
                value = value[part]
            if spec.get("type") == "array":
                if (
                    not isinstance(value, list)
                    or len(value) != spec["length"]
                    or any(
                        isinstance(v, bool)
                        or not isinstance(v, (float, int))
                        or not math.isfinite(v)
                        or v <= spec["exclusiveMinimum"]
                        for v in value
                    )
                ):
                    raise ValueError(f"Invalid positive numeric array: {key}")
            if spec.get("type") in {"integer", "number"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"Numeric parameter required: {key}")
                if spec["type"] == "integer" and not isinstance(value, int):
                    raise ValueError(f"Integer parameter required: {key}")
                if not float("-inf") < value < float("inf"):
                    raise ValueError(f"Finite parameter required: {key}")
                if value < spec.get("minimum", float("-inf")) or value > spec.get(
                    "maximum", float("inf")
                ):
                    raise ValueError(f"Parameter outside allowed range: {key}")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"Unsupported parameter: {key}")
        if require_dataset:
            dataset = Path(config["data"]["dataset_path"]).resolve()
            if str(dataset) not in {item["value"] for item in self.datasets()}:
                raise ValueError("Select a registered dataset")
        return plugin

    def start(self, config, display_name, parent_checkpoint=None):
        config = copy.deepcopy(config)
        self._validate_config(config)
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("Model name is required")
        import torch

        device = config["training"]["device"]
        if device not in {"cuda:0", "cpu"}:
            raise ValueError("Select cuda:0 or explicitly select cpu")
        if device == "cuda:0" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable; no automatic CPU fallback")
        self.output_root.mkdir(parents=True, exist_ok=True)
        with self._mutex, FileLock(str(self.output_root / ".launch.lock"), timeout=5):
            if any(item.get("status") in _ACTIVE for item in self.runs()):
                raise ValueError("A training run is already active")
            parent_run, checkpoint = None, None
            if parent_checkpoint:
                parts = str(parent_checkpoint).replace("\\", "/").split("/")
                if len(parts) != 2 or parts[1] not in {"best", "last"}:
                    raise ValueError("Select an explicit run_id/best or run_id/last checkpoint")
                parent_run = parts[0]
                parent_path = self._run_path(parent_run)
                checkpoint = parent_path / "checkpoints" / parts[1]
                required = ("trainer_state.json", "optimizer.pt", "scheduler.pt", "rng_state.pth")
                if not all((checkpoint / name).is_file() for name in required):
                    raise ValueError("Checkpoint is not resumable")
                original = OmegaConf.to_container(
                    OmegaConf.load(parent_path / "effective_config.yaml"), resolve=True
                )
                revised, before = copy.deepcopy(config), copy.deepcopy(original)
                new_epochs = revised["training"].pop("max_epochs")
                old_epochs = before["training"].pop("max_epochs")
                if revised != before or new_epochs < old_epochs:
                    raise ValueError("Resume allows only extending max_epochs")
                get_plugin(config["plugin_id"])["validate_checkpoint"](checkpoint, config)
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12]
            directory = self._plugin_root(config["plugin_id"]) / run_id
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "logs").mkdir()
            OmegaConf.save(OmegaConf.create(config), directory / "effective_config.yaml")
            metadata = {
                "run_id": run_id,
                "display_name": display_name.strip(),
                "plugin_id": config["plugin_id"],
                "dataset_path": config["data"]["dataset_path"],
                "created_at": datetime.now(UTC).isoformat(),
                "parent_run": parent_run,
                "parent_checkpoint": str(checkpoint) if checkpoint else None,
                "resume_global_step": (
                    read_json(checkpoint / "trainer_state.json")["global_step"]
                    if checkpoint
                    else None
                ),
                "parent_checkpoint_sha256": (
                    {p.name: sha256(p) for p in checkpoint.iterdir() if p.is_file()}
                    if checkpoint
                    else {}
                ),
            }
            write_provenance(directory, self.project_root, Path(config["data"]["dataset_path"]))
            atomic_json(directory / "run.json", metadata)
            atomic_json(directory / "status.json", {"status": "queued", "epoch": 0})
            try:
                with (directory / "logs/worker.log").open("ab", buffering=0) as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-u",
                            "-m",
                            "models.runtime.v1.worker",
                            "--run-dir",
                            str(directory),
                        ],
                        cwd=self.project_root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                self._processes[run_id] = process
                atomic_json(directory / "process.json", {"pid": process.pid})
            except Exception as exc:
                atomic_json(directory / "status.json", {"status": "failed", "error": str(exc)})
                raise
            return run_id

    def stop(self, run_id):
        directory = self._run_path(run_id)
        if self.status(run_id).get("status") not in _ACTIVE:
            raise ValueError("Run is not active")
        (directory / "stop.request").touch(exist_ok=True)

    def status(self, run_id):
        directory = self._run_path(run_id)
        state = read_json(directory / "status.json")
        process = self._processes.get(run_id)
        if state.get("status") in _ACTIVE and process is not None and process.poll() is not None:
            state.update(
                status="failed",
                error=f"Worker exited {process.returncode} without terminal receipt",
            )
            atomic_json(directory / "status.json", state)
        elif state.get("status") in _ACTIVE and process is None:
            record = directory / "process.json"
            if record.is_file():
                try:
                    candidate = psutil.Process(read_json(record)["pid"])
                    command = candidate.cmdline()
                    alive = "models.runtime.v1.worker" in command and str(directory) in command
                except psutil.NoSuchProcess:
                    alive = False
                except psutil.AccessDenied:
                    alive = True
                if not alive:
                    state.update(
                        status="failed", error="Worker unavailable without terminal receipt"
                    )
                    atomic_json(directory / "status.json", state)
        config = OmegaConf.to_container(
            OmegaConf.load(directory / "effective_config.yaml"), resolve=True
        )
        log = directory / "logs/worker.log"
        tail = ""
        if log.is_file():
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 8192))
                tail = handle.read().decode("utf-8", errors="replace")
        return {
            **read_json(directory / "run.json"),
            **state,
            "config": config,
            "device": config["training"]["device"],
            "log_tail": tail,
        }

    def runs(self):
        result = []
        for root in {self._plugin_root(p["id"]) for p in self.plugins()}:
            for path in sorted(root.glob("*/run.json")):
                result.append(self.status(path.parent.name))
        return sorted(result, key=lambda row: row["created_at"], reverse=True)

    def metrics(self, run_id):
        directory = self._run_path(run_id)
        path = directory / "logs/metrics.jsonl"
        rows = []
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        if line.endswith("\n"):
                            raise ValueError("Malformed metrics record") from None
        events = directory / "tensorboard"
        if events.is_dir():
            from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

            accumulator = EventAccumulator(str(events), size_guidance={"scalars": 0})
            accumulator.Reload()
            by_step = {}
            for row in rows:
                by_step[(row.get("global_step", 0), row.get("split", "train"))] = row
            for tag in accumulator.Tags().get("scalars", []):
                prefix, _, name = tag.partition("/")
                if prefix not in {"train", "eval"}:
                    continue
                split = "validation" if prefix == "eval" else "train"
                for event in accumulator.Scalars(tag):
                    row = by_step.setdefault(
                        (event.step, split),
                        {
                            "global_step": event.step,
                            "split": split,
                        },
                    )
                    key = f"eval_{name}" if prefix == "eval" else name
                    row[key] = event.value
                    if name == "epoch":
                        row["epoch"] = event.value
            rows = sorted(by_step.values(), key=lambda row: row["global_step"])
        return rows

    def predict(self, run_id, checkpoint="best"):
        if checkpoint not in {"best", "last"}:
            raise ValueError("Select best or last")
        directory = self._run_path(run_id)
        path = directory / "evaluation" / f"preview_{checkpoint}.json"
        if not path.is_file():
            raise ValueError("Prediction preview is available after checkpoint saving")
        return read_json(path)

    def diagnostic_windows(
        self, run_id, checkpoint, *, vertical=None, xy_movement=None, sort_by="axis_rmse_z_m"
    ):
        """List explicitly selected saved validation windows without loading a model."""
        if checkpoint not in {"best", "last"}:
            raise ValueError("Select best or last")
        if sort_by not in {"axis_rmse_z_m", "ade_m"}:
            raise ValueError("Select Z RMSE or ADE sorting")
        directory = self._run_path(run_id) / "diagnostics" / checkpoint / "validation"
        if not all((directory / name).is_file() for name in (
            "windows.csv", "points.csv", "provenance.json"
        )):
            return {"available": False, "windows": [], "provenance": {},
                    "vertical_options": [], "xy_options": []}
        rows = _diagnostic_rows(directory / "windows.csv")
        vertical_options = sorted({row["observed_vertical"] for row in rows})
        xy_options = sorted({row["observed_xy_movement"] for row in rows})
        if vertical is not None and vertical not in vertical_options:
            raise ValueError("Unknown observed vertical label")
        if xy_movement is not None and xy_movement not in xy_options:
            raise ValueError("Unknown observed XY label")
        selected = [row for row in rows
                    if (vertical is None or row["observed_vertical"] == vertical)
                    and (xy_movement is None or row["observed_xy_movement"] == xy_movement)]
        selected.sort(key=lambda row: (-row[sort_by], row["sequence_id"]))
        return {"available": True, "windows": selected,
                "provenance": read_json(directory / "provenance.json"),
                "vertical_options": vertical_options, "xy_options": xy_options}

    def diagnostic_window(self, run_id, checkpoint, sequence_id):
        """Select a CSV identity only; a sequence selector is never a filesystem path."""
        if not isinstance(sequence_id, str) or not _ID.fullmatch(sequence_id):
            raise ValueError("Invalid window ID")
        catalog = self.diagnostic_windows(run_id, checkpoint)
        selected = [row for row in catalog["windows"] if row["sequence_id"] == sequence_id]
        if len(selected) != 1:
            raise ValueError("Saved window diagnostics unavailable")
        directory = self._run_path(run_id) / "diagnostics" / checkpoint / "validation"
        points = [row for row in _diagnostic_rows(directory / "points.csv")
                  if row["sequence_id"] == sequence_id]
        if not points:
            raise ValueError("Saved window points unavailable")
        return {"window": selected[0], "points": points, "provenance": catalog["provenance"]}

    def close(self):
        # Training is independent of viewer lifecycle; preserve running workers.
        return None
