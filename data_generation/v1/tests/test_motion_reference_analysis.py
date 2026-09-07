"""Safety and accounting tests for Bitcraze motion-reference analysis."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import data_generation.v1.motion_reference_analysis as analysis_module
from data_generation.v1.motion_reference_analysis import (
    MotionReferenceError,
    analyze_motion_reference,
    load_motion_reference_config,
    load_numeric_motion_array,
    segment_valid_motion,
    write_motion_reference_analysis,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_CONFIG_PATH = (
    PROJECT_ROOT
    / "data_generation"
    / "v1"
    / "configs"
    / "motion_reference"
    / "bitcraze_positioning.yaml"
)


def test_segment_valid_motion_splits_nan_runs_and_long_gaps_without_bridging() -> None:
    """Only contiguous finite XYZ windows can enter interpolation/filtering."""
    samples = np.array(
        [
            [0.0, 0.0, 0.0, 0.0],
            [10.0, 1.0, 0.0, 0.0],
            [20.0, np.nan, np.nan, np.nan],
            [30.0, 2.0, 0.0, 0.0],
            [40.0, 3.0, 0.0, 0.0],
            [200.0, 4.0, 0.0, 0.0],
            [210.0, 5.0, 0.0, 0.0],
        ],
        dtype=float,
    )

    segmentation = segment_valid_motion(
        samples,
        timestamp_scale_s=0.001,
        maximum_gap_s=0.05,
    )

    assert [segment.position_m.shape[0] for segment in segmentation.segments] == [2, 2, 2]
    assert segmentation.invalid_xyz_sample_count == 1
    assert segmentation.invalid_xyz_run_count == 1
    assert segmentation.long_gap_split_count == 1
    assert segmentation.dropped_time_s > 0.0
    assert all(np.all(np.diff(segment.time_s) > 0.0) for segment in segmentation.segments)


def test_segment_valid_motion_rejects_nonmonotonic_or_nonfinite_timestamps() -> None:
    """The source time axis is never sorted or repaired implicitly."""
    nonmonotonic = np.array(
        [[0.0, 0.0, 0.0, 0.0], [10.0, 1.0, 0.0, 0.0], [5.0, 2.0, 0.0, 0.0]]
    )
    nonfinite = np.array(
        [[0.0, 0.0, 0.0, 0.0], [np.nan, 1.0, 0.0, 0.0]]
    )

    with pytest.raises(MotionReferenceError, match="strictly increasing"):
        segment_valid_motion(nonmonotonic, timestamp_scale_s=0.001, maximum_gap_s=0.05)
    with pytest.raises(MotionReferenceError, match="finite"):
        segment_valid_motion(nonfinite, timestamp_scale_s=0.001, maximum_gap_s=0.05)


def test_numeric_reader_rejects_unsafe_object_arrays_and_maps_supported_nx16(
    tmp_path: Path,
) -> None:
    """Loading remains non-pickle and only documented first-four-column mappings are used."""
    object_path = tmp_path / "unsafe.npy"
    np.save(object_path, np.array([["unsafe", object()]], dtype=object), allow_pickle=True)
    with pytest.raises(MotionReferenceError):
        load_numeric_motion_array(object_path)

    nx16_path = tmp_path / "markers.npy"
    source = np.arange(32, dtype=float).reshape(2, 16)
    np.save(nx16_path, source, allow_pickle=False)
    mapped = load_numeric_motion_array(nx16_path)
    assert mapped.shape == (2, 4)
    assert np.array_equal(mapped, source[:, :4])


def test_real_source_analysis_keeps_calibration_and_comparison_separate(tmp_path: Path) -> None:
    """00/01 parameterize the reference report while 02/03 remain held-out comparison."""
    config = load_motion_reference_config(REFERENCE_CONFIG_PATH)
    report = analyze_motion_reference(config)

    assert report.config_hash
    assert set(report.flight_summaries) == {"mocap00", "mocap01", "mocap02", "mocap03"}
    assert {summary.split for summary in report.calibration_summaries} == {"calibration"}
    assert {summary.split for summary in report.comparison_summaries} == {"comparison"}
    assert all(summary.usable_segment_count > 0 for summary in report.flight_summaries.values())
    assert all(summary.analysis_sample_count > 0 for summary in report.flight_summaries.values())
    assert all(summary.speed_quantiles_mps for summary in report.flight_summaries.values())
    assert all(summary.acceleration_quantiles_mps2 for summary in report.flight_summaries.values())
    assert all(
        summary.analysis_sample_exposure_s
        == pytest.approx(summary.analysis_sample_count * config.values["analysis"]["resample_dt_s"])
        for summary in report.flight_summaries.values()
    )
    assert all(
        abs(summary.time_accounting_residual_s) <= 1e-8
        for summary in report.flight_summaries.values()
    )

    artifact = write_motion_reference_analysis(REFERENCE_CONFIG_PATH, output_root=tmp_path)
    manifest = json.loads((artifact / "analysis_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_kind"] == "source_motion_reference_analysis"
    assert manifest["config_sha256"] == report.config_hash
    assert len(manifest["analysis_code_sha256"]) == 64
    manifest_csv = (artifact / "manifest.csv").read_text(encoding="utf-8")
    assert "artifact_kind" in manifest_csv
    assert "source_provenance.csv" in manifest_csv
    assert "environment.txt" in manifest_csv
    assert (artifact / "flight_summary.csv").is_file()
    assert (artifact / "analysis_summary.md").is_file()
    assert (artifact / "effective_motion_reference_config.yaml").is_file()
    assert (artifact / "source_provenance.csv").is_file()
    assert (artifact / "environment.txt").is_file()


def test_crafted_short_edge_and_z_filter_accounting_is_reconcilable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short windows, discarded edges, and z rejection partition elapsed time explicitly."""
    config = load_motion_reference_config(REFERENCE_CONFIG_PATH)
    values = copy.deepcopy(config.values)
    record = copy.deepcopy(values["source"]["files"][0])
    record["id"] = "crafted"
    values["source"]["files"] = [record]
    values["splits"] = {
        "calibration_flight_ids": ["crafted"],
        "comparison_flight_ids": [],
    }
    values["analysis"].update(
        {
            "resample_dt_s": 0.02,
            "savgol_window_samples": 5,
            "savgol_polynomial_degree": 2,
            "discard_edge_samples": 1,
            "flight_only_min_z_m": 0.2,
        }
    )
    samples = np.array(
        [
            [0.0, 0.0, 0.0, 0.3],
            [20.0, 0.02, 0.0, 0.3],
            [40.0, 0.04, 0.0, 0.3],
            [60.0, np.nan, np.nan, np.nan],
            [80.0, 0.08, 0.0, 0.3],
            [100.0, 0.10, 0.0, 0.3],
            [120.0, 0.12, 0.0, 0.3],
            [140.0, 0.14, 0.0, 0.3],
            [160.0, 0.16, 0.0, 0.0],
            [180.0, 0.18, 0.0, 0.3],
            [200.0, 0.20, 0.0, 0.3],
            [220.0, 0.22, 0.0, 0.3],
            [240.0, 0.24, 0.0, 0.3],
        ],
        dtype=float,
    )
    monkeypatch.setattr(analysis_module, "load_numeric_motion_array", lambda _: samples)

    summary = analysis_module._summary_for_flight(record, values)

    assert summary.short_segment_count == 1
    assert summary.short_segment_drop_time_s == pytest.approx(0.04)
    assert summary.edge_discard_time_s == pytest.approx(0.04)
    assert summary.flight_filter_drop_sample_count == 3
    assert summary.flight_filter_drop_time_s == pytest.approx(0.08)
    assert summary.analysis_sample_count == 4
    assert summary.analysis_sample_exposure_s == pytest.approx(0.08)
    assert summary.valid_time_used_s == pytest.approx(0.04)
    assert summary.resample_tail_drop_time_s == pytest.approx(0.0)
    assert summary.time_accounting_residual_s == pytest.approx(0.0, abs=1e-10)
    assert summary.source_span_s == pytest.approx(
        summary.invalid_or_gap_drop_time_s
        + summary.short_segment_drop_time_s
        + summary.resample_tail_drop_time_s
        + summary.edge_discard_time_s
        + summary.flight_filter_drop_time_s
        + summary.valid_time_used_s
    )


def test_summary_markdown_uses_report_derived_split_ids() -> None:
    """Human-readable split labels must follow the report rather than task literals."""
    report = analyze_motion_reference(load_motion_reference_config(REFERENCE_CONFIG_PATH))
    calibration = replace(report.calibration_summaries[0], flight_id="calibration-custom")
    comparison = replace(report.comparison_summaries[0], flight_id="comparison-custom")
    custom_report = replace(
        report,
        calibration_summaries=(calibration,),
        comparison_summaries=(comparison,),
    )

    summary_markdown = analysis_module._summary_markdown(custom_report)

    assert "`calibration-custom`" in summary_markdown
    assert "`comparison-custom`" in summary_markdown
    assert "`mocap00`" not in summary_markdown
