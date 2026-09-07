"""Tests for the headerless X8 flight-log reader and source provenance."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from data_generation.v1.source import SourceFormatError, read_x8_source_csv, verify_md5


def _row(offset: float) -> str:
    return ",".join(str(index + offset) for index in range(1, 42))


def test_headerless_x8_reader_maps_motion_fields_and_drops_gps_fields(tmp_path: Path) -> None:
    """README-indexed values must map to physical fields without exposing GPS columns."""
    path = tmp_path / "maneuver.csv"
    path.write_text(_row(0.0) + "\n" + _row(0.025) + "\n", encoding="utf-8")

    flight = read_x8_source_csv(path)

    assert len(flight.samples) == 2
    sample = flight.samples[0]
    assert sample.t_s == 1.0
    assert sample.elevator_rad == 2.0
    assert sample.aileron_rad == 3.0
    assert sample.throttle == 4.0
    assert sample.imu_acc_mps2 == (5.0, 6.0, 7.0)
    assert sample.body_velocity_mps == (17.0, 18.0, 19.0)
    assert sample.wind_ned_mps == (23.0, 24.0, 25.0)
    assert sample.indicated_speed_mps == 29.0
    assert sample.current_a == 38.0
    assert sample.voltage_v == 39.0
    assert not hasattr(sample, "gps_lon")
    assert not hasattr(sample, "gps_x")


def test_source_reader_rejects_non_41_column_or_non_monotonic_records(tmp_path: Path) -> None:
    """A malformed raw log must fail rather than shift motion fields silently."""
    short_path = tmp_path / "short.csv"
    short_path.write_text(",".join(str(index) for index in range(40)) + "\n", encoding="utf-8")

    with pytest.raises(SourceFormatError, match="41 columns"):
        read_x8_source_csv(short_path)

    time_path = tmp_path / "time.csv"
    time_path.write_text(_row(0.1) + "\n" + _row(0.0) + "\n", encoding="utf-8")

    with pytest.raises(SourceFormatError, match="strictly increasing"):
        read_x8_source_csv(time_path)


def test_source_checksum_mismatch_is_reported_before_motion_check(tmp_path: Path) -> None:
    """The exact raw source bytes must be checked before a replay is trusted."""
    path = tmp_path / "maneuver.csv"
    payload = _row(0.0).encode("utf-8")
    path.write_bytes(payload)
    expected_md5 = hashlib.md5(payload).hexdigest()

    verify_md5(path, expected_md5)

    with pytest.raises(SourceFormatError, match="MD5 mismatch"):
        verify_md5(path, "0" * 32)
