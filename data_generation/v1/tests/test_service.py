"""Tests for the bounded, nonblocking generation service used by the viewer."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from data_generation.v1 import generate, service
from data_generation.v1.dataset import GenerationRequest, write_dataset
from data_generation.v1.tests.test_dataset import _episode


class FakeProcess:
    """Keep process scheduling deterministic while testing real worker/file behavior."""

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.returncode = None
        self.pid = 12345

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        assert self.returncode is not None
        return self.returncode


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "Popen", FakeProcess)
    manager = service.GenerationService(
        output_root=tmp_path / "outputs", work_root=tmp_path / "jobs"
    )
    yield manager
    manager.close()


def _finish(manager, monkeypatch, *, failure=False):
    def run(config, *, output_root, progress):
        progress(0, int(config.episode_count), "simulating")
        if failure:
            raise RuntimeError("test integration failed")
        progress(1, 1, "writing")
        return write_dataset(
            [_episode()],
            request=GenerationRequest(int(config.seed), "diagnostic", "NOT_RUN"),
            output_root=output_root,
        )

    monkeypatch.setattr(service, "generate_from_config", run)
    job = manager.snapshot()
    exit_code = service.run_job(job.work_path, manager.output_root)
    manager._process.returncode = exit_code
    return manager.snapshot()


def test_presets_come_from_real_configs_with_readable_names():
    presets = service.list_presets()
    assert {preset.preset_id for preset in presets} == {
        "diagnostic",
        "diagnostic_helicopter",
        "diagnostic_quadrotor",
        "diagnostic_vtol",
        "diagnostic_with_motion_check",
        "pilot",
        "pilot_helicopter",
        "pilot_quadrotor",
        "pilot_vtol",
    }
    assert {preset.object_id for preset in presets} == {
        "x8_fixed_wing",
        "quadrotor",
        "vtol",
        "helicopter",
    }
    for preset in presets:
        config = OmegaConf.load(service.CONFIG_ROOT / f"{preset.preset_id}.yaml")
        assert preset.label == config.ui.label
        assert preset.episode_count == config.episode_count
        assert preset.default_seed == config.seed
        assert preset.description == config.ui.description


@pytest.mark.parametrize("seed", [-1, 1.2, True, None, "17", float("nan"), 2**32])
def test_invalid_seed_does_not_launch_a_process(jobs, seed):
    with pytest.raises(ValueError, match="seed"):
        jobs.submit("diagnostic", seed)
    assert jobs.snapshot() is None
    assert not jobs.work_root.exists()


def test_unknown_preset_and_path_traversal_are_rejected(jobs):
    with pytest.raises(ValueError, match="preset"):
        jobs.submit("../../outside", 17)
    assert jobs.snapshot() is None


def test_submit_is_nonblocking_and_rejects_duplicate_run(jobs):
    first = jobs.submit("diagnostic", 42)
    assert first.state == "running"
    assert first.completed == 0 and first.total == 1
    config = OmegaConf.load(first.work_path / "request.yaml")
    assert config.seed == 42
    assert jobs._process.kwargs["shell"] is False
    assert jobs._process.command[0] == service.sys.executable
    with pytest.raises(RuntimeError, match="already running"):
        jobs.submit("pilot", 17)
    assert jobs.snapshot().job_id == first.job_id


def test_complete_requires_validated_output_and_keeps_seed_provenance(jobs, monkeypatch):
    jobs.submit("diagnostic", 42)
    finished = _finish(jobs, monkeypatch)
    assert finished.state == "complete"
    assert finished.completed == finished.total == 1
    assert finished.result_path.is_relative_to(jobs.output_root)
    with (finished.result_path / "manifest.csv").open(encoding="utf-8") as handle:
        manifest = next(csv.DictReader(handle))
    assert manifest["seed"] == "42"
    assert manifest["status"] == "complete"
    assert len(jobs.results) == 1


def test_worker_exception_is_failed_and_not_playable(jobs, monkeypatch):
    jobs.submit("diagnostic", 42)
    failed = _finish(jobs, monkeypatch, failure=True)
    assert failed.state == "failed"
    assert "test integration failed" in failed.message
    assert failed.result_path is None
    assert jobs.results == ()


def test_process_exit_without_receipt_is_not_success(jobs):
    jobs.submit("diagnostic", 42)
    jobs._process.returncode = 0
    assert jobs.snapshot().state == "failed"
    assert "receipt" in jobs.snapshot().message


@pytest.mark.parametrize("completed,phase", [(0, "complete"), (1, "writing")])
def test_incomplete_terminal_receipt_is_rejected(jobs, completed, phase):
    job = jobs.submit("diagnostic", 42)
    service._write_status(
        job.work_path,
        state="complete",
        phase=phase,
        completed=completed,
        total=1,
        result_path=str(jobs.output_root / "data_generation/v1/missing"),
        message="",
    )
    jobs._process.returncode = 0
    assert jobs.snapshot().state == "failed"
    assert jobs.results == ()


def test_missing_result_cannot_be_reported_complete(jobs):
    job = jobs.submit("diagnostic", 42)
    service._write_status(
        job.work_path,
        state="complete",
        phase="complete",
        completed=1,
        total=1,
        result_path=str(jobs.output_root / "data_generation/v1/missing"),
        message="",
    )
    jobs._process.returncode = 0
    assert jobs.snapshot().state == "failed"


def test_terminal_receipt_must_match_the_requested_seed(jobs):
    job = jobs.submit("diagnostic", 42)
    other = write_dataset(
        [_episode()],
        request=GenerationRequest(18, "diagnostic", "NOT_RUN"),
        output_root=jobs.output_root,
    )
    service._write_status(
        job.work_path,
        state="complete",
        phase="complete",
        completed=1,
        total=1,
        result_path=str(other.path),
        message="",
    )
    jobs._process.returncode = 0
    assert jobs.snapshot().state == "failed"
    assert "manifest" in jobs.snapshot().message
    assert jobs.results == ()


@pytest.mark.parametrize("field", ["object_id", "model_id", "source_id", "input_id"])
def test_completed_dataset_must_match_requested_model_provenance(jobs, field):
    job = jobs.submit("diagnostic", 42)
    other = write_dataset(
        [_episode()],
        request=GenerationRequest(42, "diagnostic", "NOT_RUN"),
        output_root=jobs.output_root,
    )
    manifest_path = other.path / "manifest.csv"
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        manifest = next(reader)
    manifest[field] = "different_model_provenance"
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(manifest)
    service._write_status(
        job.work_path,
        state="complete",
        phase="complete",
        completed=1,
        total=1,
        result_path=str(other.path),
        message="",
    )
    jobs._process.returncode = 0
    assert jobs.snapshot().state == "failed"
    assert "manifest" in jobs.snapshot().message
    assert jobs.results == ()


def test_success_receipt_is_not_complete_until_worker_exits(jobs):
    job = jobs.submit("diagnostic", 42)
    service._write_status(
        job.work_path,
        state="complete",
        phase="complete",
        completed=1,
        total=1,
        result_path="",
        message="",
    )
    assert jobs.snapshot().state == "running"
    assert jobs.snapshot().phase == "finishing"


def test_cancel_persists_the_terminal_state(jobs):
    job = jobs.submit("diagnostic", 42)
    jobs.cancel()
    assert service._read_status(job.work_path)["state"] == "cancelled"


def test_receipt_replace_retries_a_temporary_windows_reader_lock(tmp_path, monkeypatch):
    original_replace = Path.replace
    attempts = []

    def locked_once(path, target):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("Windows reader briefly holds status.csv")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", locked_once)
    service._write_status(
        tmp_path,
        state="running",
        phase="simulating",
        completed=0,
        total=1,
        result_path="",
        message="",
    )
    assert len(attempts) == 2
    assert service._read_status(tmp_path)["state"] == "running"


def test_cancel_stops_only_the_owned_worker_and_allows_a_new_run(jobs):
    first = jobs.submit("pilot", 17)
    cancelled = jobs.cancel()
    assert cancelled.state == "cancelled"
    second = jobs.submit("diagnostic", 17)
    assert first.job_id != second.job_id
    assert first.work_path.is_dir()
    assert jobs.results == ()


def test_generate_progress_is_real_episode_completion(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(generate, "run_default_x8_episode", lambda seed: _episode())
    config = OmegaConf.load(service.CONFIG_ROOT / "diagnostic.yaml")
    result = generate.generate_from_config(
        config,
        output_root=tmp_path / "outputs",
        progress=lambda completed, total, phase: events.append((completed, total, phase)),
    )
    assert events[0] == (0, 1, "simulating")
    assert (1, 1, "simulating") in events
    assert events[-1] == (1, 1, "writing")
    assert result.status == "complete"
