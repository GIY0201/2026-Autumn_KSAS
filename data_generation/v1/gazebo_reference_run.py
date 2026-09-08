"""Immutable, evaluation-only CSV writer for the Gazebo-derived force trials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import platform
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from omegaconf import OmegaConf

from . import gazebo_reference, gazebo_trials
from .gazebo_reference import load_catalog
from .gazebo_trials import cases_from_config, run_trial, validate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict]):
    if not rows:
        raise ValueError("cannot write empty result table")
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_batch(config_path: Path, output_root: Path) -> Path:
    config_path = Path(config_path).resolve()
    config_bytes = config_path.read_bytes()
    models, config = load_catalog(config_path)
    cases = cases_from_config(config)
    if not cases:
        raise ValueError("no trial cases")
    for case in cases:
        validate(case, config["settings"], config["controller"], models[case["model_id"]])
    source_root = (config_path.parent / config["source_root"]).resolve()
    project = Path(__file__).resolve().parents[2]
    code_paths = [
        Path(gazebo_reference.__file__),
        Path(gazebo_trials.__file__),
        Path(__file__),
        project / "pyproject.toml",
        project / "uv.lock",
    ]
    hashes = {str(p): _sha(p) for p in code_paths}
    source_hashes = {str(source_root / p): _sha(source_root / p) for p in config["sources"]}
    root = Path(output_root).resolve() / str(uuid.uuid4())
    evaluation = root / "evaluation"
    evaluation.mkdir(parents=True, exist_ok=False)
    (root / "input_config.yaml").write_bytes(config_bytes)
    OmegaConf.save(OmegaConf.create(config), root / "effective_config.yaml")
    OmegaConf.save(
        OmegaConf.create({key: asdict(value) for key, value in models.items()}),
        root / "model_parameters.yaml",
    )
    (root / "environment.txt").write_text(
        f"python={sys.version}\nexecutable={sys.executable}\nplatform={platform.platform()}\n",
        encoding="utf-8",
    )
    _write(root / "source_hashes.csv", [dict(path=k, sha256=v) for k, v in source_hashes.items()])
    _write(root / "code_hashes.csv", [dict(path=k, sha256=v) for k, v in hashes.items()])
    summaries = []
    # Stream per-case samples so the CSV already contains completed cases on interruption.
    sample_path = evaluation / "samples.csv"
    with sample_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = None
        for index, case in enumerate(cases, 1):
            result = run_trial(
                models[case["model_id"]], case, config["settings"], config["controller"]
            )
            summary = result["summary"]
            summaries.append(summary)
            if result["samples"]:
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(result["samples"][0]))
                    writer.writeheader()
                writer.writerows(result["samples"])
                stream.flush()
            print(
                f"[{index}/{len(cases)}] {case['trial_id']}: {summary['status']}, "
                f"target_met={summary['target_met']}",
                flush=True,
            )
    _write(evaluation / "combinations.csv", summaries)
    _write(evaluation / "commands.csv", cases)
    group_rows = []
    for model_id, model in models.items():
        for mode in model.definition["modes"]:
            selected = [s for s in summaries if s["model_id"] == model_id and s["mode"] == mode]
            group_rows.append(
                dict(
                    model_id=model_id,
                    label=model.definition["label"],
                    mode=mode,
                    tested=len(selected),
                    numerical_complete=sum(s["status"] == "complete" for s in selected),
                    target_met=sum(s["target_met"] for s in selected),
                )
            )
    _write(evaluation / "overview.csv", group_rows)
    unchanged = (
        all(_sha(Path(k)) == v for k, v in hashes.items())
        and all(_sha(Path(k)) == v for k, v in source_hashes.items())
        and config_path.read_bytes() == config_bytes
    )
    failures = sum(s["status"] != "complete" for s in summaries)
    manifest = dict(
        run_id=root.name,
        created_at_utc=datetime.now(UTC).isoformat(),
        data_role="REFERENCE_SIMULATION",
        evidence="GAZEBO_DERIVED_REDUCED_FORCE_NOT_FULL_SIMULATOR",
        format_version=config["schema_version"],
        code_version="v1",
        status="failed" if not unchanged else "partial" if failures else "complete",
        trial_count=len(cases),
        numerical_failures=failures,
        target_met_count=sum(s["target_met"] for s in summaries),
        files_unchanged_during_run=unchanged,
        input_data_id="pinned_gazebo_reference_2026",
        seed="not_applicable; deterministic",
        code_hash=hashlib.sha256(str(sorted(hashes.items())).encode()).hexdigest(),
        config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        config_path="effective_config.yaml",
        environment_path="environment.txt",
        source_path="source_hashes.csv",
    )
    _write(root / "manifest.csv", [manifest])
    inventory = [
        dict(path=p.relative_to(root).as_posix(), sha256=_sha(p))
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]
    _write(root / "files.csv", inventory)
    if not unchanged:
        raise RuntimeError(f"sources changed during run; failed output preserved at {root}")
    print(root, flush=True)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    run_batch(args.config, args.output_root)


if __name__ == "__main__":
    main()
