"""Validated command schedules for the reduced-motion VTOL/helicopter engine."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S


@dataclass(frozen=True, slots=True)
class MotionLimits:
    """Kinematic simulation settings, not aircraft-device coefficients."""

    dt_s: float
    horizontal_speed_max_mps: float
    vertical_speed_max_mps: float
    horizontal_accel_max_mps2: float
    vertical_accel_max_mps2: float
    turn_rate_max_rad_s: float
    speed_response_s: float
    vertical_response_s: float
    turn_response_s: float


@dataclass(frozen=True, slots=True)
class MotionMode:
    """One named simulation command range selected per scheduled segment."""

    name: str
    mode_class: str
    duration_s: tuple[float, float]
    target_speed_mps: tuple[float, float]
    target_vertical_speed_mps: tuple[float, float]
    target_turn_rate_rad_s: tuple[float, float]


@dataclass(frozen=True, slots=True)
class ScheduledMotionCommand:
    """One sampled command window; state is deliberately not part of this record."""

    index: int
    mode: MotionMode
    start_s: float
    end_s: float
    target_speed_mps: float
    target_vertical_speed_mps: float
    target_turn_rate_rad_s: float
    was_cut_off: bool


@dataclass(frozen=True, slots=True)
class MotionScenario:
    """Fully resolved schedule and initial kinematic state for one deterministic seed."""

    object_id: str
    initial_position_enu_m: np.ndarray
    initial_speed_mps: float
    initial_heading_rad: float
    initial_vertical_speed_mps: float
    initial_turn_rate_rad_s: float
    hover_speed_tolerance_mps: float
    target_tolerance_speed_mps: float
    target_tolerance_vertical_speed_mps: float
    target_tolerance_turn_rate_rad_s: float
    cruise_min_speed_mps: float | None
    mode_index_map: Mapping[str, int]
    commands: tuple[ScheduledMotionCommand, ...]


@dataclass(frozen=True, slots=True)
class PhaseExecutionPolicy:
    """Explicit opt-in engineering policy for phase-aware VTOL experiments."""

    policy_id: str
    reference_blend_s: float
    settling_dwell_s: float
    max_extension_s: float


@dataclass(frozen=True, slots=True)
class PhaseMotionTarget:
    """One sampled target whose phase cannot advance on nominal time alone."""

    mode: MotionMode
    reference_duration_s: float
    target_speed_mps: float
    target_vertical_speed_mps: float
    target_turn_rate_rad_s: float


@dataclass(frozen=True, slots=True)
class PhaseMotionScenario:
    """Parsed, fully sampled state-gated VTOL phase sequence for one seed."""

    object_id: str
    initial_position_enu_m: np.ndarray
    initial_speed_mps: float
    initial_heading_rad: float
    initial_vertical_speed_mps: float
    initial_turn_rate_rad_s: float
    hover_speed_tolerance_mps: float
    target_tolerance_speed_mps: float
    target_tolerance_vertical_speed_mps: float
    target_tolerance_turn_rate_rad_s: float
    hover_turn_rate_tolerance_rad_s: float
    cruise_min_speed_mps: float | None
    mode_index_map: Mapping[str, int]
    policy: PhaseExecutionPolicy
    targets: tuple[PhaseMotionTarget, ...]


_MOTION_FIELDS = frozenset(
    {
        "dt_s",
        "horizontal_speed_max_mps",
        "vertical_speed_max_mps",
        "horizontal_accel_max_mps2",
        "vertical_accel_max_mps2",
        "turn_rate_max_deg_s",
        "speed_response_s",
        "vertical_response_s",
        "turn_response_s",
    }
)
_EXPERIMENT_FIELDS = frozenset(
    {
        "role",
        "initial_position_enu_m",
        "initial_speed_mps",
        "initial_track_heading_deg",
        "initial_heading_deg",
        "initial_vertical_speed_mps",
        "initial_turn_rate_deg_s",
        "cruise_min_speed_mps",
        "hover_speed_tolerance_mps",
        "target_tolerance_speed_mps",
        "target_tolerance_vertical_speed_mps",
        "target_tolerance_turn_rate_deg_s",
        "mode_index_map",
        "modes",
        "ordering_alternatives",
        "execution_policy",
    }
)
_MODE_FIELDS = frozenset(
    {
        "mode_class",
        "duration_s",
        "target_speed_mps",
        "target_vertical_speed_mps",
        "target_turn_rate_deg_s",
    }
)
_PHASE_POLICY_FIELDS = frozenset(
    {"id", "reference_blend_s", "settling_dwell_s", "max_extension_s"}
)
_PHASE_POLICY_ID = "vtol_phase_aware_v1"


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite numeric value")
    if positive and number <= 0.0:
        raise ValueError(f"{label} must be positive")
    return number


def _bounded_range(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError(f"{label} must be a two-value range")
    lower = _finite_number(value[0], f"{label}[0]")
    upper = _finite_number(value[1], f"{label}[1]")
    if lower > upper:
        raise ValueError(f"{label} must be ordered")
    return lower, upper


def _parse_phase_execution_policy(
    value: object,
    *,
    object_id: str,
) -> PhaseExecutionPolicy | None:
    """Parse the optional policy without changing legacy schedule behavior."""
    if value is None:
        return None
    if object_id != "vtol":
        raise ValueError("execution_policy is supported only for VTOL bounded motion")
    if not isinstance(value, Mapping):
        raise ValueError("experiment.execution_policy must be a mapping")
    keys = set(value)
    missing = _PHASE_POLICY_FIELDS - keys
    unknown = keys - _PHASE_POLICY_FIELDS
    if missing or unknown:
        raise ValueError(
            "experiment.execution_policy fields are invalid; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    policy_id = value["id"]
    if policy_id != _PHASE_POLICY_ID:
        raise ValueError(
            f"unsupported VTOL execution_policy {policy_id!r}; expected {_PHASE_POLICY_ID!r}"
        )
    return PhaseExecutionPolicy(
        policy_id=policy_id,
        reference_blend_s=_finite_number(
            value["reference_blend_s"],
            "experiment.execution_policy.reference_blend_s",
            positive=True,
        ),
        settling_dwell_s=_finite_number(
            value["settling_dwell_s"],
            "experiment.execution_policy.settling_dwell_s",
            positive=True,
        ),
        max_extension_s=_finite_number(
            value["max_extension_s"],
            "experiment.execution_policy.max_extension_s",
            positive=True,
        ),
    )


def _integer_ratio(numerator: float, denominator: float, *, label: str) -> int:
    ratio = numerator / denominator
    rounded = round(ratio)
    if rounded <= 0 or not math.isclose(ratio, rounded, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{label} must divide the declared {numerator:g}-second output step")
    return int(rounded)


def parse_motion_limits(motion: object) -> MotionLimits:
    """Validate explicit bounded-motion settings without an implicit default."""
    if not isinstance(motion, Mapping):
        raise ValueError("bounded motion profile requires a motion mapping")
    keys = set(motion)
    missing = _MOTION_FIELDS - keys
    unknown = keys - _MOTION_FIELDS
    if missing or unknown:
        raise ValueError(
            f"motion fields are invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    dt_s = _finite_number(motion["dt_s"], "motion.dt_s", positive=True)
    _integer_ratio(OUTPUT_DT_S, dt_s, label="motion.dt_s")
    turn_rate_max_deg_s = _finite_number(
        motion["turn_rate_max_deg_s"], "motion.turn_rate_max_deg_s", positive=True
    )
    return MotionLimits(
        dt_s=dt_s,
        horizontal_speed_max_mps=_finite_number(
            motion["horizontal_speed_max_mps"], "motion.horizontal_speed_max_mps", positive=True
        ),
        vertical_speed_max_mps=_finite_number(
            motion["vertical_speed_max_mps"], "motion.vertical_speed_max_mps", positive=True
        ),
        horizontal_accel_max_mps2=_finite_number(
            motion["horizontal_accel_max_mps2"],
            "motion.horizontal_accel_max_mps2",
            positive=True,
        ),
        vertical_accel_max_mps2=_finite_number(
            motion["vertical_accel_max_mps2"], "motion.vertical_accel_max_mps2", positive=True
        ),
        turn_rate_max_rad_s=math.radians(turn_rate_max_deg_s),
        speed_response_s=_finite_number(
            motion["speed_response_s"], "motion.speed_response_s", positive=True
        ),
        vertical_response_s=_finite_number(
            motion["vertical_response_s"], "motion.vertical_response_s", positive=True
        ),
        turn_response_s=_finite_number(
            motion["turn_response_s"], "motion.turn_response_s", positive=True
        ),
    )


def _initial_heading_range(experiment: Mapping[str, object]) -> tuple[float, float]:
    has_track = "initial_track_heading_deg" in experiment
    has_legacy = "initial_heading_deg" in experiment
    if has_track == has_legacy:
        raise ValueError("experiment requires exactly one initial track heading setting")
    value = experiment["initial_track_heading_deg" if has_track else "initial_heading_deg"]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _bounded_range(value, "experiment.initial_track_heading_deg")
    fixed = _finite_number(value, "experiment.initial_track_heading_deg")
    return fixed, fixed


def _initial_position(experiment: Mapping[str, object]) -> np.ndarray:
    value = experiment.get("initial_position_enu_m")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError("experiment.initial_position_enu_m must contain three finite values")
    position = np.asarray(
        [_finite_number(item, "experiment.initial_position_enu_m") for item in value], dtype=float
    )
    assert position.shape == (3,)
    return position


def _parse_modes(
    raw_modes: object,
    *,
    object_id: str,
    limits: MotionLimits,
    cruise_min_speed_mps: float | None,
) -> dict[str, MotionMode]:
    if not isinstance(raw_modes, Mapping) or not raw_modes:
        raise ValueError("experiment requires named modes")
    modes: dict[str, MotionMode] = {}
    for name, raw_mode in raw_modes.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError("mode names must be readable English identifiers")
        if not isinstance(raw_mode, Mapping):
            raise ValueError(f"mode {name} must be a mapping")
        unknown = set(raw_mode) - _MODE_FIELDS
        missing = _MODE_FIELDS - set(raw_mode)
        if missing or unknown:
            raise ValueError(
                f"mode {name} fields are invalid; missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        mode_class = raw_mode["mode_class"]
        if not isinstance(mode_class, str) or not mode_class:
            raise ValueError(f"mode {name} requires a non-empty mode_class")
        duration_s = _bounded_range(raw_mode["duration_s"], f"mode {name}.duration_s")
        if duration_s[0] <= 0.0:
            raise ValueError(f"mode {name}.duration_s must be positive")
        speed = _bounded_range(raw_mode["target_speed_mps"], f"mode {name}.target_speed_mps")
        vertical = _bounded_range(
            raw_mode["target_vertical_speed_mps"], f"mode {name}.target_vertical_speed_mps"
        )
        turn_deg = _bounded_range(
            raw_mode["target_turn_rate_deg_s"], f"mode {name}.target_turn_rate_deg_s"
        )
        if speed[0] < 0.0 or speed[1] > limits.horizontal_speed_max_mps:
            raise ValueError(f"mode {name} target_speed_mps exceeds the supported range")
        if max(abs(vertical[0]), abs(vertical[1])) > limits.vertical_speed_max_mps:
            raise ValueError(f"mode {name} target_vertical_speed_mps exceeds the supported range")
        if max(abs(turn_deg[0]), abs(turn_deg[1])) > math.degrees(limits.turn_rate_max_rad_s):
            raise ValueError(f"mode {name} target_turn_rate_deg_s exceeds the supported range")
        if object_id == "vtol" and mode_class == "cruise":
            assert cruise_min_speed_mps is not None
            if speed[0] < cruise_min_speed_mps:
                raise ValueError("VTOL cruise mode must not command below cruise_min_speed_mps")
        modes[name] = MotionMode(
            name=name,
            mode_class=mode_class,
            duration_s=duration_s,
            target_speed_mps=speed,
            target_vertical_speed_mps=vertical,
            target_turn_rate_rad_s=(math.radians(turn_deg[0]), math.radians(turn_deg[1])),
        )
    return modes


def _parse_sequences(
    raw_sequences: object, modes: Mapping[str, MotionMode]
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw_sequences, Sequence) or isinstance(raw_sequences, (str, bytes)):
        raise ValueError("experiment.ordering_alternatives must be a sequence")
    sequences: list[tuple[str, ...]] = []
    for sequence in raw_sequences:
        if not isinstance(sequence, Sequence) or isinstance(sequence, (str, bytes)) or not sequence:
            raise ValueError("each ordering alternative must be a non-empty sequence")
        names = tuple(sequence)
        if any(not isinstance(name, str) or name not in modes for name in names):
            raise ValueError("ordering alternative references an unknown mode")
        sequences.append(names)
    if not sequences:
        raise ValueError("experiment requires at least one ordering alternative")
    return tuple(sequences)


def _parse_mode_index_map(
    value: object,
    modes: Mapping[str, MotionMode],
) -> dict[str, int]:
    labels = {mode.mode_class for mode in modes.values()} | {"transition", "decelerating"}
    if value is None:
        return {label: index for index, label in enumerate(sorted(labels))}
    if not isinstance(value, Mapping):
        raise ValueError("experiment.mode_index_map must be a mapping")
    if set(value) != labels:
        raise ValueError("experiment.mode_index_map must cover each effective mode class")
    result: dict[str, int] = {}
    for label, index in value.items():
        if isinstance(index, bool) or not isinstance(index, Integral) or int(index) < 0:
            raise ValueError("experiment.mode_index_map values must be non-negative integers")
        result[str(label)] = int(index)
    if len(set(result.values())) != len(result):
        raise ValueError("experiment.mode_index_map values must be unique")
    return result


def _sample(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    if bounds[0] == bounds[1]:
        return bounds[0]
    return float(rng.uniform(bounds[0], bounds[1]))


def _schedule_commands(
    *,
    rng: np.random.Generator,
    modes: Mapping[str, MotionMode],
    sequences: tuple[tuple[str, ...], ...],
) -> tuple[ScheduledMotionCommand, ...]:
    commands: list[ScheduledMotionCommand] = []
    start_s = 0.0
    index = 0
    while start_s < DURATION_S - 1e-12:
        sequence = sequences[int(rng.integers(len(sequences)))]
        for name in sequence:
            mode = modes[name]
            duration_s = _sample(rng, mode.duration_s)
            uncut_end_s = start_s + duration_s
            end_s = min(uncut_end_s, DURATION_S)
            commands.append(
                ScheduledMotionCommand(
                    index=index,
                    mode=mode,
                    start_s=start_s,
                    end_s=end_s,
                    target_speed_mps=_sample(rng, mode.target_speed_mps),
                    target_vertical_speed_mps=_sample(rng, mode.target_vertical_speed_mps),
                    target_turn_rate_rad_s=_sample(rng, mode.target_turn_rate_rad_s),
                    was_cut_off=uncut_end_s > DURATION_S,
                )
            )
            start_s = end_s
            index += 1
            if start_s >= DURATION_S - 1e-12:
                break
    assert commands and commands[0].start_s == 0.0
    assert math.isclose(commands[-1].end_s, DURATION_S, rel_tol=0.0, abs_tol=1e-12)
    return tuple(commands)


@dataclass(frozen=True, slots=True)
class _ParsedMotionScenarioInputs:
    object_id: str
    initial_position_enu_m: np.ndarray
    initial_speed_mps: float
    initial_heading_rad: float
    initial_vertical_speed_mps: float
    initial_turn_rate_rad_s: float
    hover_speed_tolerance_mps: float
    target_tolerance_speed_mps: float
    target_tolerance_vertical_speed_mps: float
    target_tolerance_turn_rate_rad_s: float
    cruise_min_speed_mps: float | None
    mode_index_map: Mapping[str, int]
    modes: Mapping[str, MotionMode]
    sequences: tuple[tuple[str, ...], ...]
    phase_policy: PhaseExecutionPolicy | None


def _parse_motion_scenario_inputs(
    profile: Mapping[str, object], *, limits: MotionLimits, rng: np.random.Generator
) -> _ParsedMotionScenarioInputs:
    """Parse common state and command data while retaining legacy RNG order."""
    object_id = profile.get("object_id")
    if object_id not in {"vtol", "helicopter"}:
        raise ValueError("bounded_motion supports only vtol or helicopter object_id values")
    experiment = profile.get("experiment")
    if not isinstance(experiment, Mapping):
        raise ValueError("bounded motion profile requires an experiment mapping")
    unknown = set(experiment) - _EXPERIMENT_FIELDS
    if unknown:
        raise ValueError(f"experiment has unsupported fields: {sorted(unknown)}")
    role = experiment.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError("experiment requires a readable simulation-design role")
    phase_policy = _parse_phase_execution_policy(
        experiment.get("execution_policy"), object_id=object_id
    )
    if "cruise_min_speed_mps" in experiment:
        if object_id != "vtol":
            raise ValueError("only VTOL bounded motion may define cruise_min_speed_mps")
        cruise_min_speed_mps = _finite_number(
            experiment["cruise_min_speed_mps"], "experiment.cruise_min_speed_mps", positive=True
        )
        if cruise_min_speed_mps > limits.horizontal_speed_max_mps:
            raise ValueError("experiment.cruise_min_speed_mps exceeds horizontal_speed_max_mps")
    elif object_id == "vtol":
        raise ValueError("VTOL bounded motion requires cruise_min_speed_mps")
    else:
        cruise_min_speed_mps = None
    hover_tolerance = _finite_number(
        experiment.get("hover_speed_tolerance_mps"),
        "experiment.hover_speed_tolerance_mps",
        positive=True,
    )
    if hover_tolerance > limits.horizontal_speed_max_mps:
        raise ValueError("experiment.hover_speed_tolerance_mps exceeds horizontal_speed_max_mps")
    target_speed_tolerance = _finite_number(
        experiment.get("target_tolerance_speed_mps"),
        "experiment.target_tolerance_speed_mps",
        positive=True,
    )
    target_vertical_tolerance = _finite_number(
        experiment.get("target_tolerance_vertical_speed_mps"),
        "experiment.target_tolerance_vertical_speed_mps",
        positive=True,
    )
    target_turn_tolerance_deg_s = _finite_number(
        experiment.get("target_tolerance_turn_rate_deg_s"),
        "experiment.target_tolerance_turn_rate_deg_s",
        positive=True,
    )
    if target_speed_tolerance > limits.horizontal_speed_max_mps:
        raise ValueError("experiment.target_tolerance_speed_mps exceeds horizontal_speed_max_mps")
    if target_vertical_tolerance > limits.vertical_speed_max_mps:
        raise ValueError(
            "experiment.target_tolerance_vertical_speed_mps exceeds vertical_speed_max_mps"
        )
    if target_turn_tolerance_deg_s > math.degrees(limits.turn_rate_max_rad_s):
        raise ValueError("experiment.target_tolerance_turn_rate_deg_s exceeds turn_rate_max_deg_s")
    initial_speed_mps = _finite_number(
        experiment.get("initial_speed_mps"), "experiment.initial_speed_mps"
    )
    initial_vertical_speed_mps = _finite_number(
        experiment.get("initial_vertical_speed_mps"), "experiment.initial_vertical_speed_mps"
    )
    initial_turn_rate_deg_s = _finite_number(
        experiment.get("initial_turn_rate_deg_s", 0.0), "experiment.initial_turn_rate_deg_s"
    )
    if not 0.0 <= initial_speed_mps <= limits.horizontal_speed_max_mps:
        raise ValueError("experiment.initial_speed_mps exceeds the supported range")
    if abs(initial_vertical_speed_mps) > limits.vertical_speed_max_mps:
        raise ValueError("experiment.initial_vertical_speed_mps exceeds the supported range")
    if abs(initial_turn_rate_deg_s) > math.degrees(limits.turn_rate_max_rad_s):
        raise ValueError("experiment.initial_turn_rate_deg_s exceeds the supported range")
    modes = _parse_modes(
        experiment.get("modes"),
        object_id=object_id,
        limits=limits,
        cruise_min_speed_mps=cruise_min_speed_mps,
    )
    if phase_policy is not None and any(
        phase_policy.reference_blend_s > mode.duration_s[0] + 1e-12
        for mode in modes.values()
    ):
        raise ValueError(
            "experiment.execution_policy.reference_blend_s must not exceed each "
            "phase minimum duration"
        )
    sequences = _parse_sequences(experiment.get("ordering_alternatives"), modes)
    mode_index_map = _parse_mode_index_map(experiment.get("mode_index_map"), modes)
    heading_range = _initial_heading_range(experiment)
    return _ParsedMotionScenarioInputs(
        object_id=object_id,
        initial_position_enu_m=_initial_position(experiment),
        initial_speed_mps=initial_speed_mps,
        initial_heading_rad=math.radians(_sample(rng, heading_range)),
        initial_vertical_speed_mps=initial_vertical_speed_mps,
        initial_turn_rate_rad_s=math.radians(initial_turn_rate_deg_s),
        hover_speed_tolerance_mps=hover_tolerance,
        target_tolerance_speed_mps=target_speed_tolerance,
        target_tolerance_vertical_speed_mps=target_vertical_tolerance,
        target_tolerance_turn_rate_rad_s=math.radians(target_turn_tolerance_deg_s),
        cruise_min_speed_mps=cruise_min_speed_mps,
        mode_index_map=mode_index_map,
        modes=modes,
        sequences=sequences,
        phase_policy=phase_policy,
    )


def build_motion_scenario(
    profile: Mapping[str, object], *, limits: MotionLimits, rng: np.random.Generator
) -> MotionScenario:
    """Validate and sample the unchanged legacy time-window command schedule."""
    parsed = _parse_motion_scenario_inputs(profile, limits=limits, rng=rng)
    if parsed.phase_policy is not None:
        raise ValueError("phase-aware VTOL policy requires build_phase_motion_scenario")
    return MotionScenario(
        object_id=parsed.object_id,
        initial_position_enu_m=parsed.initial_position_enu_m,
        initial_speed_mps=parsed.initial_speed_mps,
        initial_heading_rad=parsed.initial_heading_rad,
        initial_vertical_speed_mps=parsed.initial_vertical_speed_mps,
        initial_turn_rate_rad_s=parsed.initial_turn_rate_rad_s,
        hover_speed_tolerance_mps=parsed.hover_speed_tolerance_mps,
        target_tolerance_speed_mps=parsed.target_tolerance_speed_mps,
        target_tolerance_vertical_speed_mps=parsed.target_tolerance_vertical_speed_mps,
        target_tolerance_turn_rate_rad_s=parsed.target_tolerance_turn_rate_rad_s,
        cruise_min_speed_mps=parsed.cruise_min_speed_mps,
        mode_index_map=parsed.mode_index_map,
        commands=_schedule_commands(rng=rng, modes=parsed.modes, sequences=parsed.sequences),
    )


def _sample_feasible_turn_rate(
    *,
    rng: np.random.Generator,
    mode: MotionMode,
    speed_mps: float,
    limits: MotionLimits,
) -> float:
    """Sample a track turn only from the speed-coupled normal-acceleration range."""
    if speed_mps <= 1e-12:
        feasible_turn_limit = limits.turn_rate_max_rad_s
    else:
        feasible_turn_limit = min(
            limits.turn_rate_max_rad_s,
            limits.horizontal_accel_max_mps2 / speed_mps,
        )
    lower = max(mode.target_turn_rate_rad_s[0], -feasible_turn_limit)
    upper = min(mode.target_turn_rate_rad_s[1], feasible_turn_limit)
    if lower > upper + 1e-12:
        raise ValueError(
            f"mode {mode.name} has no feasible turn-rate intersection for sampled "
            f"speed {speed_mps:g} m/s under the horizontal acceleration budget"
        )
    return _sample(rng, (lower, upper))


def build_phase_motion_scenario(
    profile: Mapping[str, object], *, limits: MotionLimits, rng: np.random.Generator
) -> PhaseMotionScenario:
    """Sample an explicit VTOL phase plan and all coupled targets before simulation."""
    parsed = _parse_motion_scenario_inputs(profile, limits=limits, rng=rng)
    policy = parsed.phase_policy
    if policy is None:
        raise ValueError("phase-aware VTOL execution requires experiment.execution_policy")
    sequence_names = parsed.sequences[int(rng.integers(len(parsed.sequences)))]
    targets: list[PhaseMotionTarget] = []
    for name in sequence_names:
        mode = parsed.modes[name]
        reference_duration_s = _sample(rng, mode.duration_s)
        target_speed_mps = _sample(rng, mode.target_speed_mps)
        targets.append(
            PhaseMotionTarget(
                mode=mode,
                reference_duration_s=reference_duration_s,
                target_speed_mps=target_speed_mps,
                target_vertical_speed_mps=_sample(rng, mode.target_vertical_speed_mps),
                target_turn_rate_rad_s=_sample_feasible_turn_rate(
                    rng=rng,
                    mode=mode,
                    speed_mps=target_speed_mps,
                    limits=limits,
                ),
            )
        )
    if not targets:
        raise ValueError("phase-aware VTOL execution requires at least one phase target")
    return PhaseMotionScenario(
        object_id=parsed.object_id,
        initial_position_enu_m=parsed.initial_position_enu_m,
        initial_speed_mps=parsed.initial_speed_mps,
        initial_heading_rad=parsed.initial_heading_rad,
        initial_vertical_speed_mps=parsed.initial_vertical_speed_mps,
        initial_turn_rate_rad_s=parsed.initial_turn_rate_rad_s,
        hover_speed_tolerance_mps=parsed.hover_speed_tolerance_mps,
        target_tolerance_speed_mps=parsed.target_tolerance_speed_mps,
        target_tolerance_vertical_speed_mps=parsed.target_tolerance_vertical_speed_mps,
        target_tolerance_turn_rate_rad_s=parsed.target_tolerance_turn_rate_rad_s,
        hover_turn_rate_tolerance_rad_s=parsed.target_tolerance_turn_rate_rad_s,
        cruise_min_speed_mps=parsed.cruise_min_speed_mps,
        mode_index_map=parsed.mode_index_map,
        policy=policy,
        targets=tuple(targets),
    )
