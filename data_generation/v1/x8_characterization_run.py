"""Explicit-config CLI for immutable, evaluation-only X8 reference CSV batches."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import platform
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from omegaconf import OmegaConf

from . import control, scenario, x8, x8_characterization
from .x8_characterization import TrialCase, TrialSettings, run_trial, validate_trial

SCHEMA_VERSION = "x8-characterization-v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> list[dict[str, str]]:
    paths = [Path(m.__file__).resolve() for m in (control, scenario, x8, x8_characterization)]
    paths.append(Path(__file__).resolve())
    project = Path(__file__).resolve().parents[2]
    paths.extend(project / name for name in ("pyproject.toml", "uv.lock"))
    return [{"path": p.relative_to(project).as_posix(), "sha256": _sha(p)} for p in paths]


def _write_csv(path: Path, rows: list[dict], *, fields: list[str] | None = None) -> None:
    columns = fields if fields is not None else list(rows[0])
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run_batch(config_path: Path, output_root: Path) -> Path:
    """Validate first, keep every trial outcome, and never issue a public dataset."""
    config_path = Path(config_path).resolve()
    config_bytes = config_path.read_bytes()
    config_text = config_bytes.decode("utf-8")
    config = OmegaConf.to_container(
        OmegaConf.create(config_text), resolve=True, throw_on_missing=True
    )
    if not isinstance(config, dict) or set(config) != {
        "schema_version",
        "settings",
        "cases",
        "provenance",
    }:
        raise ValueError("config requires schema_version, settings, cases, and provenance")
    if config["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported characterization schema_version")
    if not isinstance(config["provenance"], dict) or not config["provenance"]:
        raise ValueError("provenance must be a nonempty mapping")
    settings = TrialSettings(**config["settings"])
    if not isinstance(config["cases"], list) or not config["cases"]:
        raise ValueError("cases must be a nonempty list")
    cases = [TrialCase(**case) for case in config["cases"]]
    for case in cases:
        validate_trial(case, settings)
    if len({c.trial_id for c in cases}) != len(cases):
        raise ValueError("trial_id values must be unique")
    hashes = _source_hashes()
    code_hash = hashlib.sha256(
        "\n".join(f"{r['path']}:{r['sha256']}" for r in hashes).encode()
    ).hexdigest()
    run_id = str(uuid.uuid4())
    root = Path(output_root).resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    evaluation = root / "evaluation"
    evaluation.mkdir()
    (root / "input_config.yaml").write_bytes(config_bytes)
    OmegaConf.save(OmegaConf.create(config), root / "effective_config.yaml")
    OmegaConf.save(OmegaConf.create(asdict(x8.X8Parameters())), root / "model_parameters.yaml")
    (root / "environment.txt").write_text(
        "\n".join(
            [
                f"python={sys.version}",
                f"executable={sys.executable}",
                f"platform={platform.platform()}",
                *[
                    f"{name}={importlib.metadata.version(name)}"
                    for name in ("numpy", "scipy", "omegaconf")
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(root / "source_hashes.csv", hashes)
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.trial_id}: {case.label}", flush=True)
        result = run_trial(case, settings)
        results.append(result)
        print(
            f"  {result.status}; samples={len(result.samples)}; {result.failure_reason or ''}",
            flush=True,
        )
    # No generation/profile files are updated; only this new reference output is written.
    rows = [row for result in results for row in result.samples]
    _write_csv(
        evaluation / "samples.csv", rows, fields=list(rows[0]) if rows else ["trial_id", "t_s"]
    )
    _write_csv(evaluation / "summary.csv", [r for result in results for r in result.summary])
    _write_csv(
        evaluation / "trials.csv",
        [
            {
                **asdict(result.case),
                "status": result.status,
                "failure_reason": result.failure_reason,
                "sample_count": len(result.samples),
            }
            for result in results
        ],
    )
    code_unchanged = hashes == _source_hashes()
    failed = sum(result.status != "complete" for result in results)
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "data_role": "REFERENCE_SIMULATION",
        "validation_status": "REPORT_ONLY",
        "status": (
            "failed"
            if not code_unchanged or failed == len(cases)
            else ("partial" if failed else "complete")
        ),
        "failure_reason": "code_changed_during_run" if not code_unchanged else "",
        "code_version": "v1",
        "format_version": SCHEMA_VERSION,
        "input_data_id": "none; autonomous synthetic reference experiment",
        "source_id": "x8_low_hansen_2025",
        "model_id": "x8_python_6dof_reference",
        "source_doi": "10.1007/s13272-025-00816-3",
        "seed": "not_applicable; deterministic",
        "trial_count": len(cases),
        "failed_trial_count": failed,
        "code_hash": code_hash,
        "code_unchanged": code_unchanged,
        "input_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "config_path": "effective_config.yaml",
        "environment_path": "environment.txt",
        "parameter_path": "model_parameters.yaml",
        "source_hashes_path": "source_hashes.csv",
        "frame": "LOCAL_ENU",
        "acceleration_method": "R*(body_v_dot+omega_cross_body_v)",
        "control_dt_s": scenario.CONTROL_DT_S,
        "physics_dt_s": settings.physics_dt_s,
        "record_dt_s": settings.record_dt_s,
        "wind": "zero",
    }
    _write_csv(root / "manifest.csv", [manifest])
    _write_csv(
        root / "files.csv",
        [
            {"path": p.relative_to(root).as_posix(), "sha256": _sha(p)}
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ],
    )
    print(f"reference_output={root}; status={manifest['status']}", flush=True)
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = run_batch(args.config, args.output_root)
    with (root / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        status = next(csv.DictReader(stream))["status"]
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
