"""Immutable records defined by the X8-GEN-V1 file contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EpisodeMetadata:
    """Public metadata describing one generated episode."""

    episode_id: str
    split: str
    duration_s: float
    sample_count: int
    dt_s: float
    identity_label: str


@dataclass(frozen=True, slots=True)
class Observation:
    """One public, noisy position observation at a fixed output step."""

    episode_id: str
    variant_id: str
    step: int
    t_s: float
    x_m: float
    y_m: float
    z_m: float
    sigma_x_m: float
    sigma_y_m: float
    sigma_z_m: float
    valid: int


@dataclass(frozen=True, slots=True)
class TruthPoint:
    """One evaluation-only simulated truth sample."""

    episode_id: str
    step: int
    t_s: float
    x_m: float
    y_m: float
    z_m: float
    vx_mps: float
    vy_mps: float
    vz_mps: float
    ax_mps2: float
    ay_mps2: float
    az_mps2: float


@dataclass(frozen=True, slots=True)
class PublicDataset:
    """Public-only dataset; this type intentionally has no truth or command field."""

    episodes: tuple[EpisodeMetadata, ...]
    observations: tuple[Observation, ...]

    @property
    def episode_ids(self) -> tuple[str, ...]:
        """Return public episode IDs in the declared metadata order."""
        return tuple(episode.episode_id for episode in self.episodes)
