"""Post-forward position evidence and separate generator-event annotations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from models.runtime.v1.io import atomic_csv, atomic_json, sha256

from .model import DT_S, FUTURE_SAMPLES, HISTORY_SAMPLES

MOVEMENT_THRESHOLD_M = 15.0
SUBSET_PER_GROUP = 2


def select_indices(dataset):
    """Choose up to two windows per observed movement group in sequence-ID order."""
    groups = {}
    for index in range(len(dataset.window_rows)):
        target = dataset.targets_m[index]
        xy = (
            "moving"
            if np.linalg.norm(target[:, :2], axis=-1).max() > MOVEMENT_THRESHOLD_M
            else "small"
        )
        dz = target[-1, 2]
        vertical = (
            "up" if dz > MOVEMENT_THRESHOLD_M else "down" if dz < -MOVEMENT_THRESHOLD_M else "level"
        )
        groups.setdefault((xy, vertical), []).append(index)
    return [
        index
        for group in sorted(groups)
        for index in sorted(groups[group], key=lambda i: dataset.window_rows[i]["sequence_id"])[
            :SUBSET_PER_GROUP
        ]
    ]


def _events(dataset_path, episodes):
    source = Path(dataset_path) / "evaluation/commands.csv" if dataset_path else None
    if source is None or not source.is_file():
        return {}, {"commands_status": "unavailable", "commands_sha256": None}
    events = {}
    with source.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["episode_id"] not in episodes:
                continue
            start, end = float(row["start_s"]), float(row["end_s"])
            if not np.isfinite([start, end]).all() or end < start:
                raise ValueError("Invalid generator event interval")
            events.setdefault(row["episode_id"], []).append(
                {"event_id": row["event_id"], "start_s": start, "end_s": end}
            )
    return events, {
        "commands_status": "available",
        "commands_sha256": sha256(source),
        "commands_path": str(source.resolve()),
        "annotation_policy": "post-forward only; half-open [start,end); point events at start",
    }


def write_diagnostics(
    output_dir,
    dataset,
    predictions_normalized,
    scale_m,
    *,
    run_id,
    checkpoint,
    epoch,
    indices=None,
    dataset_path=None,
):
    """Export already-computed predictions; this function never calls a model or split reader."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not hasattr(dataset, "window_rows"):
        receipt = {"status": "unsupported", "reason": "dataset lacks window lineage"}
        atomic_json(out / "provenance.json", receipt)
        return receipt
    selected = list(range(len(dataset))) if indices is None else list(indices)
    predictions = np.asarray(predictions_normalized, dtype=np.float64)
    if predictions.shape != (len(selected), FUTURE_SAMPLES, 3):
        raise ValueError("Diagnostic prediction shape does not match selected windows")
    if not np.isfinite(predictions).all() or not np.isfinite(scale_m) or scale_m <= 0:
        raise ValueError("Nonfinite diagnostic prediction or scale")
    selected_rows = [dataset.window_rows[index] for index in selected]
    events, provenance = _events(dataset_path, {r["episode_id"] for r in selected_rows})
    points, windows = [], []
    for prediction, index, row in zip(predictions, selected, selected_rows, strict=True):
        identity = {
            "run_id": run_id,
            "checkpoint": checkpoint,
            "epoch": epoch,
            **{
                key: row[key]
                for key in (
                    "split",
                    "episode_id",
                    "sequence_id",
                    "variant_id",
                    "start_step",
                    "end_step",
                )
            },
        }
        anchor_time = float(dataset.timestamps_s[index, -1])
        relative = prediction * scale_m
        target = dataset.targets_m[index]
        error = relative - target
        distance = np.linalg.norm(error, axis=-1)
        excursion = float(np.linalg.norm(target[:, :2], axis=-1).max())
        dz = float(target[-1, 2])
        episode_events = events.get(row["episode_id"], [])
        start_time = float(dataset.timestamps_s[index, 0])
        end_time = anchor_time + FUTURE_SAMPLES * DT_S
        transitions = []
        for event in episode_events:
            for boundary, kind in (("start_s", "enter"), ("end_s", "exit")):
                if start_time <= event[boundary] <= end_time:
                    transitions.append(
                        {"t_s": event[boundary], "event_id": event["event_id"], "transition": kind}
                    )
        transitions.sort(key=lambda item: (item["t_s"], item["event_id"], item["transition"]))
        windows.append(
            {
                **identity,
                "anchor_t_s": anchor_time,
                "ade_m": float(distance.mean()),
                "fde_m": float(distance[-1]),
                **{
                    f"axis_rmse_{axis}_m": float(np.sqrt(np.mean(error[:, j] ** 2)))
                    for j, axis in enumerate("xyz")
                },
                "observed_xy_movement": "moving" if excursion > MOVEMENT_THRESHOLD_M else "small",
                "observed_xy_max_excursion_m": excursion,
                "observed_vertical": "up"
                if dz > MOVEMENT_THRESHOLD_M
                else "down"
                if dz < -MOVEMENT_THRESHOLD_M
                else "level",
                "observed_dz_m": dz,
                "generator_transitions": json.dumps(transitions, ensure_ascii=False),
            }
        )
        observed = np.concatenate(
            (dataset.input_positions_m[index], target + dataset.anchors[index])
        )
        times = np.concatenate(
            (dataset.timestamps_s[index], anchor_time + DT_S * np.arange(1, FUTURE_SAMPLES + 1))
        )
        for sample, (position, timestamp) in enumerate(zip(observed, times, strict=True)):
            future = sample >= HISTORY_SAMPLES
            offset = sample - HISTORY_SAMPLES
            active = [
                event["event_id"]
                for event in episode_events
                if event["start_s"] <= timestamp < event["end_s"]
                or event["start_s"] == event["end_s"] == timestamp
            ]
            points.append(
                {
                    **identity,
                    "sample_role": "future" if future else "input",
                    "sample_index": sample,
                    "step": int(row["start_step"]) + sample,
                    "t_s": float(timestamp),
                    "horizon_s": float(timestamp - anchor_time),
                    **{f"observed_{axis}_m": float(position[j]) for j, axis in enumerate("xyz")},
                    **{
                        f"predicted_{axis}_m": float(
                            relative[offset, j] + dataset.anchors[index, j]
                        )
                        if future
                        else None
                        for j, axis in enumerate("xyz")
                    },
                    **{
                        f"error_{axis}_m": float(error[offset, j]) if future else None
                        for j, axis in enumerate("xyz")
                    },
                    "error_3d_m": float(distance[offset]) if future else None,
                    "generator_event_ids": json.dumps(active, ensure_ascii=False),
                    "generator_annotation_status": "known" if active else "unknown",
                }
            )
    for name, records in (("points.csv", points), ("windows.csv", windows)):
        if not records:
            raise ValueError("No diagnostic windows selected")
        atomic_csv(out / name, records, list(records[0]))
    receipt = {
        "status": "completed",
        "window_count": len(selected),
        "point_count": len(points),
        "observation_sha256": dataset.source_sha256,
        "coordinate_frame": "absolute ENU meters",
        "movement_threshold_m": MOVEMENT_THRESHOLD_M,
        "future_timestamp_policy": "validated fixed DT_S grid after last native input timestamp",
        **provenance,
    }
    atomic_json(out / "provenance.json", receipt)
    return receipt
