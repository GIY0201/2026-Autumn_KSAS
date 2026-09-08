"""Validation and explicit parsing for constrained point-mass profiles."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S

from .point_mass import PointMassLimits, PointMassState, PointMassTarget, advance_point_mass

_OBJECT_IDS = {"x8_fixed_wing", "quadrotor"}
_ROLES = {
    "source_reference",
    "legacy_simulation_design",
    "derived",
    "new_simulation_design",
}
_MOTION_FIELDS = {
    "integration_dt_s",
    "minimum_horizontal_speed_mps",
    "maximum_horizontal_speed_mps",
    "maximum_vertical_speed_mps",
    "maximum_track_turn_rate_rad_s",
    "horizontal_acceleration_max_mps2",
    "tangential_acceleration_max_mps2",
    "vertical_acceleration_max_mps2",
}
_INITIAL_STATE_FIELDS = {
    "position_enu_m",
    "horizontal_speed_mps",
    "track_heading_deg",
    "vertical_speed_mps",
    "track_turn_rate_rad_s",
}
_COMMAND_FIELDS = {
    "command_id",
    "duration_s",
    "target_horizontal_speed_mps",
    "target_vertical_speed_mps",
    "target_track_turn_rate_rad_s",
}
_RUNTIME_TIME_EPSILON_S = 1e-12


@dataclass(frozen=True, slots=True)
class PointMassCommand:
    """One explicit Stage 1 target held for a configured duration or window end."""

    command_id: str
    duration_s: float | None
    target: PointMassTarget


def _finite(value: object, *, name: str, positive: bool = False) -> float:
    """Read an explicitly supplied finite scalar without coercing booleans."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return numeric


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _range(value: object, *, name: str) -> tuple[float, float]:
    """Require a two-value inclusive numeric range rather than defaulting one end."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise ValueError(f"{name} must be a two-value range")
    lower = _finite(value[0], name=f"{name}[0]")
    upper = _finite(value[1], name=f"{name}[1]")
    if lower > upper:
        raise ValueError(f"{name} has an inverted range")
    return lower, upper


def _position_bounds(value: object) -> tuple[np.ndarray, np.ndarray]:
    bounds = _mapping(value, name="initial_state.position_enu_m")
    if set(bounds) != {"minimum", "maximum"}:
        raise ValueError("initial_state.position_enu_m requires minimum and maximum")
    try:
        minimum = np.asarray(bounds["minimum"], dtype=float)
        maximum = np.asarray(bounds["maximum"], dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("initial_state.position_enu_m bounds must be numeric") from error
    if (
        minimum.shape != (3,)
        or maximum.shape != (3,)
        or not np.all(np.isfinite(minimum))
        or not np.all(np.isfinite(maximum))
        or np.any(minimum > maximum)
    ):
        raise ValueError("initial_state.position_enu_m bounds are invalid")
    return minimum, maximum


def _provenance_record(value: object, *, name: str) -> None:
    record = _mapping(value, name=name)
    if set(record) != {"role", "basis"}:
        raise ValueError(f"{name} requires role and basis")
    role = record["role"]
    basis = record["basis"]
    if role not in _ROLES or not isinstance(basis, str) or not basis.strip():
        raise ValueError(f"{name} has unsupported parameter provenance")


def _validate_parameter_provenance(profile: Mapping[str, object]) -> None:
    provenance = _mapping(profile.get("parameter_provenance"), name="parameter_provenance")
    if set(provenance) != {"motion", "initial_state", "commands"}:
        raise ValueError("parameter_provenance must cover motion, initial_state, and commands")
    motion = _mapping(provenance["motion"], name="parameter_provenance.motion")
    expected_motion = set(_MOTION_FIELDS)
    if profile["object_id"] == "x8_fixed_wing":
        expected_motion.add("maximum_track_path_angle_deg")
    if set(motion) != expected_motion:
        raise ValueError("parameter_provenance.motion must cover every motion setting")
    for name, record in motion.items():
        _provenance_record(record, name=f"parameter_provenance.motion.{name}")
    initial = _mapping(provenance["initial_state"], name="parameter_provenance.initial_state")
    if set(initial) != _INITIAL_STATE_FIELDS:
        raise ValueError("parameter_provenance.initial_state must cover every initial state")
    for name, record in initial.items():
        _provenance_record(record, name=f"parameter_provenance.initial_state.{name}")
    commands = _mapping(provenance["commands"], name="parameter_provenance.commands")
    command_ids = [item["command_id"] for item in _commands(profile)]
    if set(commands) != set(command_ids):
        raise ValueError("parameter_provenance.commands must cover every command ID")
    for command_id, record in commands.items():
        _provenance_record(record, name=f"parameter_provenance.commands.{command_id}")


def point_mass_limits_from_profile(profile: Mapping[str, object]) -> PointMassLimits:
    """Build the kernel limits from a previously validated explicit profile."""
    motion = _mapping(profile.get("motion"), name="motion")
    path_angle_deg = motion.get("maximum_track_path_angle_deg")
    path_angle_rad = None if path_angle_deg is None else math.radians(float(path_angle_deg))
    return PointMassLimits(
        minimum_horizontal_speed_mps=float(motion["minimum_horizontal_speed_mps"]),
        maximum_horizontal_speed_mps=float(motion["maximum_horizontal_speed_mps"]),
        maximum_vertical_speed_mps=float(motion["maximum_vertical_speed_mps"]),
        maximum_track_turn_rate_rad_s=float(motion["maximum_track_turn_rate_rad_s"]),
        horizontal_acceleration_max_mps2=float(motion["horizontal_acceleration_max_mps2"]),
        tangential_acceleration_max_mps2=float(motion["tangential_acceleration_max_mps2"]),
        vertical_acceleration_max_mps2=float(motion["vertical_acceleration_max_mps2"]),
        maximum_track_path_angle_rad=path_angle_rad,
    )


def _commands(profile: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    commands = profile.get("commands")
    if isinstance(commands, (str, bytes)) or not isinstance(commands, Sequence) or not commands:
        raise ValueError("commands must be a nonempty sequence")
    normalized: list[Mapping[str, object]] = []
    seen_ids: set[str] = set()
    finite_duration_s = 0.0
    for index, command_value in enumerate(commands):
        command = _mapping(command_value, name=f"commands[{index}]")
        if set(command) != _COMMAND_FIELDS:
            raise ValueError(f"commands[{index}] has unsupported or missing fields")
        command_id = command["command_id"]
        if not isinstance(command_id, str) or not command_id or command_id in seen_ids:
            raise ValueError("command_id values must be nonempty and unique")
        seen_ids.add(command_id)
        duration = command["duration_s"]
        if duration is None:
            if index != len(commands) - 1:
                raise ValueError("only the final command may end at the observation window")
        else:
            finite_duration_s += _finite(
                duration,
                name=f"commands[{index}].duration_s",
                positive=True,
            )
        for field in (
            "target_horizontal_speed_mps",
            "target_vertical_speed_mps",
            "target_track_turn_rate_rad_s",
        ):
            _finite(command[field], name=f"commands[{index}].{field}")
        normalized.append(command)
    if finite_duration_s >= DURATION_S:
        raise ValueError(
            "finite point-mass command durations must leave an observation-window tail"
        )
    return tuple(normalized)


def point_mass_commands_from_profile(profile: Mapping[str, object]) -> tuple[PointMassCommand, ...]:
    """Parse profile commands after structural validation, without inventing defaults."""
    return tuple(
        PointMassCommand(
            command_id=str(command["command_id"]),
            duration_s=None if command["duration_s"] is None else float(command["duration_s"]),
            target=PointMassTarget(
                horizontal_speed_mps=float(command["target_horizontal_speed_mps"]),
                vertical_speed_mps=float(command["target_vertical_speed_mps"]),
                track_turn_rate_rad_s=float(command["target_track_turn_rate_rad_s"]),
            ),
        )
        for command in _commands(profile)
    )


def sample_initial_point_mass_state(seed: int, profile: Mapping[str, object]) -> PointMassState:
    """Sample only explicitly ranged initial conditions from one uint32 seed."""
    if isinstance(seed, bool) or not isinstance(seed, Integral) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be a uint32 integer")
    initial = _mapping(profile.get("initial_state"), name="initial_state")
    minimum, maximum = _position_bounds(initial["position_enu_m"])
    speed_range = _range(initial["horizontal_speed_mps"], name="initial_state.horizontal_speed_mps")
    heading_range = _range(initial["track_heading_deg"], name="initial_state.track_heading_deg")
    vertical_range = _range(initial["vertical_speed_mps"], name="initial_state.vertical_speed_mps")
    turn_range = _range(
        initial["track_turn_rate_rad_s"], name="initial_state.track_turn_rate_rad_s"
    )
    generator = np.random.default_rng(np.random.SeedSequence([int(seed), 41]))
    return PointMassState(
        position_enu_m=generator.uniform(minimum, maximum),
        horizontal_speed_mps=float(generator.uniform(*speed_range)),
        track_heading_rad=math.radians(float(generator.uniform(*heading_range))),
        vertical_speed_mps=float(generator.uniform(*vertical_range)),
        track_turn_rate_rad_s=float(generator.uniform(*turn_range)),
    )


def validate_point_mass_profile(profile: dict) -> None:
    """Reject incomplete, unprovenanced, or dynamically impossible point-mass profiles."""
    if not isinstance(profile, dict):
        raise ValueError("point_mass profile must be a mapping")
    if profile.get("engine") != "point_mass":
        raise ValueError("point_mass profile requires engine: point_mass")
    object_id = profile.get("object_id")
    if object_id not in _OBJECT_IDS:
        raise ValueError("point_mass engine supports only fixed-wing or quadrotor objects")
    metadata = _mapping(profile.get("metadata"), name="point_mass metadata")
    if metadata.get("motion_abstraction") != "constrained_point_mass_kinematics":
        raise ValueError("point_mass metadata must declare constrained_point_mass_kinematics")
    if not all(
        isinstance(metadata.get(field), str) and metadata[field].strip()
        for field in ("operating_parameter_role", "source_model_role")
    ):
        raise ValueError("point_mass metadata requires explicit parameter and source roles")
    source_evidence = profile.get("source_evidence")
    if not isinstance(source_evidence, list) or not source_evidence:
        raise ValueError("point_mass profile requires source_evidence")
    for index, evidence in enumerate(source_evidence):
        record = _mapping(evidence, name=f"source_evidence[{index}]")
        if set(record) != {"location", "statement", "use"} or not all(
            isinstance(value, str) and value.strip() for value in record.values()
        ):
            raise ValueError("point_mass source_evidence must be complete text records")
    motion = _mapping(profile.get("motion"), name="motion")
    required_motion = set(_MOTION_FIELDS)
    if object_id == "x8_fixed_wing":
        required_motion.add("maximum_track_path_angle_deg")
    if set(motion) != required_motion:
        raise ValueError("point_mass motion settings must be explicit and complete")
    for field in _MOTION_FIELDS:
        _finite(
            motion[field],
            name=f"motion.{field}",
            positive=field != "minimum_horizontal_speed_mps",
        )
    minimum_speed = float(motion["minimum_horizontal_speed_mps"])
    maximum_speed = float(motion["maximum_horizontal_speed_mps"])
    if minimum_speed < 0.0 or maximum_speed < minimum_speed:
        raise ValueError("point_mass horizontal speed bounds are invalid")
    if object_id == "x8_fixed_wing" and minimum_speed <= 0.0:
        raise ValueError("fixed-wing minimum horizontal speed must be positive")
    if object_id == "x8_fixed_wing":
        _finite(
            motion["maximum_track_path_angle_deg"],
            name="motion.maximum_track_path_angle_deg",
            positive=True,
        )
    initial = _mapping(profile.get("initial_state"), name="initial_state")
    if set(initial) != _INITIAL_STATE_FIELDS:
        raise ValueError("point_mass initial_state must be explicit and complete")
    _position_bounds(initial["position_enu_m"])
    initial_speed = _range(
        initial["horizontal_speed_mps"], name="initial_state.horizontal_speed_mps"
    )
    _range(initial["track_heading_deg"], name="initial_state.track_heading_deg")
    initial_vertical_speed = _range(
        initial["vertical_speed_mps"], name="initial_state.vertical_speed_mps"
    )
    initial_turn_rate = _range(
        initial["track_turn_rate_rad_s"], name="initial_state.track_turn_rate_rad_s"
    )
    if initial_speed[0] < minimum_speed or initial_speed[1] > maximum_speed:
        raise ValueError("initial horizontal speed range exceeds point-mass limits")
    if max(abs(value) for value in initial_vertical_speed) > float(
        motion["maximum_vertical_speed_mps"]
    ):
        raise ValueError("initial vertical speed range exceeds point-mass limits")
    if max(abs(value) for value in initial_turn_rate) > float(
        motion["maximum_track_turn_rate_rad_s"]):
        raise ValueError("initial track turn-rate range exceeds point-mass limits")
    maximum_initial_turn_rate = max(abs(value) for value in initial_turn_rate)
    if initial_speed[1] * maximum_initial_turn_rate > float(
        motion["horizontal_acceleration_max_mps2"]
    ) + 1e-12:
        raise ValueError(
            "initial track turn-rate range exceeds the shared horizontal acceleration budget"
        )
    if object_id == "x8_fixed_wing":
        maximum_path_angle_rad = math.radians(float(motion["maximum_track_path_angle_deg"]))
        maximum_initial_path_angle = math.atan2(
            max(abs(value) for value in initial_vertical_speed), initial_speed[0]
        )
        if maximum_initial_path_angle > maximum_path_angle_rad:
            raise ValueError("initial track path angle exceeds point-mass limits")
    integration_dt_s = float(motion["integration_dt_s"])
    if integration_dt_s <= _RUNTIME_TIME_EPSILON_S:
        raise ValueError("point_mass integration_dt_s must exceed the runtime time epsilon")
    ratio = OUTPUT_DT_S / integration_dt_s
    if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("point_mass integration_dt_s must divide the output grid exactly")
    limits = point_mass_limits_from_profile(profile)
    initial_state = sample_initial_point_mass_state(0, profile)
    for command in point_mass_commands_from_profile(profile):
        advance_point_mass(initial_state, command.target, limits, integration_dt_s)
    _validate_parameter_provenance(profile)
