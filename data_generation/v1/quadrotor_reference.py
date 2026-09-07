"""Source-grounded smooth reference planning for the Crazyflie scenario."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from omegaconf import OmegaConf


class ReferencePlanningError(ValueError):
    """Raised when an explicit smooth-reference setting cannot be honored."""


def _finite_float(value: object, *, name: str, positive: bool = False) -> float:
    """Read one finite numeric setting without a silent default."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ReferencePlanningError(f"{name} must be numeric") from error
    if not math.isfinite(numeric) or (positive and numeric <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ReferencePlanningError(f"{name} must be {qualifier}")
    return numeric


def _vector3(value: object, *, name: str) -> np.ndarray:
    """Convert one ENU vector while preserving the explicit three-axis contract."""
    try:
        vector = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ReferencePlanningError(f"{name} must be numeric") from error
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ReferencePlanningError(f"{name} must be a finite three-vector")
    return vector.copy()


def normalized_rest_to_rest_blend(s: float) -> tuple[float, float, float, float]:
    """Evaluate the seventh-order blend and its first three normalized derivatives.

    ``s`` is clamped to the segment interval.  Explicit endpoint returns avoid
    numerical residue and make the rest-to-rest boundary contract observable.
    """
    normalized_time = _finite_float(s, name="normalized time")
    normalized_time = min(max(normalized_time, 0.0), 1.0)
    if normalized_time == 0.0:
        return 0.0, 0.0, 0.0, 0.0
    if normalized_time == 1.0:
        return 1.0, 0.0, 0.0, 0.0
    s2 = normalized_time * normalized_time
    s3 = s2 * normalized_time
    s4 = s3 * normalized_time
    s5 = s4 * normalized_time
    s6 = s5 * normalized_time
    s7 = s6 * normalized_time
    blend = 35.0 * s4 - 84.0 * s5 + 70.0 * s6 - 20.0 * s7
    first = 140.0 * s3 - 420.0 * s4 + 420.0 * s5 - 140.0 * s6
    second = 420.0 * s2 - 1680.0 * s3 + 2100.0 * s4 - 840.0 * s5
    third = 840.0 * normalized_time - 5040.0 * s2 + 8400.0 * s3 - 4200.0 * s4
    return blend, first, second, third


@dataclass(frozen=True, slots=True)
class ReferenceState:
    """Position, velocity, acceleration, and jerk on one smooth ENU segment."""

    position_enu_m: np.ndarray
    velocity_enu_mps: np.ndarray
    acceleration_enu_mps2: np.ndarray
    jerk_enu_mps3: np.ndarray


@dataclass(frozen=True, slots=True)
class RestToRestSegment:
    """One finite seventh-order segment between two rest endpoints."""

    start_enu_m: np.ndarray
    end_enu_m: np.ndarray
    duration_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_enu_m", _vector3(self.start_enu_m, name="segment start"))
        object.__setattr__(self, "end_enu_m", _vector3(self.end_enu_m, name="segment end"))
        object.__setattr__(
            self,
            "duration_s",
            _finite_float(self.duration_s, name="segment duration", positive=True),
        )

    def evaluate(self, elapsed_s: float) -> ReferenceState:
        """Return the analytic P/V/A/jerk reference at one clamped segment time."""
        elapsed = _finite_float(elapsed_s, name="segment elapsed time")
        blend, first, second, third = normalized_rest_to_rest_blend(elapsed / self.duration_s)
        displacement = self.end_enu_m - self.start_enu_m
        return ReferenceState(
            position_enu_m=self.start_enu_m + displacement * blend,
            velocity_enu_mps=displacement * (first / self.duration_s),
            acceleration_enu_mps2=displacement * (second / self.duration_s**2),
            jerk_enu_mps3=displacement * (third / self.duration_s**3),
        )


@dataclass(frozen=True, slots=True)
class SourceGroundedReferenceSettings:
    """Explicit source-conditioned planning limits, distinct from airframe physics."""

    target_min_enu_m: np.ndarray
    target_max_enu_m: np.ndarray
    nominal_mean_speeds_mps: tuple[float, ...]
    minimum_leg_distance_m: float
    maximum_target_sampling_attempts: int
    minimum_duration_s: float
    maximum_duration_s: float
    maximum_peak_speed_mps: float
    maximum_peak_acceleration_mps2: float
    blend_peak_speed_factor: float
    blend_peak_acceleration_factor: float
    maximum_hold_duration_s: float
    settling_duration_s: float

    @classmethod
    def from_mapping(cls, config: dict[str, Any]) -> SourceGroundedReferenceSettings:
        """Resolve the named planner settings from a verified YAML configuration."""
        try:
            planner = config["planner"]
        except KeyError as error:
            raise ReferencePlanningError("motion reference config requires planner") from error
        if not isinstance(planner, dict) or planner.get("mode") != "source_grounded":
            raise ReferencePlanningError("planner mode must be source_grounded")
        bounds = planner.get("target_bounds_enu_m")
        if not isinstance(bounds, dict):
            raise ReferencePlanningError("planner requires target_bounds_enu_m")
        target_min = _vector3(bounds.get("minimum"), name="target lower bound")
        target_max = _vector3(bounds.get("maximum"), name="target upper bound")
        if not np.all(target_min < target_max):
            raise ReferencePlanningError("target bounds must have positive extent on every axis")
        speeds = planner.get("nominal_mean_speeds_mps")
        if not isinstance(speeds, list) or not speeds:
            raise ReferencePlanningError("planner requires nominal_mean_speeds_mps")
        parsed_speeds = tuple(
            _finite_float(value, name="nominal mean speed", positive=True) for value in speeds
        )
        attempts_value = planner.get("maximum_target_sampling_attempts")
        if isinstance(attempts_value, bool) or not isinstance(attempts_value, int):
            raise ReferencePlanningError("maximum_target_sampling_attempts must be an integer")
        if attempts_value <= 0:
            raise ReferencePlanningError("maximum_target_sampling_attempts must be positive")
        minimum_duration = _finite_float(
            planner.get("minimum_duration_s"), name="minimum_duration_s", positive=True
        )
        maximum_duration = _finite_float(
            planner.get("maximum_duration_s"), name="maximum_duration_s", positive=True
        )
        if minimum_duration > maximum_duration:
            raise ReferencePlanningError("planner duration bounds are invalid")
        return cls(
            target_min_enu_m=target_min,
            target_max_enu_m=target_max,
            nominal_mean_speeds_mps=parsed_speeds,
            minimum_leg_distance_m=_finite_float(
                planner.get("minimum_leg_distance_m"),
                name="minimum_leg_distance_m",
                positive=True,
            ),
            maximum_target_sampling_attempts=attempts_value,
            minimum_duration_s=minimum_duration,
            maximum_duration_s=maximum_duration,
            maximum_peak_speed_mps=_finite_float(
                planner.get("maximum_peak_speed_mps"),
                name="maximum_peak_speed_mps",
                positive=True,
            ),
            maximum_peak_acceleration_mps2=_finite_float(
                planner.get("maximum_peak_acceleration_mps2"),
                name="maximum_peak_acceleration_mps2",
                positive=True,
            ),
            blend_peak_speed_factor=_finite_float(
                planner.get("blend_peak_speed_factor"),
                name="blend_peak_speed_factor",
                positive=True,
            ),
            blend_peak_acceleration_factor=_finite_float(
                planner.get("blend_peak_acceleration_factor"),
                name="blend_peak_acceleration_factor",
                positive=True,
            ),
            maximum_hold_duration_s=_finite_float(
                planner.get("maximum_hold_duration_s"),
                name="maximum_hold_duration_s",
                positive=True,
            ),
            settling_duration_s=_finite_float(
                planner.get("settling_duration_s"),
                name="settling_duration_s",
                positive=True,
            ),
        )


def load_source_grounded_reference_settings(config_path: Path) -> SourceGroundedReferenceSettings:
    """Load one explicit YAML file for direct planner/unit-test use."""
    path = Path(config_path)
    if not path.is_file():
        raise ReferencePlanningError(f"motion reference config is missing: {path}")
    resolved = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(resolved, dict):
        raise ReferencePlanningError("motion reference config must be a mapping")
    return SourceGroundedReferenceSettings.from_mapping(resolved)


@dataclass(frozen=True, slots=True)
class PlannedReferenceLeg:
    """One fresh target and the smooth segment used to approach it."""

    index: int
    start_enu_m: np.ndarray
    target_enu_m: np.ndarray
    nominal_mean_speed_mps: float
    duration_s: float
    segment: RestToRestSegment


class SourceGroundedReferencePlanner:
    """Generate diverse, bounded 3D targets without replaying source trajectories."""

    def __init__(self, settings: SourceGroundedReferenceSettings, *, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
            raise ReferencePlanningError("reference planner seed must be an integer")
        self._settings = settings
        self._generator = np.random.default_rng(np.random.SeedSequence([int(seed), 1]))
        self._next_index = 0

    def _duration_s(self, distance_m: float, nominal_mean_speed_mps: float) -> float:
        """Honor average-speed and analytic P/V/A peak constraints together."""
        settings = self._settings
        duration = max(
            settings.minimum_duration_s,
            distance_m / nominal_mean_speed_mps,
            distance_m * settings.blend_peak_speed_factor / settings.maximum_peak_speed_mps,
            math.sqrt(
                distance_m
                * settings.blend_peak_acceleration_factor
                / settings.maximum_peak_acceleration_mps2
            ),
        )
        if duration > settings.maximum_duration_s:
            raise ReferencePlanningError(
                "source-grounded target requires a duration above maximum_duration_s"
            )
        return duration

    def next_leg(self, start_enu_m: np.ndarray) -> PlannedReferenceLeg:
        """Sample one nondegenerate target or fail after the configured finite attempts."""
        start = _vector3(start_enu_m, name="reference leg start")
        settings = self._settings
        for _ in range(settings.maximum_target_sampling_attempts):
            target = self._generator.uniform(settings.target_min_enu_m, settings.target_max_enu_m)
            distance_m = float(np.linalg.norm(target - start))
            if distance_m < settings.minimum_leg_distance_m:
                continue
            nominal_mean_speed_mps = float(self._generator.choice(settings.nominal_mean_speeds_mps))
            try:
                duration_s = self._duration_s(distance_m, nominal_mean_speed_mps)
            except ReferencePlanningError:
                continue
            segment = RestToRestSegment(start, target, duration_s)
            leg = PlannedReferenceLeg(
                index=self._next_index,
                start_enu_m=start.copy(),
                target_enu_m=target.copy(),
                nominal_mean_speed_mps=nominal_mean_speed_mps,
                duration_s=duration_s,
                segment=segment,
            )
            self._next_index += 1
            return leg
        raise ReferencePlanningError(
            "could not sample a source-grounded target within maximum_target_sampling_attempts"
        )


__all__ = [
    "PlannedReferenceLeg",
    "ReferencePlanningError",
    "ReferenceState",
    "RestToRestSegment",
    "SourceGroundedReferencePlanner",
    "SourceGroundedReferenceSettings",
    "load_source_grounded_reference_settings",
    "normalized_rest_to_rest_blend",
]
