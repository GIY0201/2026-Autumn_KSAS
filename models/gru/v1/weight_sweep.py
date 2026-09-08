"""Serial multi-seed validation-only loss-weight comparison with frozen windows."""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
from omegaconf import OmegaConf
from safetensors.torch import load_file

from models.runtime.v1.io import atomic_csv, atomic_json, sha256, write_manifest
from models.runtime.v1.service import TrainingService

from .ablation import phase_summary, validate_sources, verify_manifest
from .data import load_split
from .model import GRUDirectModel, metrics, resolve_config

METRICS = ("ade_m", "fde_m", "axis_rmse_x_m", "axis_rmse_y_m", "axis_rmse_z_m")


def build_config(reference: dict, weight: float, seed: int, window_seed: int) -> dict:
    """Vary initialization and loss ratio while keeping sampling fixed."""
    config = copy.deepcopy(reference)
    config["model"]["axis_loss_weights"] = [1.0, 1.0, float(weight)]
    config["training"]["seed"] = seed
    config["data"]["window_seed"] = window_seed
    config["evaluation"]["mode"] = "validation_only"
    return config


def summarize(rows: list[dict], weights: list, seeds: list) -> tuple[list[dict], float]:
    """Select by equal-seed mean best ADE; report sample standard deviations."""
    expected = {(w, s, c) for w in weights for s in seeds for c in ("best", "last")}
    actual = [(r["z_weight"], r["seed"], r["checkpoint"]) for r in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("A complete unique weight/seed/checkpoint grid is required")
    summary = []
    for weight in weights:
        for checkpoint in ("best", "last"):
            selected = [
                r for r in rows if r["z_weight"] == weight and r["checkpoint"] == checkpoint
            ]
            result = dict(z_weight=weight, checkpoint=checkpoint, seed_count=len(selected))
            for metric in METRICS:
                values = np.asarray([r[metric] for r in selected], dtype=float)
                if not np.isfinite(values).all() or np.any(values < 0):
                    raise ValueError(f"Invalid {metric} measurements")
                result[f"{metric}_mean"] = float(values.mean())
                result[f"{metric}_std"] = float(values.std(ddof=1)) if len(values) > 1 else 0.0
            summary.append(result)
    best = min(
        (r for r in summary if r["checkpoint"] == "best"),
        key=lambda r: (r["ade_m_mean"], r["z_weight"]),
    )
    return summary, best["z_weight"]


def evaluate_run(directory: Path, config: dict, expected_sources: dict) -> tuple[list, list]:
    """Read only validation observations for both explicitly named checkpoints."""
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.enabled = config["training"].get("cudnn_enabled", True)
    normalization = json.loads((directory / "normalization.json").read_text())
    validation = load_split(
        Path(config["data"]["dataset_path"]), "validation", config, normalization
    )
    if validation.source_sha256 != expected_sources["validation"]:
        raise ValueError("Validation observations changed before comparison")
    model = GRUDirectModel(config).to(config["training"]["device"]).eval()
    rows, phases = [], []
    for checkpoint in ("best", "last"):
        path = directory / "checkpoints" / checkpoint
        model.load_state_dict(load_file(str(path / "model.safetensors")))
        predictions = []
        with torch.no_grad():
            for start in range(0, len(validation), config["training"]["batch_size"]):
                features = validation.input_features[
                    start : start + config["training"]["batch_size"]
                ]
                batch = torch.from_numpy(features).to(next(model.parameters()).device)
                predictions.append(model(batch)["logits"].cpu().numpy())
        predicted = np.concatenate(predictions)
        state = json.loads((path / "trainer_state.json").read_text())
        identity = dict(
            run_id=directory.name,
            seed=config["training"]["seed"],
            z_weight=config["model"]["axis_loss_weights"][2],
            checkpoint=checkpoint,
        )
        values = metrics(predicted, validation.labels, normalization["scale_m"])
        logged_ade = validate_logged_ade(directory, state["global_step"], values["ade_m"])
        rows.append(
            dict(
                **identity,
                epoch=state["epoch"],
                worker_ade_m=logged_ade,
                ade_replay_difference_m=abs(logged_ade - values["ade_m"]),
                **values,
            )
        )
        phases.extend(
            dict(**identity, **row)
            for row in phase_summary(predicted * normalization["scale_m"], validation.targets_m)
        )
    return rows, phases


def validate_logged_ade(directory: Path, step: int, actual: float) -> float:
    """Require FP32 replay to match the saved checkpoint's validation evaluation."""
    logs = [
        json.loads(line) for line in (directory / "logs/metrics.jsonl").read_text().splitlines()
    ]
    selected = [row for row in logs if row.get("global_step") == step and "eval_ade_m" in row]
    if not selected:
        raise ValueError(f"Missing validation ADE log at step {step}")
    expected = float(selected[-1]["eval_ade_m"])
    if not np.isfinite(expected) or not np.isclose(actual, expected, rtol=1e-5, atol=1e-4):
        raise ValueError(f"FP32 checkpoint replay ADE mismatch: {actual} versus {expected}")
    return expected


def _write_rows(path: Path, rows: list[dict]) -> None:
    atomic_csv(path, rows, list(rows[0]))


def run_sweep(project_root: Path, reference_run: Path) -> Path:
    """Execute the versioned protocol in new runs, fail closed on evidence mismatch."""
    reference_run = reference_run.resolve()
    verify_manifest(reference_run)
    reference_manifest = sha256(reference_run / "manifest.csv")
    config = resolve_config(
        OmegaConf.to_container(
            OmegaConf.load(reference_run / "effective_config.yaml"), resolve=True
        )
    )
    protocol_path = Path(__file__).parent / "configs/weight_sweep.yaml"
    protocol = OmegaConf.to_container(OmegaConf.load(protocol_path), resolve=True)
    weights, seeds = protocol["z_weights"], protocol["seeds"]
    sources = json.loads((reference_run / "data_sources.json").read_text())
    expected = {
        name: sha256(reference_run / name) for name in ("window_index.csv", "normalization.json")
    }
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12]
    output = project_root / "outputs/visualization/v1" / identifier
    output.mkdir(parents=True, exist_ok=False)
    protocol.update(
        reference_run=str(reference_run),
        reference_manifest_sha256=reference_manifest,
        protocol_source_sha256=sha256(protocol_path),
        reference_config=config,
        test_evaluated=False,
        evaluation_mode="validation_only",
        initialization="fresh per seed; no resume",
        expected_data_hashes=expected,
    )
    atomic_json(output / "protocol.json", protocol)
    atomic_json(output / "run.json", dict(run_id=identifier, reference_run=str(reference_run)))
    service = TrainingService(project_root)
    initialization_hashes, records, rows, phases = {}, [], [], []
    try:
        for seed in seeds:
            for weight in weights:
                current = build_config(config, weight, seed, protocol["window_seed"])
                current["training"]["cudnn_enabled"] = protocol["cudnn_enabled"]
                run_id = service.start(current, f"Z weight {weight} / seed {seed}")
                directory = service._run_path(run_id)
                records.append(dict(run_id=run_id, seed=seed, z_weight=weight))
                atomic_json(output / "runs.json", records)
                print(f"START {run_id} weight={weight} seed={seed}", flush=True)
                while True:
                    status = service.status(run_id)
                    if status["status"] in {"completed", "stopped", "failed"}:
                        break
                    time.sleep(2)
                if status["status"] != "completed":
                    raise RuntimeError(f"Sweep run {run_id}: {status}")
                # The terminal status precedes the last manifest publication in the child.
                process = service._processes[run_id]
                exit_code = process.wait(timeout=60)
                if exit_code != 0:
                    raise RuntimeError(
                        f"Worker exited unsuccessfully after terminal status: {run_id}; "
                        f"exit_code={exit_code} (0x{exit_code & 0xffffffff:08X})"
                    )
                verify_manifest(directory)
                receipt = json.loads((directory / "success.json").read_text())
                if receipt.get("test_evaluated") is not False:
                    raise ValueError("Validation-only receipt required")
                observed_sources = json.loads((directory / "data_sources.json").read_text())
                if (
                    "test" in observed_sources
                    or (directory / "evaluation/test_evaluation_started.json").exists()
                ):
                    raise ValueError("Unexpected test access")
                validate_sources(sources, observed_sources)
                for name, fingerprint in expected.items():
                    if sha256(directory / name) != fingerprint:
                        raise ValueError(f"Frozen data artifact mismatch: {name}")
                initial = json.loads((directory / "initialization.json").read_text())[
                    "state_sha256"
                ]
                if seed in initialization_hashes and initialization_hashes[seed] != initial:
                    raise ValueError(f"Initialization differs within seed {seed}")
                initialization_hashes[seed] = initial
                new_rows, new_phases = evaluate_run(directory, current, sources)
                rows.extend(new_rows)
                phases.extend(new_phases)
                _write_rows(output / "comparison.csv", rows)
                _write_rows(output / "phases.csv", phases)
                print(f"FINISH {run_id}", flush=True)
        summary, selected = summarize(rows, weights, seeds)
        _write_rows(output / "summary.csv", summary)
        atomic_json(output / "initialization_hashes.json", initialization_hashes)
        atomic_json(
            output / "selection.json",
            dict(
                z_weight=selected,
                criterion="equal-seed mean best validation ADE; ties lower weight",
            ),
        )
        report = [
            "# Z loss weight sweep",
            "",
            "Validation only; test not evaluated.",
            "Sample standard deviations describe initialization variability on fixed windows.",
            "No p-values, statistical generalization, or optimal-weight claim.",
            "",
            f"Selected Z weight: {selected:g} (mean best validation ADE).",
            "",
            "| Weight | Checkpoint | ADE mean ± SD | X RMSE | Y RMSE | Z RMSE |",
            "|---:|---|---:|---:|---:|---:|",
        ]
        for row in summary:
            report.append(
                f"| {row['z_weight']:g} | {row['checkpoint']} | "
                f"{row['ade_m_mean']:.3f} ± {row['ade_m_std']:.3f} | "
                f"{row['axis_rmse_x_m_mean']:.3f} | {row['axis_rmse_y_m_mean']:.3f} | "
                f"{row['axis_rmse_z_m_mean']:.3f} |"
            )
        (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        if sha256(reference_run / "manifest.csv") != reference_manifest:
            raise ValueError("Reference manifest changed")
        verify_manifest(reference_run)
        atomic_json(
            output / "success.json",
            dict(
                status="completed",
                test_evaluated=False,
                run_count=len(records),
                selected_z_weight=selected,
            ),
        )
    except Exception as error:
        atomic_json(output / "failed.json", dict(status="failed", error=str(error)))
        write_manifest(output)
        raise
    write_manifest(output)
    print(f"SWEEP {output}", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-run", type=Path, required=True)
    args = parser.parse_args()
    run_sweep(Path(__file__).resolve().parents[3], args.reference_run)


if __name__ == "__main__":
    main()
