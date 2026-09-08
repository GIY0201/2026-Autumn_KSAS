"""Evaluation-only transition probes using the unchanged point-mass integrator.

This is an external target schedule, not an aircraft controller or proof of
physical stability. All speed/acceleration/jerk budgets are experimental inputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
import uuid
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf

from .point_mass import (
    PointMassLimits,
    PointMassState,
    PointMassTarget,
    advance_point_mass,
)


def smooth_fraction(fraction: float) -> float:
    """Quintic interpolation with zero first/second endpoint derivatives."""
    if not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError("fraction outside [0, 1]")
    return fraction**3 * (10 + fraction * (-15 + 6 * fraction))


def run_probe(case: dict, dt: float, interpolation: str = "quintic") -> dict:
    """Integrate a continuous position history and independently difference it."""
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("dt must be finite and positive")
    if interpolation not in ("quintic", "step"):
        raise ValueError("unknown interpolation")
    if not case["segments"] or case["padding_s"] <= 0:
        raise ValueError("segments and positive padding required")
    limits = PointMassLimits(**case["limits"])
    start = np.asarray(case["initial_target"], dtype=float)
    state = PointMassState(np.asarray(case["initial_position"], dtype=float),
                           start[0], 0.0, start[1], start[2])
    times, positions, velocities, targets, phases = [], [], [], [], []

    def record(t, target, phase):
        times.append(t)
        positions.append(state.position_enu_m.copy())
        velocities.append([state.horizontal_speed_mps * math.cos(state.track_heading_rad),
                           state.horizontal_speed_mps * math.sin(state.track_heading_rad),
                           state.vertical_speed_mps])
        targets.append(target.copy())
        phases.append(phase)

    record(0.0, start, "initial_hold")
    segments = [dict(label="initial_hold", duration_s=case["padding_s"], target=start)]
    segments += case["segments"]
    segments += [dict(label="final_hold", duration_s=case["padding_s"],
                      target=case["segments"][-1]["target"])]
    elapsed = 0.0
    for segment in segments:
        duration = float(segment["duration_s"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid segment duration")
        end = np.asarray(segment["target"], dtype=float)
        if end.shape != (3,) or not np.isfinite(end).all():
            raise ValueError("invalid target")
        steps = max(2, math.ceil(duration / dt))
        step = duration / steps
        for index in range(1, steps + 1):
            blend = smooth_fraction(index / steps) if interpolation == "quintic" else 1.0
            target = start + blend * (end - start)
            integration_target = target.copy()
            if interpolation == "quintic":
                # The existing integrator holds turn rate across the interval,
                # but moves speed to its endpoint. Evaluate turn at midpoint.
                integration_target[2] = start[2] + smooth_fraction(
                    (index - 0.5) / steps
                ) * (end[2] - start[2])
            state = advance_point_mass(
                state, PointMassTarget(*integration_target), limits, step
            )
            record(elapsed + index * step, target, segment["label"])
        start = end
        elapsed += duration
    t = np.asarray(times)
    p, v, target = map(np.asarray, (positions, velocities, targets))
    acceleration = np.gradient(v, t, axis=0, edge_order=2)
    jerk = np.gradient(acceleration, t, axis=0, edge_order=2)
    position_velocity = np.gradient(p, t, axis=0, edge_order=2)
    actual_hw = np.column_stack((np.linalg.norm(v[:, :2], axis=1), v[:, 2]))
    tracking = float(np.max(np.linalg.norm(actual_hw - target[:, :2], axis=1)))
    peak_horizontal = float(np.max(np.linalg.norm(acceleration[:, :2], axis=1)))
    peak_vertical = float(np.max(np.abs(acceleration[:, 2])))
    peak_jerk = float(np.max(np.linalg.norm(jerk, axis=1)))
    residual = float(np.max(np.linalg.norm(position_velocity - v, axis=1)))
    tol = case["numeric_tolerance"]
    checks = dict(
        horizontal_acceleration=peak_horizontal <= limits.horizontal_acceleration_max_mps2 + tol,
        vertical_acceleration=peak_vertical <= limits.vertical_acceleration_max_mps2 + tol,
        jerk=peak_jerk <= case["jerk_limit_mps3"] + tol,
        tracking=tracking <= case["tracking_tolerance_mps"],
        position_velocity=residual <= case["position_fd_tolerance_mps"],
        above_ground=float(p[:, 2].min()) >= -tol,
    )
    if "expected_final_altitude_m" in case:
        checks["final_altitude"] = abs(p[-1, 2] - case["expected_final_altitude_m"]) <= tol
    assert np.isfinite(np.concatenate((p, v, acceleration, jerk), axis=1)).all()
    summary = dict(
        case_id=case["case_id"], label=case["label"], object_id=case["object_id"],
        interpolation=interpolation, dt_s=dt, duration_s=float(t[-1]),
        passed=all(checks.values()), failed_checks=";".join(k for k, ok in checks.items() if not ok),
        peak_horizontal_acceleration_mps2=peak_horizontal,
        peak_vertical_acceleration_mps2=peak_vertical, peak_jerk_mps3=peak_jerk,
        max_tracking_error_mps=tracking, position_velocity_error_mps=residual,
        max_adjacent_acceleration_change_mps2=float(np.max(np.linalg.norm(np.diff(acceleration, axis=0), axis=1))),
        final_altitude_m=float(p[-1, 2]), final_speed_mps=float(np.linalg.norm(v[-1])),
    )
    return dict(summary=summary, time=t, position=p, velocity=v,
                acceleration=acceleration, jerk=jerk, target=target, phase=phases)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_batch(config_path: Path, output_root: Path) -> Path:
    config_path = config_path.resolve()
    config = OmegaConf.to_container(OmegaConf.load(config_path), resolve=True)
    if config["schema_version"] != "transition-probe-v1":
        raise ValueError("unsupported probe schema")
    time_steps = config["time_steps_s"]
    if len(time_steps) != 2 or not 0 < time_steps[1] < time_steps[0]:
        raise ValueError("exactly two decreasing positive time steps required")
    inputs = [config_path, Path(__file__).resolve(), Path(__file__).with_name("point_mass.py")]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    root = output_root.resolve() / str(uuid.uuid4())
    evaluation = root / "evaluation"
    evaluation.mkdir(parents=True, exist_ok=False)
    (root / "configuration.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "environment.json").write_text(json.dumps(dict(python=sys.version, platform=platform.platform(), numpy=np.__version__)), encoding="utf-8")
    write_csv(root / "inputs.csv", [dict(path=k, sha256=v) for k, v in hashes.items()])
    summaries, traces, refinements = [], [], []
    for original in config["cases"]:
        case = {**config["defaults"], **original}
        modes = ["quintic", "step"] if case.get("negative_control") else ["quintic"]
        for mode in modes:
            mode_results = []
            for dt in config["time_steps_s"]:
                result = run_probe(case, dt, mode)
                result["summary"]["expected_pass"] = case.get("expected_pass", True) if mode == "quintic" else False
                summaries.append(result["summary"])
                mode_results.append(result)
                for index, t in enumerate(result["time"]):
                    row = dict(case_id=case["case_id"], label=case["label"],
                               interpolation=mode, dt_s=dt, t_s=t, phase=result["phase"][index])
                    for field, prefix, unit in [("position", "", "m"), ("velocity", "v", "mps"),
                                                ("acceleration", "a", "mps2"), ("jerk", "j", "mps3")]:
                        row.update({f"{prefix}{axis}_{unit}": value for axis, value in zip("xyz", result[field][index])})
                    traces.append(row)
            coarse, fine = mode_results
            fine_at_coarse = np.column_stack([
                np.interp(coarse["time"], fine["time"], fine["position"][:, axis]) for axis in range(3)
            ])
            error = float(np.max(np.linalg.norm(coarse["position"] - fine_at_coarse, axis=1)))
            refinements.append(dict(case_id=case["case_id"], interpolation=mode,
                                    max_position_difference_m=error,
                                    position_converged=error <= config["position_refinement_tolerance_m"],
                                    jerk_ratio_fine_over_coarse=fine["summary"]["peak_jerk_mps3"] / max(coarse["summary"]["peak_jerk_mps3"], 1e-12)))
        print(case["case_id"], [(r["passed"], r["failed_checks"]) for r in summaries if r["case_id"] == case["case_id"]], flush=True)
    write_csv(evaluation / "summary.csv", summaries)
    write_csv(evaluation / "samples.csv", traces)
    write_csv(evaluation / "refinement.csv", refinements)
    positive = [r for r in summaries if r["interpolation"] == "quintic"]
    negative = [r for r in summaries if r["interpolation"] == "step"]
    passed = all(r["passed"] == r["expected_pass"] for r in positive + negative)
    expected_positive = {r["case_id"] for r in positive if r["expected_pass"]}
    passed &= all(r["position_converged"] for r in refinements
                  if r["interpolation"] == "quintic" and r["case_id"] in expected_positive)
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    write_csv(root / "manifest.csv", [dict(run_id=root.name, code_version="v1", schema_version="transition-probe-v1",
              status="complete" if unchanged else "failed_source_changed", checks_passed=passed,
              training_selectable=False, evaluation_only=True, seed="deterministic",
              code_hash=hashlib.sha256((hashes[str(inputs[1])] + hashes[str(inputs[2])]).encode()).hexdigest(),
              config_path="configuration.json", environment_path="environment.json", inputs_path="inputs.csv",
              claim_scope="sampled_kinematic_continuity_not_aircraft_stability_or_full_episode")])
    write_csv(root / "files.csv", [dict(path=str(p.relative_to(root)), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()])
    assert unchanged, "inputs changed during probe"
    print(f"OUTPUT {root} CHECKS_PASSED={passed}", flush=True)
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/data_generation/v1"))
    args = parser.parse_args()
    run_batch(args.config, args.output_root)
