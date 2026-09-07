"""Dependency-free provenance checks shared by source-grounded motion callers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _source_file_pairs(records: object, *, name: str) -> set[tuple[object, object]]:
    """Return unique path/digest evidence pairs from one metadata record list."""
    if not isinstance(records, list):
        raise ValueError(f"{name} must be a list")
    pairs: set[tuple[object, object]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError(f"{name} must contain mappings")
        path = record.get("path")
        sha256 = record.get("sha256")
        if not isinstance(path, str) or not isinstance(sha256, str):
            raise ValueError(f"{name} requires path and sha256 strings")
        pairs.add((path, sha256))
    if len(pairs) != len(records):
        raise ValueError(f"{name} must not repeat path/hash records")
    return pairs


def validate_source_grounded_metadata(
    motion_reference: Mapping[str, object], config_values: Mapping[str, Any]
) -> None:
    """Require profile source evidence and ordered split IDs to match loaded config values."""
    source = config_values.get("source")
    splits = config_values.get("splits")
    if not isinstance(source, Mapping) or not isinstance(splits, Mapping):
        raise ValueError("source-grounded motion reference config requires source and splits")
    if motion_reference.get("source_id") != source.get("source_id"):
        raise ValueError("source-grounded motion_reference source_id does not match config")
    metadata_files = motion_reference.get("source_files")
    config_files = source.get("files")
    if not isinstance(metadata_files, list) or not isinstance(config_files, list):
        raise ValueError("source-grounded motion_reference requires matching source_files metadata")
    if len(metadata_files) != len(config_files):
        raise ValueError("source-grounded motion_reference requires matching source_files metadata")
    metadata_pairs = _source_file_pairs(
        metadata_files,
        name="source-grounded motion_reference source_files",
    )
    config_pairs = _source_file_pairs(
        config_files,
        name="source-grounded motion reference config source.files",
    )
    if metadata_pairs != config_pairs:
        raise ValueError("source-grounded motion_reference source_files do not match config")
    for field in ("calibration_flight_ids", "comparison_flight_ids"):
        profile_ids = motion_reference.get(field)
        config_ids = splits.get(field)
        if not isinstance(profile_ids, list) or not all(
            isinstance(value, str) for value in profile_ids
        ):
            raise ValueError(f"source-grounded motion_reference requires {field}")
        if not isinstance(config_ids, list) or not all(
            isinstance(value, str) for value in config_ids
        ):
            raise ValueError(f"source-grounded motion reference config requires {field}")
        if profile_ids != config_ids:
            raise ValueError(f"source-grounded motion_reference {field} does not match config")


__all__ = ["validate_source_grounded_metadata"]
