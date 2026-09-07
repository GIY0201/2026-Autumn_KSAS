"""Replay-style motion checks against the public X8 TRAIN source logs.

This is a diagnostic comparison, not an optimizer and not a validation-data
tuning path.  It reports residuals for the source's motion-related estimated
states after the source README's declared synchronization/resampling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .source import X8SourceFlight, X8SourceSample, read_x8_source_csv, verify_known_source_tree
from .x8 import (
    GRAVITY_MPS2,
    ActuatorCommand,
    CommandDelay,
    X8Dynamics,
    integrate_rk4,
    mix_elevons,
    quaternion_from_euler,
    quaternion_to_euler,
    quaternion_to_rotation,
    solve_calm_trim,
)

PHYSICS_DT_S = 0.0025
SOURCE_DT_S = 0.025

assert int(SOURCE_DT_S / PHYSICS_DT_S) == 10


@dataclass(frozen=True, slots=True)
class ResidualSummary:
    """Scalar aggregate for a vector residual over one selected time window."""

    rmse: float
    bias: float
    max_abs: float


@dataclass(frozen=True, slots=True)
class ClipMotionCheck:
    """Report for one source TRAIN clip, with no accept/reject performance threshold."""

    clip_id: str
    sample_count: int
    full_metrics: dict[str, ResidualSummary]
    after_first_second_metrics: dict[str, ResidualSummary]


@dataclass(frozen=True, slots=True)
class MotionCheckReport:
    """Aggregate source-motion diagnostic report for the fixed TRAIN split only."""

    clips: tuple[ClipMotionCheck, ...]
    full_metrics: dict[str, ResidualSummary]
    after_first_second_metrics: dict[str, ResidualSummary]

    def csv_rows(self) -> list[dict[str, str]]:
        """Return a stable tabular report suitable for an evaluation-only CSV."""
        rows: list[dict[str, str]] = []
        for scope, metrics in (
            ("aggregate_full", self.full_metrics),
            ("aggregate_after_first_second", self.after_first_second_metrics),
        ):
            for metric, summary in metrics.items():
                rows.append(
                    {
                        "scope": scope,
                        "metric": metric,
                        "rmse": format(summary.rmse, ".17g"),
                        "bias": format(summary.bias, ".17g"),
                        "max_abs": format(summary.max_abs, ".17g"),
                    }
                )
        for clip in self.clips:
            for scope, metrics in (
                ("clip_full", clip.full_metrics),
                ("clip_after_first_second", clip.after_first_second_metrics),
            ):
                for metric, summary in metrics.items():
                    rows.append(
                        {
                            "scope": f"{scope}:{clip.clip_id}",
                            "metric": metric,
                            "rmse": format(summary.rmse, ".17g"),
                            "bias": format(summary.bias, ".17g"),
                            "max_abs": format(summary.max_abs, ".17g"),
                        }
                    )
        return rows


def wrapped_angle_residual(model: np.ndarray, source: np.ndarray) -> np.ndarray:
    """Compute model-minus-source residuals in [-pi, pi), including Euler yaw wrapping."""
    return (np.asarray(model, dtype=float) - np.asarray(source, dtype=float) + math.pi) % (
        2.0 * math.pi
    ) - math.pi


def summarize_residual(residual: np.ndarray) -> ResidualSummary:
    """Summarize all vector components without hiding a componentwise large error."""
    values = np.asarray(residual, dtype=float)
    if values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("residual must be a non-empty finite array")
    return ResidualSummary(
        rmse=float(np.sqrt(np.mean(values**2))),
        bias=float(np.mean(values)),
        max_abs=float(np.max(np.abs(values))),
    )


def _source_command(sample: X8SourceSample) -> ActuatorCommand:
    left_rad, right_rad = mix_elevons(sample.elevator_rad, sample.aileron_rad)
    return ActuatorCommand(left_rad=left_rad, right_rad=right_rad, throttle=sample.throttle)


def _initial_state(
    dynamics: X8Dynamics, sample: X8SourceSample
) -> tuple[np.ndarray, ActuatorCommand]:
    airspeed = max(14.0, min(22.0, sample.indicated_speed_mps))
    trim = solve_calm_trim(dynamics, airspeed_mps=airspeed)
    state = trim.state.copy()
    command = _source_command(sample)
    state[3:6] = sample.body_velocity_mps
    state[6:10] = quaternion_from_euler(*sample.euler_rad)
    state[10:13] = sample.estimated_pqr_radps
    state[14:16] = (command.left_rad, command.right_rad)
    state[16:18] = 0.0
    state[18] = command.throttle
    return state, command


def _specific_force_body(
    dynamics: X8Dynamics,
    state: np.ndarray,
    command: ActuatorCommand,
    wind_ned_mps: np.ndarray,
    voltage_v: float,
) -> np.ndarray:
    derivative = dynamics.derivative(state, command, wind_ned_mps=wind_ned_mps, voltage_v=voltage_v)
    gravity_body = quaternion_to_rotation(state[6:10]).T @ np.array([0.0, 0.0, GRAVITY_MPS2])
    return derivative[3:6] + np.cross(state[10:13], state[3:6]) - gravity_body


def _model_motion_fields(
    dynamics: X8Dynamics, state: np.ndarray, command: ActuatorCommand, sample: X8SourceSample
) -> dict[str, np.ndarray]:
    rotation_nb = quaternion_to_rotation(state[6:10]).T
    body_air_velocity = state[3:6] - rotation_nb @ np.asarray(sample.wind_ned_mps, dtype=float)
    return {
        "body_velocity_mps": state[3:6].copy(),
        "angular_rate_radps": state[10:13].copy(),
        "euler_rad": quaternion_to_euler(state[6:10]),
        "indicated_speed_mps": np.array([np.linalg.norm(body_air_velocity)], dtype=float),
        "specific_force_mps2": _specific_force_body(
            dynamics,
            state,
            command,
            np.asarray(sample.wind_ned_mps, dtype=float),
            sample.voltage_v,
        ),
    }


def _source_motion_fields(sample: X8SourceSample) -> dict[str, np.ndarray]:
    return {
        "body_velocity_mps": np.asarray(sample.body_velocity_mps, dtype=float),
        "angular_rate_radps": np.asarray(sample.estimated_pqr_radps, dtype=float),
        "euler_rad": np.asarray(sample.euler_rad, dtype=float),
        "indicated_speed_mps": np.array([sample.indicated_speed_mps], dtype=float),
        "specific_force_mps2": np.asarray(sample.imu_acc_mps2, dtype=float),
    }


def _simulate_source_flight(flight: X8SourceFlight, dynamics: X8Dynamics) -> dict[str, np.ndarray]:
    samples = flight.samples
    state, initial_command = _initial_state(dynamics, samples[0])
    delay = CommandDelay(PHYSICS_DT_S, initial_command)
    delayed_command = initial_command
    model_fields = {name: [] for name in _source_motion_fields(samples[0])}
    first = _model_motion_fields(dynamics, state, delayed_command, samples[0])
    for name, values in first.items():
        model_fields[name].append(values)

    for previous, current in zip(samples[:-1], samples[1:], strict=True):
        interval_s = current.t_s - previous.t_s
        substeps = round(interval_s / PHYSICS_DT_S)
        if not math.isclose(substeps * PHYSICS_DT_S, interval_s, abs_tol=1e-9):
            raise ValueError(
                f"source interval {interval_s} does not align with 2.5 ms physics clock"
            )
        command = _source_command(previous)
        for substep in range(substeps):
            fraction = (substep + 0.5) / substeps
            wind = (1.0 - fraction) * np.asarray(previous.wind_ned_mps) + fraction * np.asarray(
                current.wind_ned_mps
            )
            voltage = (1.0 - fraction) * previous.voltage_v + fraction * current.voltage_v
            delayed_command = delay.advance(command)
            state = integrate_rk4(
                dynamics,
                state,
                delayed_command,
                duration_s=PHYSICS_DT_S,
                dt_s=PHYSICS_DT_S,
                wind_ned_mps=wind,
                voltage_v=voltage,
            )
        fields = _model_motion_fields(dynamics, state, delayed_command, current)
        for name, values in fields.items():
            model_fields[name].append(values)
    return {name: np.asarray(values, dtype=float) for name, values in model_fields.items()}


def run_training_motion_check(raw_directory: Path) -> MotionCheckReport:
    """Run the fixed model against all 13 verified TRAIN logs.

    VALIDATION files are not used for tuning in this report-only check.
    """
    verify_known_source_tree(raw_directory)
    dynamics = X8Dynamics.default()
    clips: list[ClipMotionCheck] = []
    aggregate_full: dict[str, list[np.ndarray]] = {}
    aggregate_after: dict[str, list[np.ndarray]] = {}
    training_paths = sorted(path for path in raw_directory.joinpath("training").glob("*.csv"))
    if len(training_paths) != 13:
        raise ValueError(f"expected 13 TRAIN source files, got {len(training_paths)}")
    for path in training_paths:
        flight = read_x8_source_csv(path)
        model = _simulate_source_flight(flight, dynamics)
        source = {
            name: np.asarray(
                [fields[name] for fields in map(_source_motion_fields, flight.samples)]
            )
            for name in model
        }
        residuals: dict[str, np.ndarray] = {}
        for name in model:
            residuals[name] = (
                wrapped_angle_residual(model[name], source[name])
                if name == "euler_rad"
                else model[name] - source[name]
            )
        first_second_mask = np.asarray(
            [sample.t_s - flight.samples[0].t_s >= 1.0 for sample in flight.samples]
        )
        full = {name: summarize_residual(values) for name, values in residuals.items()}
        after = {
            name: summarize_residual(values[first_second_mask])
            for name, values in residuals.items()
            if np.any(first_second_mask)
        }
        clips.append(
            ClipMotionCheck(
                clip_id=path.stem,
                sample_count=len(flight.samples),
                full_metrics=full,
                after_first_second_metrics=after,
            )
        )
        for name, values in residuals.items():
            aggregate_full.setdefault(name, []).append(values)
            aggregate_after.setdefault(name, []).append(values[first_second_mask])
    return MotionCheckReport(
        clips=tuple(clips),
        full_metrics={
            name: summarize_residual(np.concatenate(values, axis=0))
            for name, values in aggregate_full.items()
        },
        after_first_second_metrics={
            name: summarize_residual(np.concatenate(values, axis=0))
            for name, values in aggregate_after.items()
        },
    )
