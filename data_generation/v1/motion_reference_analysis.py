"""Safe, split-aware analysis of the local Bitcraze motion-reference source."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from omegaconf import OmegaConf
from scipy.signal import savgol_filter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_CODE_PATH = Path(__file__).resolve()
ANALYSIS_CODE_RELATIVE_PATH = ANALYSIS_CODE_PATH.relative_to(PROJECT_ROOT)
SUPPORTED_SOURCE_COLUMN_COUNTS = frozenset({4, 16})
SOURCE_VALUE_COLUMN_COUNT = 4
ANALYSIS_ARTIFACT_KIND = "source_motion_reference_analysis"


class MotionReferenceError(ValueError):
    """Raised when a source array or its explicit analysis configuration is unsafe."""


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MotionReferenceError(f"{name} must be a mapping")
    return value


def _finite_float(value: object, *, name: str, positive: bool = False) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise MotionReferenceError(f"{name} must be numeric") from error
    if not math.isfinite(numeric) or (positive and numeric <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise MotionReferenceError(f"{name} must be {qualifier}")
    return numeric


def _positive_integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MotionReferenceError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class LoadedMotionReferenceConfig:
    """An explicit, hash-verified YAML configuration and its resolved values."""

    path: Path
    sha256: str
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MotionSegment:
    """One contiguous finite XYZ source window in seconds and meters."""

    time_s: np.ndarray
    position_m: np.ndarray


@dataclass(frozen=True, slots=True)
class MotionSegmentation:
    """Explicit account of finite windows and samples excluded before interpolation."""

    segments: tuple[MotionSegment, ...]
    input_sample_count: int
    valid_xyz_sample_count: int
    invalid_xyz_sample_count: int
    invalid_xyz_run_count: int
    long_gap_split_count: int
    valid_segment_time_s: float
    dropped_time_s: float


@dataclass(frozen=True, slots=True)
class FlightSummary:
    """One flight's source, filter, and motion-statistics accounting."""

    flight_id: str
    split: str
    nominal_mean_speed_mps: float
    input_sample_count: int
    valid_xyz_sample_count: int
    invalid_xyz_sample_count: int
    invalid_xyz_run_count: int
    long_gap_split_count: int
    valid_segment_count: int
    usable_segment_count: int
    short_segment_count: int
    resampled_sample_count: int
    analysis_sample_count: int
    analysis_sample_exposure_s: float
    source_span_s: float
    valid_segment_time_s: float
    invalid_or_gap_drop_time_s: float
    short_segment_drop_time_s: float
    resample_tail_drop_time_s: float
    edge_discard_time_s: float
    flight_filter_drop_sample_count: int
    flight_filter_drop_time_s: float
    valid_time_used_s: float
    time_accounting_residual_s: float
    low_speed_time_fraction: float | None
    speed_quantiles_mps: dict[str, float]
    acceleration_quantiles_mps2: dict[str, float]
    geometry_min_m: tuple[float, float, float] | None
    geometry_max_m: tuple[float, float, float] | None


@dataclass(frozen=True, slots=True)
class MotionReferenceAnalysisReport:
    """Source summaries with calibration and held-out comparison preserved separately."""

    config_path: Path
    config_hash: str
    flight_summaries: dict[str, FlightSummary]
    calibration_summaries: tuple[FlightSummary, ...]
    comparison_summaries: tuple[FlightSummary, ...]
    calibration_envelope: dict[str, float | None]


def _relative_project_path(path_value: object, *, name: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise MotionReferenceError(f"{name} must be a nonempty project-relative path")
    candidate = (PROJECT_ROOT / path_value).resolve()
    if not candidate.is_relative_to(PROJECT_ROOT):
        raise MotionReferenceError(f"{name} escapes the project root")
    return candidate


def _source_files(config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    source = _mapping(config.get("source"), name="source")
    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise MotionReferenceError("source.source_id must be nonempty")
    source_root = (PROJECT_ROOT / "data_sources" / source_id).resolve()
    files = source.get("files")
    if not isinstance(files, list) or not files:
        raise MotionReferenceError("source.files must be a nonempty list")
    seen_ids: set[str] = set()
    verified: list[dict[str, Any]] = []
    for record in files:
        record_mapping = _mapping(record, name="source file")
        flight_id = record_mapping.get("id")
        if not isinstance(flight_id, str) or not flight_id or flight_id in seen_ids:
            raise MotionReferenceError("source file ids must be nonempty and unique")
        seen_ids.add(flight_id)
        path = _relative_project_path(record_mapping.get("path"), name="source file path")
        if not path.is_relative_to(source_root):
            raise MotionReferenceError("source file path must remain inside source_id")
        expected_hash = record_mapping.get("sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise MotionReferenceError("source file requires a SHA-256 digest")
        if not path.is_file():
            raise MotionReferenceError(f"source file is missing: {record_mapping['path']}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash.lower():
            raise MotionReferenceError(f"source hash mismatch: {record_mapping['path']}")
        _finite_float(
            record_mapping.get("nominal_mean_speed_mps"),
            name="source nominal_mean_speed_mps",
            positive=True,
        )
        verified.append(record_mapping)
    return tuple(verified)


def _validate_split_lists(config: Mapping[str, Any], *, flight_ids: set[str]) -> None:
    splits = _mapping(config.get("splits"), name="splits")
    calibration = splits.get("calibration_flight_ids")
    comparison = splits.get("comparison_flight_ids")
    if not isinstance(calibration, list) or not isinstance(comparison, list):
        raise MotionReferenceError(
            "splits requires calibration_flight_ids and comparison_flight_ids"
        )
    if not all(isinstance(value, str) for value in calibration + comparison):
        raise MotionReferenceError("split flight ids must be strings")
    calibration_ids = set(calibration)
    comparison_ids = set(comparison)
    if calibration_ids & comparison_ids:
        raise MotionReferenceError("calibration and comparison flights must not overlap")
    if calibration_ids | comparison_ids != flight_ids:
        raise MotionReferenceError("every configured source flight must have one explicit split")


def _validate_analysis_config(config: Mapping[str, Any]) -> None:
    source = _mapping(config.get("source"), name="source")
    _finite_float(source.get("timestamp_scale_s"), name="source.timestamp_scale_s", positive=True)
    analysis = _mapping(config.get("analysis"), name="analysis")
    _finite_float(analysis.get("resample_dt_s"), name="analysis.resample_dt_s", positive=True)
    _finite_float(analysis.get("maximum_gap_s"), name="analysis.maximum_gap_s", positive=True)
    window = _positive_integer(
        analysis.get("savgol_window_samples"), name="savgol_window_samples"
    )
    polynomial = _positive_integer(
        analysis.get("savgol_polynomial_degree"), name="savgol_polynomial_degree"
    )
    discard = _positive_integer(analysis.get("discard_edge_samples"), name="discard_edge_samples")
    if window % 2 == 0 or polynomial >= window or discard * 2 >= window:
        raise MotionReferenceError("Savitzky-Golay window, degree, or edge discard is invalid")
    _finite_float(analysis.get("flight_only_min_z_m"), name="analysis.flight_only_min_z_m")
    _finite_float(
        analysis.get("low_speed_threshold_mps"),
        name="analysis.low_speed_threshold_mps",
        positive=True,
    )
    quantiles = analysis.get("quantiles")
    if not isinstance(quantiles, list) or not quantiles:
        raise MotionReferenceError("analysis.quantiles must be a nonempty list")
    for quantile in quantiles:
        value = _finite_float(quantile, name="analysis quantile")
        if not 0.0 <= value <= 1.0:
            raise MotionReferenceError("analysis quantiles must lie in [0, 1]")
    envelope = _mapping(config.get("reference_envelope"), name="reference_envelope")
    _finite_float(
        envelope.get("calibration_quantile"),
        name="reference_envelope.calibration_quantile",
    )
    _finite_float(
        envelope.get("maximum_reference_peak_speed_mps"),
        name="maximum_reference_peak_speed_mps",
        positive=True,
    )
    _finite_float(
        envelope.get("maximum_reference_peak_acceleration_mps2"),
        name="maximum_reference_peak_acceleration_mps2",
        positive=True,
    )
    _finite_float(
        envelope.get("engineering_speed_margin_mps"),
        name="engineering_speed_margin_mps",
        positive=True,
    )
    _finite_float(
        envelope.get("engineering_acceleration_margin_mps2"),
        name="engineering_acceleration_margin_mps2",
        positive=True,
    )


def load_motion_reference_config(config_path: Path) -> LoadedMotionReferenceConfig:
    """Load settings and verify every source checksum before any analysis or planning."""
    path = _relative_project_path(str(config_path), name="motion reference config")
    if not path.is_file():
        raise MotionReferenceError(f"motion reference config is missing: {path}")
    resolved = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(resolved, dict):
        raise MotionReferenceError("motion reference config must be a mapping")
    source_files = _source_files(resolved)
    _validate_split_lists(resolved, flight_ids={record["id"] for record in source_files})
    _validate_analysis_config(resolved)
    return LoadedMotionReferenceConfig(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        values=resolved,
    )


def load_numeric_motion_array(path: Path) -> np.ndarray:
    """Read only documented numeric Nx4/Nx16 arrays with pickle disabled."""
    try:
        array = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as error:
        raise MotionReferenceError(f"could not safely load motion array: {path}") from error
    if not isinstance(array, np.ndarray) or array.ndim != 2:
        raise MotionReferenceError("motion array must be two-dimensional")
    if array.shape[0] == 0 or array.shape[1] not in SUPPORTED_SOURCE_COLUMN_COUNTS:
        raise MotionReferenceError("motion array must have a supported nonempty Nx4 or Nx16 shape")
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.complexfloating):
        raise MotionReferenceError("motion array must have a real numeric dtype")
    return np.asarray(array[:, :SOURCE_VALUE_COLUMN_COUNT], dtype=float)


def segment_valid_motion(
    samples: np.ndarray,
    *,
    timestamp_scale_s: float,
    maximum_gap_s: float,
) -> MotionSegmentation:
    """Split finite XYZ windows and reject any source-time repair or reordering."""
    scale = _finite_float(timestamp_scale_s, name="timestamp_scale_s", positive=True)
    maximum_gap = _finite_float(maximum_gap_s, name="maximum_gap_s", positive=True)
    try:
        source = np.asarray(samples, dtype=float)
    except (TypeError, ValueError) as error:
        raise MotionReferenceError("motion samples must be numeric") from error
    if source.ndim != 2 or source.shape[0] == 0 or source.shape[1] != SOURCE_VALUE_COLUMN_COUNT:
        raise MotionReferenceError("motion samples must have shape Nx4")
    time_s = source[:, 0] * scale
    if not np.all(np.isfinite(time_s)):
        raise MotionReferenceError("motion timestamps must be finite")
    if np.any(np.diff(time_s) <= 0.0):
        raise MotionReferenceError("motion timestamps must be strictly increasing")
    positions = source[:, 1:4]
    valid_xyz = np.all(np.isfinite(positions), axis=1)
    segments: list[MotionSegment] = []
    current_indices: list[int] = []
    invalid_xyz_run_count = 0
    long_gap_split_count = 0
    in_invalid_run = False
    for index, is_valid in enumerate(valid_xyz):
        if not is_valid:
            if not in_invalid_run:
                invalid_xyz_run_count += 1
                in_invalid_run = True
            if current_indices:
                segment_indices = np.asarray(current_indices, dtype=int)
                segments.append(
                    MotionSegment(time_s[segment_indices], positions[segment_indices].copy())
                )
                current_indices = []
            continue
        in_invalid_run = False
        if current_indices and time_s[index] - time_s[current_indices[-1]] > maximum_gap:
            segment_indices = np.asarray(current_indices, dtype=int)
            segments.append(
                MotionSegment(time_s[segment_indices], positions[segment_indices].copy())
            )
            current_indices = []
            long_gap_split_count += 1
        current_indices.append(index)
    if current_indices:
        segment_indices = np.asarray(current_indices, dtype=int)
        segments.append(MotionSegment(time_s[segment_indices], positions[segment_indices].copy()))
    valid_segment_time_s = sum(
        float(segment.time_s[-1] - segment.time_s[0]) for segment in segments if len(segment.time_s)
    )
    source_span_s = float(time_s[-1] - time_s[0]) if len(time_s) > 1 else 0.0
    return MotionSegmentation(
        segments=tuple(segments),
        input_sample_count=len(source),
        valid_xyz_sample_count=int(np.count_nonzero(valid_xyz)),
        invalid_xyz_sample_count=int(np.count_nonzero(~valid_xyz)),
        invalid_xyz_run_count=invalid_xyz_run_count,
        long_gap_split_count=long_gap_split_count,
        valid_segment_time_s=valid_segment_time_s,
        dropped_time_s=max(0.0, source_span_s - valid_segment_time_s),
    )


def _quantile_key(value: float) -> str:
    return f"q{int(round(value * 100.0)):02d}"


def _resample_positions(segment: MotionSegment, *, dt_s: float) -> tuple[np.ndarray, np.ndarray]:
    duration_s = float(segment.time_s[-1] - segment.time_s[0])
    sample_count = int(math.floor(duration_s / dt_s + 1e-12)) + 1
    target_time_s = segment.time_s[0] + np.arange(sample_count, dtype=float) * dt_s
    position_m = np.column_stack(
        [np.interp(target_time_s, segment.time_s, segment.position_m[:, axis]) for axis in range(3)]
    )
    return target_time_s, position_m


def _analysis_settings(
    config: Mapping[str, Any],
) -> tuple[float, int, int, int, float, float, tuple[float, ...]]:
    analysis = _mapping(config["analysis"], name="analysis")
    return (
        _finite_float(analysis["resample_dt_s"], name="resample_dt_s", positive=True),
        _positive_integer(analysis["savgol_window_samples"], name="savgol_window_samples"),
        _positive_integer(analysis["savgol_polynomial_degree"], name="savgol_polynomial_degree"),
        _positive_integer(analysis["discard_edge_samples"], name="discard_edge_samples"),
        _finite_float(analysis["flight_only_min_z_m"], name="flight_only_min_z_m"),
        _finite_float(
            analysis["low_speed_threshold_mps"], name="low_speed_threshold_mps", positive=True
        ),
        tuple(_finite_float(value, name="quantile") for value in analysis["quantiles"]),
    )


def _flight_split(config: Mapping[str, Any], flight_id: str) -> str:
    splits = _mapping(config["splits"], name="splits")
    if flight_id in splits["calibration_flight_ids"]:
        return "calibration"
    if flight_id in splits["comparison_flight_ids"]:
        return "comparison"
    raise MotionReferenceError(f"source flight has no configured split: {flight_id}")


def _summary_for_flight(record: Mapping[str, Any], config: Mapping[str, Any]) -> FlightSummary:
    source = _mapping(config["source"], name="source")
    dt_s, window, polynomial, discard, min_z_m, low_speed_threshold, quantiles = _analysis_settings(
        config
    )
    array_path = _relative_project_path(record["path"], name="source file path")
    samples = load_numeric_motion_array(array_path)
    segmentation = segment_valid_motion(
        samples,
        timestamp_scale_s=_finite_float(
            source["timestamp_scale_s"], name="timestamp_scale_s", positive=True
        ),
        maximum_gap_s=_finite_float(
            config["analysis"]["maximum_gap_s"], name="maximum_gap_s", positive=True
        ),
    )
    source_span_s = float(samples[-1, 0] - samples[0, 0]) * float(source["timestamp_scale_s"])
    speeds: list[np.ndarray] = []
    accelerations: list[np.ndarray] = []
    geometry: list[np.ndarray] = []
    usable_segment_count = 0
    short_segment_count = 0
    short_segment_drop_time_s = 0.0
    resample_tail_drop_time_s = 0.0
    edge_discard_time_s = 0.0
    resampled_sample_count = 0
    flight_filter_drop_sample_count = 0
    flight_filter_drop_time_s = 0.0
    analysis_sample_exposure_s = 0.0
    valid_time_used_s = 0.0
    for segment in segmentation.segments:
        segment_span_s = float(segment.time_s[-1] - segment.time_s[0])
        target_time_s, resampled_position_m = _resample_positions(segment, dt_s=dt_s)
        resampled_sample_count += len(target_time_s)
        if len(target_time_s) < window:
            short_segment_count += 1
            short_segment_drop_time_s += segment_span_s
            continue
        resampled_interval_time_s = (
            float(target_time_s[-1] - target_time_s[0]) if len(target_time_s) > 1 else 0.0
        )
        segment_tail_drop_time_s = segment_span_s - resampled_interval_time_s
        if segment_tail_drop_time_s < -1e-8:
            raise MotionReferenceError("resampling grid exceeds a valid source segment")
        resample_tail_drop_time_s += max(segment_tail_drop_time_s, 0.0)
        smooth_position_m = savgol_filter(
            resampled_position_m,
            window_length=window,
            polyorder=polynomial,
            deriv=0,
            delta=dt_s,
            axis=0,
            mode="interp",
        )
        velocity_mps = savgol_filter(
            resampled_position_m,
            window_length=window,
            polyorder=polynomial,
            deriv=1,
            delta=dt_s,
            axis=0,
            mode="interp",
        )
        acceleration_mps2 = savgol_filter(
            resampled_position_m,
            window_length=window,
            polyorder=polynomial,
            deriv=2,
            delta=dt_s,
            axis=0,
            mode="interp",
        )
        interior = slice(discard, len(target_time_s) - discard)
        interior_position_m = smooth_position_m[interior]
        interior_velocity_mps = velocity_mps[interior]
        interior_acceleration_mps2 = acceleration_mps2[interior]
        flight_mask = interior_position_m[:, 2] > min_z_m
        flight_filter_drop_sample_count += int(np.count_nonzero(~flight_mask))
        edge_discard_time_s += 2.0 * discard * dt_s
        interval_count = max(len(interior_position_m) - 1, 0)
        if interval_count:
            retained_intervals = flight_mask[:-1] & flight_mask[1:]
            retained_interval_count = int(np.count_nonzero(retained_intervals))
            valid_time_used_s += retained_interval_count * dt_s
            flight_filter_drop_time_s += (interval_count - retained_interval_count) * dt_s
        if not np.any(flight_mask):
            continue
        usable_segment_count += 1
        selected_position_m = interior_position_m[flight_mask]
        selected_velocity_mps = interior_velocity_mps[flight_mask]
        selected_acceleration_mps2 = interior_acceleration_mps2[flight_mask]
        geometry.append(selected_position_m)
        speeds.append(np.linalg.norm(selected_velocity_mps, axis=1))
        accelerations.append(np.linalg.norm(selected_acceleration_mps2, axis=1))
        analysis_sample_exposure_s += len(selected_position_m) * dt_s
    if speeds:
        speed_values = np.concatenate(speeds)
        acceleration_values = np.concatenate(accelerations)
        geometry_values = np.concatenate(geometry)
        speed_quantiles = {
            _quantile_key(value): float(np.quantile(speed_values, value)) for value in quantiles
        }
        acceleration_quantiles = {
            _quantile_key(value): float(np.quantile(acceleration_values, value))
            for value in quantiles
        }
        geometry_min = tuple(float(value) for value in np.min(geometry_values, axis=0))
        geometry_max = tuple(float(value) for value in np.max(geometry_values, axis=0))
        low_speed_fraction: float | None = float(np.mean(speed_values < low_speed_threshold))
        analysis_sample_count = int(len(speed_values))
    else:
        speed_quantiles = {}
        acceleration_quantiles = {}
        geometry_min = None
        geometry_max = None
        low_speed_fraction = None
        analysis_sample_count = 0
    valid_segment_accounting_residual_s = segmentation.valid_segment_time_s - (
        short_segment_drop_time_s
        + resample_tail_drop_time_s
        + edge_discard_time_s
        + flight_filter_drop_time_s
        + valid_time_used_s
    )
    time_accounting_residual_s = source_span_s - (
        segmentation.dropped_time_s
        + short_segment_drop_time_s
        + resample_tail_drop_time_s
        + edge_discard_time_s
        + flight_filter_drop_time_s
        + valid_time_used_s
    )
    assert math.isfinite(valid_segment_accounting_residual_s)
    assert math.isfinite(time_accounting_residual_s)
    if not math.isclose(valid_segment_accounting_residual_s, 0.0, abs_tol=1e-8):
        raise MotionReferenceError("valid segment time accounting does not reconcile")
    if not math.isclose(time_accounting_residual_s, 0.0, abs_tol=1e-8):
        raise MotionReferenceError("source time accounting does not reconcile")
    return FlightSummary(
        flight_id=str(record["id"]),
        split=_flight_split(config, str(record["id"])),
        nominal_mean_speed_mps=_finite_float(
            record["nominal_mean_speed_mps"], name="nominal_mean_speed_mps", positive=True
        ),
        input_sample_count=segmentation.input_sample_count,
        valid_xyz_sample_count=segmentation.valid_xyz_sample_count,
        invalid_xyz_sample_count=segmentation.invalid_xyz_sample_count,
        invalid_xyz_run_count=segmentation.invalid_xyz_run_count,
        long_gap_split_count=segmentation.long_gap_split_count,
        valid_segment_count=len(segmentation.segments),
        usable_segment_count=usable_segment_count,
        short_segment_count=short_segment_count,
        resampled_sample_count=resampled_sample_count,
        analysis_sample_count=analysis_sample_count,
        analysis_sample_exposure_s=analysis_sample_exposure_s,
        source_span_s=source_span_s,
        valid_segment_time_s=segmentation.valid_segment_time_s,
        invalid_or_gap_drop_time_s=segmentation.dropped_time_s,
        short_segment_drop_time_s=short_segment_drop_time_s,
        resample_tail_drop_time_s=resample_tail_drop_time_s,
        edge_discard_time_s=edge_discard_time_s,
        flight_filter_drop_sample_count=flight_filter_drop_sample_count,
        flight_filter_drop_time_s=flight_filter_drop_time_s,
        valid_time_used_s=valid_time_used_s,
        time_accounting_residual_s=time_accounting_residual_s,
        low_speed_time_fraction=low_speed_fraction,
        speed_quantiles_mps=speed_quantiles,
        acceleration_quantiles_mps2=acceleration_quantiles,
        geometry_min_m=geometry_min,
        geometry_max_m=geometry_max,
    )


def _calibration_envelope(
    calibration_summaries: tuple[FlightSummary, ...], config: Mapping[str, Any]
) -> dict[str, float | None]:
    envelope = _mapping(config["reference_envelope"], name="reference_envelope")
    quantile = _finite_float(envelope["calibration_quantile"], name="calibration_quantile")
    quantile_key = _quantile_key(quantile)
    speed_values = [
        summary.speed_quantiles_mps[quantile_key]
        for summary in calibration_summaries
        if quantile_key in summary.speed_quantiles_mps
    ]
    acceleration_values = [
        summary.acceleration_quantiles_mps2[quantile_key]
        for summary in calibration_summaries
        if quantile_key in summary.acceleration_quantiles_mps2
    ]
    reference_speed = _finite_float(
        envelope["maximum_reference_peak_speed_mps"],
        name="maximum_reference_peak_speed_mps",
        positive=True,
    )
    reference_acceleration = _finite_float(
        envelope["maximum_reference_peak_acceleration_mps2"],
        name="maximum_reference_peak_acceleration_mps2",
        positive=True,
    )
    calibration_speed = max(speed_values) if speed_values else None
    calibration_acceleration = max(acceleration_values) if acceleration_values else None
    return {
        "calibration_quantile": quantile,
        "calibration_speed_quantile_mps": calibration_speed,
        "calibration_acceleration_quantile_mps2": calibration_acceleration,
        "configured_reference_peak_speed_mps": reference_speed,
        "configured_reference_peak_acceleration_mps2": reference_acceleration,
        "configured_minus_calibration_speed_mps": (
            reference_speed - calibration_speed if calibration_speed is not None else None
        ),
        "configured_minus_calibration_acceleration_mps2": (
            reference_acceleration - calibration_acceleration
            if calibration_acceleration is not None
            else None
        ),
        "declared_engineering_speed_margin_mps": _finite_float(
            envelope["engineering_speed_margin_mps"],
            name="engineering_speed_margin_mps",
            positive=True,
        ),
        "declared_engineering_acceleration_margin_mps2": _finite_float(
            envelope["engineering_acceleration_margin_mps2"],
            name="engineering_acceleration_margin_mps2",
            positive=True,
        ),
    }


def analyze_motion_reference(
    config: LoadedMotionReferenceConfig,
) -> MotionReferenceAnalysisReport:
    """Analyze all configured flights while preserving fixed calibration/comparison splits."""
    if not isinstance(config, LoadedMotionReferenceConfig):
        raise MotionReferenceError("analysis requires a loaded motion-reference config")
    records = _source_files(config.values)
    summaries = {
        str(record["id"]): _summary_for_flight(record, config.values) for record in records
    }
    calibration = tuple(summary for summary in summaries.values() if summary.split == "calibration")
    comparison = tuple(summary for summary in summaries.values() if summary.split == "comparison")
    return MotionReferenceAnalysisReport(
        config_path=config.path,
        config_hash=config.sha256,
        flight_summaries=summaries,
        calibration_summaries=calibration,
        comparison_summaries=comparison,
        calibration_envelope=_calibration_envelope(calibration, config.values),
    )


def _summary_row(summary: FlightSummary) -> dict[str, object]:
    row: dict[str, object] = {
        "flight_id": summary.flight_id,
        "split": summary.split,
        "nominal_mean_speed_mps": summary.nominal_mean_speed_mps,
        "input_sample_count": summary.input_sample_count,
        "valid_xyz_sample_count": summary.valid_xyz_sample_count,
        "invalid_xyz_sample_count": summary.invalid_xyz_sample_count,
        "invalid_xyz_run_count": summary.invalid_xyz_run_count,
        "long_gap_split_count": summary.long_gap_split_count,
        "valid_segment_count": summary.valid_segment_count,
        "usable_segment_count": summary.usable_segment_count,
        "short_segment_count": summary.short_segment_count,
        "resampled_sample_count": summary.resampled_sample_count,
        "analysis_sample_count": summary.analysis_sample_count,
        "analysis_sample_exposure_s": summary.analysis_sample_exposure_s,
        "source_span_s": summary.source_span_s,
        "valid_segment_time_s": summary.valid_segment_time_s,
        "invalid_or_gap_drop_time_s": summary.invalid_or_gap_drop_time_s,
        "short_segment_drop_time_s": summary.short_segment_drop_time_s,
        "resample_tail_drop_time_s": summary.resample_tail_drop_time_s,
        "edge_discard_time_s": summary.edge_discard_time_s,
        "flight_filter_drop_sample_count": summary.flight_filter_drop_sample_count,
        "flight_filter_drop_time_s": summary.flight_filter_drop_time_s,
        "valid_time_used_s": summary.valid_time_used_s,
        "time_accounting_residual_s": summary.time_accounting_residual_s,
        "low_speed_time_fraction": summary.low_speed_time_fraction,
    }
    for name, value in summary.speed_quantiles_mps.items():
        row[f"speed_{name}_mps"] = value
    for name, value in summary.acceleration_quantiles_mps2.items():
        row[f"acceleration_{name}_mps2"] = value
    for axis, value in zip("xyz", summary.geometry_min_m or (None, None, None), strict=True):
        row[f"{axis}_min_m"] = value
    for axis, value in zip("xyz", summary.geometry_max_m or (None, None, None), strict=True):
        row[f"{axis}_max_m"] = value
    return row


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    columns = tuple(dict.fromkeys(name for row in rows for name in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _summary_markdown(report: MotionReferenceAnalysisReport) -> str:
    calibration_ids = ", ".join(
        f"`{summary.flight_id}`" for summary in report.calibration_summaries
    ) or "(none)"
    comparison_ids = ", ".join(
        f"`{summary.flight_id}`" for summary in report.comparison_summaries
    ) or "(none)"
    lines = [
        "# Bitcraze motion-reference analysis",
        "",
        f"- Artifact kind: `{ANALYSIS_ARTIFACT_KIND}`",
        f"- Effective config SHA-256: `{report.config_hash}`",
        f"- Calibration: {calibration_ids}; held-out comparison: {comparison_ids}.",
        "- This analyzes marker-centroid tracks only. It is not raw-control replay, system-ID, or "
        "independent real-flight validation of the 27 g Eschmann model.",
        "",
        "## Calibration-derived reference envelope",
        "",
    ]
    for name, value in report.calibration_envelope.items():
        lines.append(f"- {name}: {value}")
    lines.extend(
        [
            "",
            "## Per-flight accounting",
            "",
            "`analysis_sample_exposure_s` is selected grid-sample count times the configured "
            "resample interval; `valid_time_used_s` is retained elapsed interval time.",
            "Each row reconciles `source_span_s` as invalid/gap, short-segment, resampling-tail, "
            "edge-discard, flight-filter, and retained elapsed-time fields; "
            "`time_accounting_residual_s` records the checked residual.",
            "See `flight_summary.csv` for source rows, usable segments, filtering, quantiles, "
            "and geometry ranges.",
            "",
            "The configured engineering margins are design settings reported separately from "
            "the measured calibration quantiles. Held-out comparison rows do not set these values.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_inventory(artifact_path: Path) -> None:
    rows = []
    files = (
        entry for entry in artifact_path.iterdir() if entry.is_file() and entry.name != "files.csv"
    )
    for path in sorted(files):
        rows.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    _write_csv(artifact_path / "files.csv", rows)


def _analysis_code_sha256() -> str:
    """Hash the module that performs this analysis rather than a generated artifact."""
    return hashlib.sha256(ANALYSIS_CODE_PATH.read_bytes()).hexdigest()


def _environment_text() -> str:
    """Record the runtime used for a traceable analysis artifact."""
    distributions = ("numpy", "omegaconf", "scipy")
    lines = [
        f"python_executable={sys.executable}",
        f"python_version={sys.version.replace(chr(10), ' ')}",
        f"platform={platform.platform()}",
    ]
    for distribution in distributions:
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "not_installed"
        lines.append(f"{distribution}_version={version}")
    return "\n".join(lines) + "\n"


def _source_provenance_rows(config: LoadedMotionReferenceConfig) -> list[dict[str, object]]:
    """Materialize per-file checksums and their immutable calibration/comparison role."""
    rows = []
    for record in _source_files(config.values):
        flight_id = str(record["id"])
        rows.append(
            {
                "flight_id": flight_id,
                "split": _flight_split(config.values, flight_id),
                "path": record["path"],
                "sha256": record["sha256"],
                "url": record["url"],
                "motion_pattern": record["motion_pattern"],
                "nominal_mean_speed_mps": record["nominal_mean_speed_mps"],
            }
        )
    return rows


def write_motion_reference_analysis(
    config_path: Path,
    *,
    output_root: Path | None = None,
) -> Path:
    """Write a new analysis-only UUID artifact without creating a contract dataset."""
    config = load_motion_reference_config(config_path)
    report = analyze_motion_reference(config)
    root = PROJECT_ROOT / "outputs" if output_root is None else Path(output_root)
    artifact_path = root / "data_generation" / "v1" / str(uuid.uuid4())
    artifact_path.mkdir(parents=True, exist_ok=False)
    code_hash = _analysis_code_sha256()
    manifest = {
        "artifact_kind": ANALYSIS_ARTIFACT_KIND,
        "artifact_status": "analysis_complete",
        "config_path": str(config.path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "config_sha256": report.config_hash,
        "analysis_code_path": str(ANALYSIS_CODE_RELATIVE_PATH).replace("\\", "/"),
        "analysis_code_sha256": code_hash,
        "source_provenance_path": "source_provenance.csv",
        "split_definition_path": "effective_motion_reference_config.yaml",
        "environment_path": "environment.txt",
        "calibration_flight_ids": [summary.flight_id for summary in report.calibration_summaries],
        "comparison_flight_ids": [summary.flight_id for summary in report.comparison_summaries],
        "calibration_envelope": report.calibration_envelope,
        "limitations": [
            "Source tracks characterize reference geometry and motion scale only.",
            "No source control log is present; this artifact is not raw-control replay.",
            "Comparison flights are held out from planner threshold and geometry selection.",
        ],
    }
    (artifact_path / "analysis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        artifact_path / "flight_summary.csv",
        [_summary_row(summary) for summary in report.flight_summaries.values()],
    )
    _write_csv(artifact_path / "source_provenance.csv", _source_provenance_rows(config))
    (artifact_path / "analysis_summary.md").write_text(
        _summary_markdown(report), encoding="utf-8"
    )
    OmegaConf.save(
        config=OmegaConf.create(config.values),
        f=artifact_path / "effective_motion_reference_config.yaml",
        resolve=True,
    )
    (artifact_path / "environment.txt").write_text(_environment_text(), encoding="utf-8")
    _write_csv(
        artifact_path / "manifest.csv",
        [
            {
                "artifact_kind": ANALYSIS_ARTIFACT_KIND,
                "artifact_id": artifact_path.name,
                "artifact_status": "analysis_complete",
                "analysis_manifest_path": "analysis_manifest.json",
                "flight_summary_path": "flight_summary.csv",
                "source_provenance_path": "source_provenance.csv",
                "effective_config_path": "effective_motion_reference_config.yaml",
                "environment_path": "environment.txt",
                "analysis_code_path": str(ANALYSIS_CODE_RELATIVE_PATH).replace("\\", "/"),
                "analysis_code_sha256": code_hash,
                "config_sha256": report.config_hash,
            }
        ],
    )
    _write_inventory(artifact_path)
    return artifact_path


def main() -> None:
    """Run explicit local source analysis without invoking any hardware source script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    arguments = parser.parse_args()
    artifact_path = write_motion_reference_analysis(
        arguments.config_path,
        output_root=arguments.output_root,
    )
    print(f"artifact_kind={ANALYSIS_ARTIFACT_KIND}")
    print(f"path={artifact_path}")


if __name__ == "__main__":
    main()


__all__ = [
    "ANALYSIS_ARTIFACT_KIND",
    "FlightSummary",
    "LoadedMotionReferenceConfig",
    "MotionReferenceAnalysisReport",
    "MotionReferenceError",
    "MotionSegment",
    "MotionSegmentation",
    "analyze_motion_reference",
    "load_motion_reference_config",
    "load_numeric_motion_array",
    "segment_valid_motion",
    "write_motion_reference_analysis",
]
