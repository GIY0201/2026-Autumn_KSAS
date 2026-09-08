"""Public local generation service: explicit presets, one worker, durable status."""

from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, replace
from numbers import Real
from pathlib import Path
from subprocess import Popen
from threading import RLock
from uuid import uuid4

from omegaconf import OmegaConf

from contracts.v1.csv_io import read_public_dataset

from .dataset import (
    CONTRACT_VERSION,
    GENERATOR_VERSION,
    X8_INPUT_ID,
    X8_MODEL_ID,
    X8_OBJECT_ID,
)
from .generate import PROJECT_ROOT, configured_episode_count, generate_from_config
from .profiles import load_generation_profile
from .source import X8_SOURCE_ID

CONFIG_ROOT = Path(__file__).resolve().parent / "configs"
MAX_SEED = 2**32 - 1
_STATUS_COLUMNS = ("state", "phase", "completed", "total", "result_path", "message")
_STATUS_REPLACE_ATTEMPTS = 5
_STATUS_REPLACE_DELAY_S = 0.02


@dataclass(frozen=True, slots=True)
class GenerationPreset:
    """User-facing metadata read from one explicitly named generator configuration."""

    preset_id: str
    label: str
    description: str
    episode_count: int
    default_seed: int
    object_id: str = X8_OBJECT_ID


@dataclass(frozen=True, slots=True)
class ObjectSupport:
    """Separate approved object scope from actually registered runnable presets."""

    object_id: str
    label: str
    unavailable_reason: str
    preset_ids: tuple[str, ...]

    @property
    def available(self) -> bool:
        return bool(self.preset_ids)


@dataclass(frozen=True, slots=True)
class GenerationJob:
    """Immutable view of a job; complete means worker exit plus validated output."""

    job_id: str
    preset_id: str
    label: str
    seed: int
    total: int
    work_path: Path
    state: str = "running"
    phase: str = "starting"
    completed: int = 0
    result_path: Path | None = None
    message: str = ""
    profile_id: str | None = None


def list_presets() -> tuple[GenerationPreset, ...]:
    """List only real configuration files explicitly exposed with UI metadata."""
    presets = []
    for path in sorted(CONFIG_ROOT.glob("*.yaml")):
        config = OmegaConf.load(path)
        if "ui" not in config:
            continue
        presets.append(
            GenerationPreset(
                preset_id=path.stem,
                label=str(config.ui.label),
                description=str(config.ui.description),
                episode_count=configured_episode_count(config),
                default_seed=int(config.seed),
                object_id=_request_provenance(config.get("profile_id"))["object_id"],
            )
        )
    return tuple(presets)


def list_object_support(presets: tuple[GenerationPreset, ...]) -> tuple[ObjectSupport, ...]:
    """Read the explicit v1 scope and derive availability from real preset metadata."""
    catalog = OmegaConf.load(CONFIG_ROOT / "object_support.yaml")
    support = tuple(
        ObjectSupport(
            object_id=str(item.object_id),
            label=str(item.label),
            unavailable_reason=str(item.unavailable_reason),
            preset_ids=tuple(p.preset_id for p in presets if p.object_id == item.object_id),
        )
        for item in catalog.objects
    )
    identifiers = {item.object_id for item in support}
    if len(identifiers) != len(support) or any(p.object_id not in identifiers for p in presets):
        raise ValueError("object support catalog must uniquely cover every preset object")
    if any(not item.label or not item.unavailable_reason for item in support):
        raise ValueError("object support entries require readable labels and unavailable reasons")
    return support


def _seed(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or int(value) != value
        or not 0 <= value <= MAX_SEED
    ):
        raise ValueError(f"seed must be an integer from 0 through {MAX_SEED}")
    return int(value)


def _write_status(work_path: Path, **values: object) -> None:
    """Replace a single-row receipt atomically so polling never reads a partial CSV."""
    staging = work_path / "status.pending.csv"
    with staging.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_STATUS_COLUMNS)
        writer.writeheader()
        writer.writerow(values)
    for attempt in range(_STATUS_REPLACE_ATTEMPTS):
        try:
            staging.replace(work_path / "status.csv")
            return
        except PermissionError:
            if attempt == _STATUS_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_STATUS_REPLACE_DELAY_S)


def _read_status(work_path: Path) -> dict[str, str]:
    with (work_path / "status.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _STATUS_COLUMNS:
            raise ValueError("invalid worker receipt columns")
        rows = list(reader)
    if len(rows) != 1 or any(value is None for value in rows[0].values()):
        raise ValueError("invalid worker receipt row")
    return rows[0]


def _request_provenance(profile_id: str | None) -> dict[str, str]:
    """Bind a result to the explicitly selected model rather than its UI label."""
    if profile_id is None:
        return {
            "object_id": X8_OBJECT_ID,
            "model_id": X8_MODEL_ID,
            "source_id": X8_SOURCE_ID,
            "input_id": X8_INPUT_ID,
        }
    profile = load_generation_profile(profile_id)
    return {key: profile[key] for key in ("object_id", "model_id", "source_id", "input_id")}


def _validate_result(
    result_path: Path,
    output_root: Path,
    seed: int,
    total: int,
    *,
    profile_id: str | None = None,
) -> None:
    """Check both worker and parent boundaries against the existing public contract."""
    if not result_path.is_relative_to(
        output_root.resolve() / "data_generation" / GENERATOR_VERSION
    ):
        raise ValueError("worker result is outside the configured output root")
    with (result_path / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifests = list(csv.DictReader(handle))
    if len(manifests) != 1 or any(
        manifests[0].get(key) != value
        for key, value in {
            "kind": "data_generation",
            "version": GENERATOR_VERSION,
            "contract_version": CONTRACT_VERSION,
            "status": "complete",
            "seed": str(seed),
            **_request_provenance(profile_id),
        }.items()
    ):
        raise ValueError("generation manifest does not match the request")
    public = read_public_dataset(result_path / "public")
    if len(public.episodes) != total:
        raise ValueError("generated episode count does not match the request")
    effective = OmegaConf.load(result_path / "effective_model_config.yaml")
    if OmegaConf.select(effective, "run.training") is not None:
        from .training_data import read_sequence_index

        read_sequence_index(result_path, public.episodes)


def run_job(work_path: Path, output_root: Path) -> int:
    """Run inside the worker process; never serve HTTP or change viewer state."""
    completed, total = 0, 0
    result_path: Path | None = None

    def progress(count: int, count_total: int, phase: str) -> None:
        nonlocal completed, total
        assert 0 <= count <= count_total
        completed, total = count, count_total
        _write_status(
            work_path,
            state="running",
            phase=phase,
            completed=count,
            total=count_total,
            result_path="",
            message="",
        )

    try:
        config = OmegaConf.load(work_path / "request.yaml")
        total = configured_episode_count(config)
        result = generate_from_config(config, output_root=output_root, progress=progress)
        result_path = result.path.resolve()
        if result.status != "complete":
            raise ValueError(f"generation returned {result.status}; see failed_episodes.csv")
        progress(completed, total, "validating")
        _validate_result(
            result_path,
            output_root,
            int(config.seed),
            total,
            profile_id=config.get("profile_id"),
        )
        _write_status(
            work_path,
            state="complete",
            phase="complete",
            completed=total,
            total=total,
            result_path=str(result_path),
            message="",
        )
        return 0
    except Exception as error:
        traceback.print_exc()
        _write_status(
            work_path,
            state="failed",
            phase="failed",
            completed=completed,
            total=total,
            result_path=str(result_path or ""),
            message=f"{type(error).__name__}: {error}",
        )
        return 1


class GenerationService:
    """Own at most one local child process and keep completed result references."""

    def __init__(self, *, output_root: Path, work_root: Path | None = None) -> None:
        self.output_root = Path(output_root).resolve()
        self.work_root = (
            Path(work_root) if work_root is not None else PROJECT_ROOT / "temp" / "generation-jobs"
        ).resolve()
        self.presets = list_presets()
        self.object_support = list_object_support(self.presets)
        self._job: GenerationJob | None = None
        self._process: Popen | None = None
        self._results: list[GenerationJob] = []
        self._lock = RLock()

    @property
    def results(self) -> tuple[GenerationJob, ...]:
        """Return successful jobs in submission order, without auto-selecting any."""
        with self._lock:
            self.snapshot()
            return tuple(self._results)

    def submit(self, preset_id: str, seed: object) -> GenerationJob:
        """Validate a request and launch the current interpreter without a shell."""
        with self._lock:
            current = self.snapshot()
            if current is not None and current.state == "running":
                raise RuntimeError("a generation job is already running")
            preset = next((item for item in self.presets if item.preset_id == preset_id), None)
            if preset is None:
                raise ValueError("unsupported generation preset")
            normalized_seed = _seed(seed)
            config = OmegaConf.load(CONFIG_ROOT / f"{preset.preset_id}.yaml")
            config.seed = normalized_seed
            profile_id = config.get("profile_id")
            _request_provenance(profile_id)
            job_id = str(uuid4())
            work_path = self.work_root / job_id
            work_path.mkdir(parents=True, exist_ok=False)
            OmegaConf.save(config, work_path / "request.yaml", resolve=True)
            self._job = GenerationJob(
                job_id,
                preset_id,
                preset.label,
                normalized_seed,
                preset.episode_count,
                work_path,
                profile_id=profile_id,
            )
            command = [
                sys.executable,
                "-m",
                "data_generation.v1.service",
                "--work-path",
                str(work_path),
                "--output-root",
                str(self.output_root),
            ]
            try:
                with (work_path / "worker.log").open("ab") as log:
                    self._process = Popen(
                        command,
                        cwd=PROJECT_ROOT,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
            except OSError as error:
                self._process = None
                self._job = replace(self._job, state="failed", phase="failed", message=str(error))
            return self._job

    def snapshot(self) -> GenerationJob | None:
        """Poll status files and process exit; a missing receipt is never success."""
        with self._lock:
            if self._job is None or self._job.state != "running":
                return self._job
            assert self._process is not None
            exit_code = self._process.poll()
            try:
                receipt = _read_status(self._job.work_path)
                completed, total = int(receipt["completed"]), int(receipt["total"])
                if total != self._job.total or not 0 <= completed <= total:
                    raise ValueError("invalid worker progress counts")
                phase = receipt["phase"]
                if exit_code is None and receipt["state"] in {"complete", "failed"}:
                    phase = "finishing"
                self._job = replace(self._job, completed=completed, phase=phase)
            except (OSError, ValueError, csv.Error) as error:
                if exit_code is None:
                    return self._job
                self._job = replace(
                    self._job,
                    state="failed",
                    phase="failed",
                    message=f"Missing/invalid worker receipt: {error}",
                )
                return self._job
            if exit_code is None:
                return self._job
            if exit_code == 0 and receipt["state"] == "complete":
                try:
                    if completed != total or receipt["phase"] != "complete":
                        raise ValueError("incomplete worker terminal receipt")
                    path = Path(receipt["result_path"]).resolve()
                    _validate_result(
                        path,
                        self.output_root,
                        self._job.seed,
                        self._job.total,
                        profile_id=self._job.profile_id,
                    )
                except (OSError, ValueError, csv.Error) as error:
                    self._job = replace(
                        self._job,
                        state="failed",
                        phase="failed",
                        message=f"Invalid completed output: {error}",
                    )
                else:
                    self._job = replace(
                        self._job, state="complete", phase="complete", result_path=path
                    )
                    self._results.append(self._job)
            else:
                self._job = replace(
                    self._job,
                    state="failed",
                    phase="failed",
                    message=(receipt["message"] or f"Worker exit code: {exit_code}")
                    + (f" Artifact: {receipt['result_path']}" if receipt["result_path"] else ""),
                )
            return self._job

    def cancel(self) -> GenerationJob | None:
        """Terminate only this service's active child; never delete output artifacts."""
        with self._lock:
            current = self.snapshot()
            if current is not None and current.state == "running":
                assert self._process is not None
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
                self._job = replace(current, state="cancelled", phase="cancelled")
                try:
                    _write_status(
                        current.work_path,
                        state="cancelled",
                        phase="cancelled",
                        completed=current.completed,
                        total=current.total,
                        result_path="",
                        message="Cancelled by the local owner",
                    )
                except OSError as error:
                    self._job = replace(self._job, message=f"Could not save cancellation: {error}")
            return self._job

    def close(self) -> None:
        """Stop this service's unfinished work when its owner shuts down."""
        self.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Internal local generation worker.")
    parser.add_argument("--work-path", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(run_job(arguments.work_path, arguments.output_root))
