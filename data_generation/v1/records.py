"""Physics-agnostic records accepted by the common v1 dataset writer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class SourceFileRecord:
    """Declared source-file provenance; hash verification belongs to the entry point."""

    path: str
    sha256: str
    url: str


@dataclass(frozen=True, slots=True)
class MotionEvent:
    """Object-neutral event audit record using the existing evaluation CSV fields."""

    event_id: str
    start_s: float
    end_s: float
    trigger_kind: str
    completed: bool
    completion_reason: str


@dataclass(frozen=True, slots=True)
class MotionEpisode:
    """Continuous object-neutral motion output before common v1 CSV serialization."""

    episode_id: str
    status: str
    failure_reason: str | None
    seed: int
    steps: np.ndarray
    t_s: np.ndarray
    position_enu_m: np.ndarray
    velocity_enu_mps: np.ndarray
    diagnostic_arrays: Mapping[str, np.ndarray]
    event_records: tuple[MotionEvent, ...]
