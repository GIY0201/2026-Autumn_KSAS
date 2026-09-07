"""Reader and provenance checks for the public Skywalker X8 source logs.

The source CSV files have no header.  Their 41-column order is copied from the
publisher's ``00_README.txt``.  GPS fields are intentionally skipped: source
replay is a motion check, not a geographic-position pipeline.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

X8_SOURCE_ID = "x8_dataverse_2024"
X8_DATASET_DOI = "10.18710/U4TLYV"
X8_README_MD5 = "4bc6c339fe46ad2ecac8d2d17d5eaa19"

SOURCE_COLUMN_NAMES = (
    "t",
    "elevator",
    "aileron",
    "throttle",
    "IMU_acc_x",
    "IMU_acc_y",
    "IMU_acc_z",
    "IMU_angvel_p",
    "IMU_angvel_q",
    "IMU_angvel_r",
    "EstimatedState_phi",
    "EstimatedState_theta",
    "EstimatedState_psi",
    "EstimatedState_p",
    "EstimatedState_q",
    "EstimatedState_r",
    "EstimatedState_u",
    "EstimatedState_v",
    "EstimatedState_w",
    "EstimatedState_vx",
    "EstimatedState_vy",
    "EstimatedState_vz",
    "EstimatedStreamVelocity_x",
    "EstimatedStreamVelocity_y",
    "EstimatedStreamVelocity_z",
    "EstimatedState_alpha",
    "EstimatedState_beta",
    "TrueSpeed",
    "IndicatedSpeed",
    "GPS_lon",
    "GPS_lat",
    "GPS_height",
    "GPS_x",
    "GPS_y",
    "GPS_z",
    "GPS_cog",
    "GPS_sog",
    "Current",
    "Voltage",
    "Temperature",
    "Pressure",
)
GPS_COLUMN_INDICES = tuple(range(29, 37))
X8_RAW_MD5 = {
    "training/lateral_121_1.csv": "d163a59d4da594f9c716990151159b3c",
    "training/lateral_121_2.csv": "091298ff063747e010298ea3e61451be",
    "training/lateral_121_3.csv": "92bdb4c97d16e05a0c0c4da868d72690",
    "training/lateral_doublet_1.csv": "a120940bc294cff231b9dd4d24577d34",
    "training/lateral_doublet_2.csv": "cec7f99412becc59ba5d2b6d36214912",
    "training/lateral_doublet_3.csv": "338a13a232717c5f397fe14c53399242",
    "training/longitudinal_3211_1.csv": "655edda35560ca299dac545ff7835dab",
    "training/longitudinal_3211_2.csv": "caca49f8743caf59cc26bf756c914554",
    "training/longitudinal_3211_3.csv": "f7fe10687cd6dac07a0d0387150fac9e",
    "training/longitudinal_3211_4.csv": "52bff9a02a5df53b0f4a5b41625ff47f",
    "training/longitudinal_doublet_1.csv": "9e9b1bd40bec64a96a30cfe44bf385c3",
    "training/longitudinal_doublet_2.csv": "03ad563a6e491674c2b763b99a0ee63e",
    "training/longitudinal_doublet_3.csv": "01f4a2fe2fa98937a090c79e117aba3e",
    "validation/lateral_121_4.csv": "e9c734f9f7133d79fa568f6bc200d8f5",
    "validation/lateral_doublet_4.csv": "2d2169e713e65f23e373db75b5122fe1",
    "validation/longitudinal_3211_5.csv": "0d87838dc9de2e75e7a8db8beca0d3c8",
    "validation/longitudinal_doublet_4.csv": "2920b5241fef83408fb1e48955372f2f",
}

assert len(SOURCE_COLUMN_NAMES) == 41
assert len(GPS_COLUMN_INDICES) == 8
assert len(X8_RAW_MD5) == 17


class SourceFormatError(ValueError):
    """Raised for a malformed or unverifiable X8 source record."""


@dataclass(frozen=True, slots=True)
class X8SourceSample:
    """Motion-related fields retained from one headerless source row."""

    t_s: float
    elevator_rad: float
    aileron_rad: float
    throttle: float
    imu_acc_mps2: tuple[float, float, float]
    imu_pqr_radps: tuple[float, float, float]
    euler_rad: tuple[float, float, float]
    estimated_pqr_radps: tuple[float, float, float]
    body_velocity_mps: tuple[float, float, float]
    velocity_ned_mps: tuple[float, float, float]
    wind_ned_mps: tuple[float, float, float]
    alpha_rad: float
    beta_rad: float
    true_speed_mps: float
    indicated_speed_mps: float
    current_a: float
    voltage_v: float
    temperature_c: float
    pressure_mbar: float


@dataclass(frozen=True, slots=True)
class X8SourceFlight:
    """A source clip with its original path and retained motion samples."""

    path: Path
    samples: tuple[X8SourceSample, ...]


def _parse_row(row: list[str], line_number: int) -> list[float]:
    if len(row) != len(SOURCE_COLUMN_NAMES):
        raise SourceFormatError(
            f"line {line_number} has {len(row)} columns; expected 41 columns from 00_README.txt"
        )
    try:
        values = [float(value) for value in row]
    except ValueError as error:
        raise SourceFormatError(f"line {line_number} contains a non-numeric field") from error
    if not all(math.isfinite(value) for value in values):
        raise SourceFormatError(f"line {line_number} contains a non-finite field")
    return values


def _sample_from_values(values: list[float]) -> X8SourceSample:
    """Map source positions to the motion interface while deliberately omitting GPS."""
    return X8SourceSample(
        t_s=values[0],
        elevator_rad=values[1],
        aileron_rad=values[2],
        throttle=values[3],
        imu_acc_mps2=(values[4], values[5], values[6]),
        imu_pqr_radps=(values[7], values[8], values[9]),
        euler_rad=(values[10], values[11], values[12]),
        estimated_pqr_radps=(values[13], values[14], values[15]),
        body_velocity_mps=(values[16], values[17], values[18]),
        velocity_ned_mps=(values[19], values[20], values[21]),
        wind_ned_mps=(values[22], values[23], values[24]),
        alpha_rad=values[25],
        beta_rad=values[26],
        true_speed_mps=values[27],
        indicated_speed_mps=values[28],
        current_a=values[37],
        voltage_v=values[38],
        temperature_c=values[39],
        pressure_mbar=values[40],
    )


def read_x8_source_csv(path: Path) -> X8SourceFlight:
    """Read one raw, headerless X8 CSV and retain only motion-check fields."""
    if not path.is_file():
        raise SourceFormatError(f"source CSV is missing: {path}")
    samples: list[X8SourceSample] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader, start=1):
            if not row:
                raise SourceFormatError(f"line {line_number} is empty")
            values = _parse_row(row, line_number)
            sample = _sample_from_values(values)
            if samples and sample.t_s <= samples[-1].t_s:
                raise SourceFormatError("source timestamps must be strictly increasing")
            samples.append(sample)
    if not samples:
        raise SourceFormatError("source CSV is empty")
    return X8SourceFlight(path=path, samples=tuple(samples))


def verify_md5(path: Path, expected_md5: str) -> None:
    """Verify the publisher-recorded MD5 before treating a raw log as source data."""
    if not path.is_file():
        raise SourceFormatError(f"source file is missing: {path}")
    actual_md5 = hashlib.md5(path.read_bytes()).hexdigest()
    if actual_md5.lower() != expected_md5.lower():
        raise SourceFormatError(
            f"MD5 mismatch for {path.name}: expected {expected_md5}, got {actual_md5}"
        )


def verify_known_source_tree(raw_directory: Path) -> tuple[Path, ...]:
    """Check the published 13 TRAIN and 4 VALIDATION CSV checksums exactly once."""
    verified: list[Path] = []
    for relative_name, expected_md5 in X8_RAW_MD5.items():
        path = raw_directory / relative_name
        verify_md5(path, expected_md5)
        verified.append(path)
    return tuple(verified)
