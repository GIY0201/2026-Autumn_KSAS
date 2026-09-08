"""Run a paired, validation-only XYZ loss diagnostic from an explicit reference run."""

from __future__ import annotations

import argparse
import copy
import json
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

from .data import load_split
from .model import GRUDirectModel, metrics, resolve_config

PHASE_THRESHOLD_M = 15.0
AXIS_WEIGHTS = ((1.0, 1.0, 1.0), (1.0, 1.0, 5.0))


def phase_summary(prediction, target):
    """Descriptive groups based on observed final altitude change, not latent flight state."""
    prediction, target = np.asarray(prediction), np.asarray(target)
    assert prediction.shape == target.shape and target.shape[1:] == (75, 3)
    assert np.isfinite(prediction).all() and np.isfinite(target).all()
    dz = target[:, -1, 2]
    masks = (dz > PHASE_THRESHOLD_M, abs(dz) <= PHASE_THRESHOLD_M, dz < -PHASE_THRESHOLD_M)
    rows = []
    for name, mask in zip(("up", "level", "down"), masks, strict=True):
        rows.append(
            dict(
                phase=name,
                window_count=int(mask.sum()),
                z_rmse_m=float(np.sqrt(np.mean((prediction[mask, :, 2] - target[mask, :, 2]) ** 2)))
                if mask.any()
                else None,
                actual_dz_m=float(dz[mask].mean()) if mask.any() else None,
                predicted_dz_m=float(prediction[mask, -1, 2].mean()) if mask.any() else None,
            )
        )
    return rows


def validate_sources(expected, actual):
    for split in ("train", "validation"):
        if actual[split] != expected[split]:
            raise ValueError(f"Source mismatch: {split}")


def verify_manifest(directory):
    import csv

    with (directory / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if sha256(directory / row["path"]) != row["sha256"]:
                raise ValueError(f"Artifact hash mismatch: {directory / row['path']}")


def run_comparison(project_root, reference_run):
    reference_run = Path(reference_run).resolve()
    verify_manifest(reference_run)
    reference_manifest = sha256(reference_run / "manifest.csv")
    config = resolve_config(
        OmegaConf.to_container(
            OmegaConf.load(reference_run / "effective_config.yaml"), resolve=True
        )
    )
    config["evaluation"]["mode"] = "validation_only"
    output_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12]
    output = Path(project_root) / "outputs/visualization/v1" / output_id
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(
        output / "run.json",
        dict(
            run_id=output_id,
            reference_run=str(reference_run),
            reference_manifest_sha256=reference_manifest,
        ),
    )
    atomic_json(
        output / "protocol.json",
        dict(
            axis_weights=AXIS_WEIGHTS,
            phase_threshold_m=PHASE_THRESHOLD_M,
            seed=config["training"]["seed"],
            evaluation_mode="validation_only",
            model_selection="validation ADE",
            reference_run=str(reference_run),
            config=config,
            initialization="fresh identical seed; no resume",
            interpretation="single-seed diagnostic; no significance or optimal-weight claim",
        ),
    )
    service = TrainingService(project_root)
    directories = []
    for weights in AXIS_WEIGHTS:
        current = copy.deepcopy(config)
        current["model"]["axis_loss_weights"] = list(weights)
        run_id = service.start(current, f"Z loss diagnostic {list(weights)}")
        directory = service._run_path(run_id)
        directories.append(directory)
        atomic_json(output / "runs.json", dict(run_ids=[p.name for p in directories]))
        print(f"START {run_id} weights={weights}", flush=True)
        while True:
            status = service.status(run_id)
            if status["status"] in {"completed", "failed", "stopped"}:
                break
            time.sleep(2)
        print(f"FINISH {run_id}: {status['status']} epoch={status['epoch']}", flush=True)
        if status["status"] != "completed":
            raise RuntimeError(f"Diagnostic training did not complete: {run_id}")
        verify_manifest(directory)
        receipt = json.loads((directory / "success.json").read_text())
        assert receipt["test_evaluated"] is False
        assert not (directory / "evaluation/test_evaluation_started.json").exists()
        assert "test" not in json.loads((directory / "data_sources.json").read_text())
    matched = {}
    for name in ("window_index.csv", "normalization.json", "initialization.json"):
        fingerprints = [sha256(p / name) for p in directories]
        assert fingerprints[0] == fingerprints[1], f"Paired run mismatch: {name}"
        matched[name] = fingerprints[0]
    for name in ("window_index.csv", "normalization.json"):
        assert sha256(reference_run / name) == matched[name], f"Reference mismatch: {name}"
    atomic_json(output / "paired_hashes.json", matched)
    reference_sources = json.loads((reference_run / "data_sources.json").read_text())
    for directory in directories:
        validate_sources(
            reference_sources, json.loads((directory / "data_sources.json").read_text())
        )
    rows, phases, episode_rows = [], [], []
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    for directory, weights in zip(directories, AXIS_WEIGHTS, strict=True):
        current = copy.deepcopy(config)
        current["model"]["axis_loss_weights"] = list(weights)
        norm = json.loads((directory / "normalization.json").read_text())
        validation = load_split(Path(current["data"]["dataset_path"]), "validation", current, norm)
        if validation.source_sha256 != reference_sources["validation"]:
            raise ValueError("Validation observations changed before comparison")
        model = GRUDirectModel(current).to(current["training"]["device"])
        model.eval()
        for checkpoint in ("best", "last"):
            path = directory / "checkpoints" / checkpoint
            model.load_state_dict(load_file(str(path / "model.safetensors")))
            predictions = []
            with torch.no_grad():
                for start in range(0, len(validation), current["training"]["batch_size"]):
                    batch = torch.from_numpy(
                        validation.input_features[start : start + current["training"]["batch_size"]]
                    ).to(next(model.parameters()).device)
                    predictions.append(model(batch)["logits"].cpu().numpy())
            predicted = np.concatenate(predictions)
            identity = dict(run_id=directory.name, z_weight=weights[2], checkpoint=checkpoint)
            state = json.loads((path / "trainer_state.json").read_text())
            rows.append(
                dict(
                    **identity,
                    epoch=state["epoch"],
                    **metrics(predicted, validation.labels, norm["scale_m"]),
                )
            )
            relative = predicted * norm["scale_m"]
            phases.extend(
                dict(**identity, **row) for row in phase_summary(relative, validation.targets_m)
            )
            for episode in dict.fromkeys(row["episode_id"] for row in validation.window_rows):
                mask = np.array([row["episode_id"] == episode for row in validation.window_rows])
                episode_rows.append(
                    dict(
                        **identity,
                        episode_id=episode,
                        **metrics(predicted[mask], validation.labels[mask], norm["scale_m"]),
                    )
                )
        del model
    for name, records in (
        ("comparison.csv", rows),
        ("phases.csv", phases),
        ("episodes.csv", episode_rows),
    ):
        atomic_csv(output / name, records, list(records[0]))
    make_figures(output, directories, rows)
    if sha256(reference_run / "manifest.csv") != reference_manifest:
        raise ValueError("Reference manifest changed")
    verify_manifest(reference_run)
    atomic_json(
        output / "success.json",
        dict(
            status="completed",
            test_evaluated=False,
            compared_checkpoints=4,
            run_ids=[p.name for p in directories],
        ),
    )
    write_manifest(output)
    print(f"COMPARISON {output}", flush=True)
    print(json.dumps(rows, indent=2), flush=True)
    return output


def make_figures(output, directories, rows):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(rows=1, cols=2, subplot_titles=("Validation ADE", "Validation Z RMSE"))
    for directory, weights in zip(directories, AXIS_WEIGHTS, strict=True):
        logs = [
            json.loads(line) for line in (directory / "logs/metrics.jsonl").read_text().splitlines()
        ]
        logs = [r for r in logs if "eval_ade_m" in r]
        for col, key in enumerate(("eval_ade_m", "eval_axis_rmse_z_m"), 1):
            figure.add_trace(
                go.Scatter(
                    x=[r["epoch"] for r in logs],
                    y=[r[key] for r in logs],
                    name=f"Z weight {weights[2]:g}",
                    line=dict(color="#0072B2" if weights[2] == 1 else "#D55E00"),
                    legendgroup=str(weights[2]),
                    showlegend=col == 1,
                ),
                1,
                col,
            )
            figure.update_xaxes(title_text="Epoch", row=1, col=col)
            figure.update_yaxes(title_text="Error (m)", row=1, col=col)
    figure.update_layout(
        template="plotly_white",
        width=1200,
        height=470,
        title="Paired loss diagnostic | validation only | seed 17",
    )
    figure.write_image(str(output / "training_curves.png"), scale=1.5)
    figure.write_html(str(output / "training_curves.html"), include_plotlyjs=True)
    bars = go.Figure()
    for checkpoint in ("best", "last"):
        selected = [r for r in rows if r["checkpoint"] == checkpoint]
        bars.add_bar(
            x=[f"Z weight {r['z_weight']:g}" for r in selected],
            y=[r["axis_rmse_z_m"] for r in selected],
            name=checkpoint,
        )
    bars.update_layout(
        template="plotly_white",
        width=850,
        height=480,
        barmode="group",
        title="Validation Z RMSE | lower is better",
        yaxis_title="RMSE (m)",
    )
    bars.write_image(str(output / "checkpoint_comparison.png"), scale=1.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-run", type=Path, required=True)
    args = parser.parse_args()
    run_comparison(Path(__file__).resolve().parents[3], args.reference_run)


if __name__ == "__main__":
    main()
