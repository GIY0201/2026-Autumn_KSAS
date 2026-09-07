"""Target-driven Crazyflie diagnostic episode generation without kinematic teleportation."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from contracts.v1.validation import DURATION_S, OUTPUT_DT_S, SAMPLE_COUNT

from .motion_reference_analysis import (
    LoadedMotionReferenceConfig,
    MotionReferenceError,
    load_motion_reference_config,
)
from .motion_reference_metadata import validate_source_grounded_metadata
from .quadrotor import (
    QuadrotorDynamics,
    _NonfiniteDynamicsError,
    quaternion_to_euler,
    quaternion_to_rotation,
)
from .quadrotor_reference import (
    ReferencePlanningError,
    ReferenceState,
    SourceGroundedReferencePlanner,
    SourceGroundedReferenceSettings,
)
from .records import MotionEpisode, MotionEvent

_DIAGNOSTIC_NAMES = (
    "q_w",
    "q_x",
    "q_y",
    "q_z",
    "body_rate_x_radps",
    "body_rate_y_radps",
    "body_rate_z_radps",
    "motor_1_normalized",
    "motor_2_normalized",
    "motor_3_normalized",
    "motor_4_normalized",
    "command_1_normalized",
    "command_2_normalized",
    "command_3_normalized",
    "command_4_normalized",
    "roll_rad",
    "pitch_rad",
    "yaw_rad",
    "target_x_m",
    "target_y_m",
    "target_z_m",
    "waypoint_index",
)

_SOURCE_REFERENCE_DIAGNOSTIC_NAMES = (
    "reference_position_x_m",
    "reference_position_y_m",
    "reference_position_z_m",
    "reference_velocity_x_mps",
    "reference_velocity_y_mps",
    "reference_velocity_z_mps",
    "reference_acceleration_x_mps2",
    "reference_acceleration_y_mps2",
    "reference_acceleration_z_mps2",
    "reference_jerk_x_mps3",
    "reference_jerk_y_mps3",
    "reference_jerk_z_mps3",
    "reference_tracking_error_m",
    "reference_leg_index",
    "reference_nominal_mean_speed_mps",
)


def _finite_scalar(config: Mapping[str, object], name: str, *, positive: bool = False) -> float:
    """Read one named numeric experiment value without a silent fallback."""
    try:
        value = float(config[name])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"experiment requires numeric {name}") from error
    if not math.isfinite(value) or (positive and value <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"experiment {name} must be {qualifier}")
    return value


def _integer_ratio(numerator: float, denominator: float, *, name: str) -> int:
    """Require an integral timing grid instead of rounding a simulation schedule."""
    ratio = numerator / denominator
    rounded = round(ratio)
    if rounded <= 0 or not math.isclose(ratio, rounded, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{name} must divide the declared timing grid exactly")
    return int(rounded)


def _bounded_norm(vector: np.ndarray, maximum: float) -> np.ndarray:
    """Limit vector magnitude while preserving direction."""
    norm = float(np.linalg.norm(vector))
    if norm <= maximum:
        return vector
    return vector * (maximum / norm)


@dataclass(frozen=True, slots=True)
class _Experiment:
    """Resolved project diagnostic/controller settings, distinct from physics coefficients."""

    physics_dt_s: float
    control_dt_s: float
    command_min: float
    command_max: float
    max_tilt_rad: float
    position_gain_per_s: float
    velocity_gain_per_s: float
    attitude_gain_per_s2: float
    rate_gain_per_s: float
    speed_command_mps: float
    position_tolerance_m: float
    velocity_tolerance_mps: float
    settling_time_s: float
    failure_speed_mps: float
    failure_tilt_rad: float
    waypoint_scale_min: float
    waypoint_scale_max: float
    waypoint_offsets_m: np.ndarray

    @classmethod
    def from_mapping(cls, experiment: Mapping[str, object]) -> _Experiment:
        """Validate all explicit experiment settings used by the simulation."""
        if not isinstance(experiment, Mapping):
            raise ValueError("profile requires an experiment mapping")
        command_min = _finite_scalar(experiment, "motor_command_min")
        command_max = _finite_scalar(experiment, "motor_command_max")
        if command_min < 0.0 or command_max > 1.0 or command_min > command_max:
            raise ValueError("experiment motor command bounds must lie in [0, 1]")
        scale_min = _finite_scalar(experiment, "waypoint_scale_min", positive=True)
        scale_max = _finite_scalar(experiment, "waypoint_scale_max", positive=True)
        if scale_min > scale_max:
            raise ValueError("experiment waypoint scale bounds are invalid")
        try:
            offsets = np.asarray(experiment["waypoint_offsets_m"], dtype=float)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("experiment requires numeric waypoint_offsets_m") from error
        if offsets.ndim != 2 or offsets.shape[0] == 0 or offsets.shape[1] != 3:
            raise ValueError("experiment waypoint_offsets_m must have shape N×3")
        if not np.all(np.isfinite(offsets)):
            raise ValueError("experiment waypoint_offsets_m must be finite")
        max_tilt_deg = _finite_scalar(experiment, "max_tilt_deg", positive=True)
        failure_tilt_deg = _finite_scalar(experiment, "failure_tilt_deg", positive=True)
        return cls(
            physics_dt_s=_finite_scalar(experiment, "physics_dt_s", positive=True),
            control_dt_s=_finite_scalar(experiment, "control_dt_s", positive=True),
            command_min=command_min,
            command_max=command_max,
            max_tilt_rad=math.radians(max_tilt_deg),
            position_gain_per_s=_finite_scalar(experiment, "position_gain_per_s", positive=True),
            velocity_gain_per_s=_finite_scalar(experiment, "velocity_gain_per_s", positive=True),
            attitude_gain_per_s2=_finite_scalar(
                experiment, "attitude_gain_per_s2", positive=True
            ),
            rate_gain_per_s=_finite_scalar(experiment, "rate_gain_per_s", positive=True),
            speed_command_mps=_finite_scalar(experiment, "speed_command_mps", positive=True),
            position_tolerance_m=_finite_scalar(experiment, "position_tolerance_m", positive=True),
            velocity_tolerance_mps=_finite_scalar(
                experiment, "velocity_tolerance_mps", positive=True
            ),
            settling_time_s=_finite_scalar(experiment, "settling_time_s", positive=True),
            failure_speed_mps=_finite_scalar(experiment, "failure_speed_mps", positive=True),
            failure_tilt_rad=math.radians(failure_tilt_deg),
            waypoint_scale_min=scale_min,
            waypoint_scale_max=scale_max,
            waypoint_offsets_m=offsets.copy(),
        )


def _yaw_rotated_offsets(offsets_m: np.ndarray, *, yaw_rad: float, scale: float) -> np.ndarray:
    """Rotate XY command changes by the seeded yaw and apply the seeded scale."""
    rotation = np.array(
        [
            [math.cos(yaw_rad), -math.sin(yaw_rad)],
            [math.sin(yaw_rad), math.cos(yaw_rad)],
        ]
    )
    transformed = offsets_m.copy() * scale
    transformed[:, :2] = offsets_m[:, :2] @ rotation.T * scale
    return transformed


def _desired_rotation(thrust_direction_world: np.ndarray, *, yaw_rad: float) -> np.ndarray:
    """Build a body-to-world target attitude with desired thrust direction and yaw."""
    body_z_world = thrust_direction_world / np.linalg.norm(thrust_direction_world)
    yaw_heading_world = np.array([math.cos(yaw_rad), math.sin(yaw_rad), 0.0])
    body_y_world = np.cross(body_z_world, yaw_heading_world)
    body_y_world = body_y_world / np.linalg.norm(body_y_world)
    body_x_world = np.cross(body_y_world, body_z_world)
    return np.column_stack((body_x_world, body_y_world, body_z_world))


def _attitude_error(rotation: np.ndarray, desired_rotation: np.ndarray) -> np.ndarray:
    """Return the geometric SO(3) attitude error vector in FLU body coordinates."""
    skew = 0.5 * (desired_rotation.T @ rotation - rotation.T @ desired_rotation)
    return np.array([skew[2, 1], skew[0, 2], skew[1, 0]])


def _controller_command(
    dynamics: QuadrotorDynamics,
    settings: _Experiment,
    state: np.ndarray,
    target_position_enu_m: np.ndarray,
    *,
    yaw_rad: float,
    reference_velocity_enu_mps: np.ndarray | None = None,
    reference_acceleration_enu_mps2: np.ndarray | None = None,
) -> np.ndarray:
    """Track a target through legacy PD or source-grounded P/V/A feedforward control."""
    position_error = target_position_enu_m - state[0:3]
    if reference_velocity_enu_mps is None and reference_acceleration_enu_mps2 is None:
        desired_velocity = _bounded_norm(
            settings.position_gain_per_s * position_error,
            settings.speed_command_mps,
        )
        acceleration_feedback = settings.velocity_gain_per_s * (desired_velocity - state[3:6])
        desired_specific_force = acceleration_feedback + np.array(
            [0.0, 0.0, dynamics.gravity_mps2]
        )
    else:
        if reference_velocity_enu_mps is None:
            reference_velocity_enu_mps = np.zeros(3)
        if reference_acceleration_enu_mps2 is None:
            reference_acceleration_enu_mps2 = np.zeros(3)
        reference_velocity = np.asarray(reference_velocity_enu_mps, dtype=float)
        reference_acceleration = np.asarray(reference_acceleration_enu_mps2, dtype=float)
        if (
            reference_velocity.shape != (3,)
            or reference_acceleration.shape != (3,)
            or not np.all(np.isfinite(reference_velocity))
            or not np.all(np.isfinite(reference_acceleration))
        ):
            raise ValueError("source-grounded reference velocity and acceleration must be finite")
        position_correction = _bounded_norm(
            settings.position_gain_per_s * position_error,
            settings.speed_command_mps,
        )
        desired_velocity = _bounded_norm(
            reference_velocity + position_correction,
            settings.speed_command_mps,
        )
        acceleration_feedback = settings.velocity_gain_per_s * (desired_velocity - state[3:6])
        desired_specific_force = (
            reference_acceleration
            + acceleration_feedback
            + np.array([0.0, 0.0, dynamics.gravity_mps2])
        )
    vertical = max(float(desired_specific_force[2]), np.finfo(float).eps)
    horizontal = desired_specific_force[:2]
    horizontal_limit = vertical * math.tan(settings.max_tilt_rad)
    horizontal = _bounded_norm(horizontal, horizontal_limit)
    desired_specific_force = np.array([horizontal[0], horizontal[1], vertical])
    desired_rotation = _desired_rotation(desired_specific_force, yaw_rad=yaw_rad)
    rotation = quaternion_to_rotation(state[6:10])
    angular_velocity = state[10:13]
    attitude_error = _attitude_error(rotation, desired_rotation)
    angular_momentum = dynamics.inertia_kg_m2 * angular_velocity
    desired_moment = dynamics.inertia_kg_m2 * (
        -settings.attitude_gain_per_s2 * attitude_error
        - settings.rate_gain_per_s * angular_velocity
    ) + np.cross(angular_velocity, angular_momentum)
    total_thrust = dynamics.mass_kg * float(np.linalg.norm(desired_specific_force))
    return dynamics.allocate_wrench(
        total_thrust_n=total_thrust,
        moment_body_nm=desired_moment,
        command_min=settings.command_min,
        command_max=settings.command_max,
    )


def _tilt_rad(state: np.ndarray) -> float:
    """Measure body-z tilt from Local ENU up."""
    rotation = quaternion_to_rotation(state[6:10])
    return math.acos(float(np.clip(rotation[2, 2], -1.0, 1.0)))


def _empty_diagnostics(names: tuple[str, ...] = _DIAGNOSTIC_NAMES) -> dict[str, list[float]]:
    return {name: [] for name in names}


def _append_sample(
    *,
    positions: list[np.ndarray],
    velocities: list[np.ndarray],
    diagnostics: dict[str, list[float]],
    state: np.ndarray,
    command: np.ndarray,
    target: np.ndarray,
    waypoint_index: int,
    reference_state: ReferenceState | None = None,
    reference_nominal_mean_speed_mps: float | None = None,
) -> None:
    """Store one actual state/controller sample without modifying simulation state."""
    positions.append(state[0:3].copy())
    velocities.append(state[3:6].copy())
    quaternion = state[6:10]
    rates = state[10:13]
    motors = state[13:17]
    euler = quaternion_to_euler(quaternion)
    for index, value in enumerate(quaternion):
        diagnostics[f"q_{'wxyz'[index]}"].append(float(value))
    for index, value in enumerate(rates):
        diagnostics[f"body_rate_{'xyz'[index]}_radps"].append(float(value))
    for index, value in enumerate(motors, start=1):
        diagnostics[f"motor_{index}_normalized"].append(float(value))
    for index, value in enumerate(command, start=1):
        diagnostics[f"command_{index}_normalized"].append(float(value))
    for index, value in enumerate(euler):
        diagnostics[f"{'roll pitch yaw'.split()[index]}_rad"].append(float(value))
    for index, axis in enumerate("xyz"):
        diagnostics[f"target_{axis}_m"].append(float(target[index]))
    diagnostics["waypoint_index"].append(float(waypoint_index))
    if reference_state is not None:
        if reference_nominal_mean_speed_mps is None:
            raise ValueError("source-grounded diagnostics require a nominal mean speed")
        reference_arrays = (
            ("position", "m", reference_state.position_enu_m),
            ("velocity", "mps", reference_state.velocity_enu_mps),
            ("acceleration", "mps2", reference_state.acceleration_enu_mps2),
            ("jerk", "mps3", reference_state.jerk_enu_mps3),
        )
        for quantity, unit, values in reference_arrays:
            for index, axis in enumerate("xyz"):
                diagnostics[f"reference_{quantity}_{axis}_{unit}"].append(float(values[index]))
        diagnostics["reference_tracking_error_m"].append(
            float(np.linalg.norm(reference_state.position_enu_m - state[0:3]))
        )
        diagnostics["reference_leg_index"].append(float(waypoint_index))
        diagnostics["reference_nominal_mean_speed_mps"].append(
            float(reference_nominal_mean_speed_mps)
        )


def _event_record(
    *,
    waypoint_index: int,
    start_s: float,
    end_s: float,
    completed: bool,
    completion_reason: str,
) -> MotionEvent:
    return MotionEvent(
        event_id=f"waypoint_{waypoint_index}",
        start_s=start_s,
        end_s=end_s,
        trigger_kind="position_velocity_settled",
        completed=completed,
        completion_reason=completion_reason,
    )


def _reference_event_record(
    *,
    leg_index: int,
    start_s: float,
    end_s: float,
    completed: bool,
    completion_reason: str,
) -> MotionEvent:
    """Record actual source-grounded endpoint completion separately from reference end."""
    return MotionEvent(
        event_id=f"reference_leg_{leg_index}",
        start_s=start_s,
        end_s=end_s,
        trigger_kind="reference_endpoint_position_velocity_settled",
        completed=completed,
        completion_reason=completion_reason,
    )


def _source_grounded_config(
    profile: Mapping[str, object],
) -> LoadedMotionReferenceConfig | None:
    """Select legacy compatibility or validate one explicit nested source-reference record."""
    motion_reference = profile.get("motion_reference")
    if motion_reference is None:
        return None
    if not isinstance(motion_reference, Mapping):
        raise ValueError("quadrotor motion_reference must be a mapping")
    mode = motion_reference.get("mode")
    if mode == "legacy_waypoints":
        return None
    if mode != "source_grounded":
        raise ValueError("quadrotor motion_reference mode is unsupported")
    config_path = motion_reference.get("config_path")
    if not isinstance(config_path, str) or not config_path:
        raise ValueError("source-grounded motion_reference requires config_path")

    resolved_config = motion_reference.get("resolved_config")
    if resolved_config is None:
        try:
            config = load_motion_reference_config(Path(config_path))
        except MotionReferenceError as error:
            raise ValueError("source-grounded motion reference config is invalid") from error
    else:
        config = _loaded_config_from_snapshot(config_path, resolved_config)
    validate_source_grounded_metadata(motion_reference, config.values)
    return config


def _loaded_config_from_snapshot(
    config_path: str, resolved_config: object
) -> LoadedMotionReferenceConfig:
    """Rehydrate the generation-start config snapshot without reading the mutable YAML."""
    if not isinstance(resolved_config, Mapping):
        raise ValueError("source-grounded resolved_config must be a mapping")
    snapshot_path = resolved_config.get("config_path")
    config_hash = resolved_config.get("config_sha256")
    values = resolved_config.get("values")
    if not isinstance(snapshot_path, str) or not snapshot_path:
        raise ValueError("source-grounded resolved_config requires config_path")
    if Path(snapshot_path).as_posix() != Path(config_path).as_posix():
        raise ValueError("source-grounded resolved_config path does not match profile")
    if not isinstance(config_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", config_hash):
        raise ValueError("source-grounded resolved_config requires a SHA-256 digest")
    if not isinstance(values, dict):
        raise ValueError("source-grounded resolved_config requires resolved values")
    return LoadedMotionReferenceConfig(
        path=Path(snapshot_path),
        sha256=config_hash,
        values=values,
    )


def _run_legacy_quadrotor_episode(seed: int, profile: Mapping[str, object]) -> MotionEpisode:
    """Generate one continuous 60-second target-tracking Crazyflie MotionEpisode.

    ``profile`` must already be the fully resolved, source-verified profile.
    The function uses its physics and experiment blocks directly and returns
    partial finite evidence rather than a falsely completed record on a
    configured dynamic-envelope failure.
    """
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise ValueError("seed must be an integer")
    if not isinstance(profile, Mapping) or profile.get("object_id") != "quadrotor":
        raise ValueError("run_quadrotor_episode requires a resolved quadrotor profile")
    physics = profile.get("physics")
    experiment = profile.get("experiment")
    if not isinstance(physics, Mapping) or not isinstance(experiment, Mapping):
        raise ValueError("quadrotor profile requires physics and experiment mappings")
    dynamics = QuadrotorDynamics(physics)
    settings = _Experiment.from_mapping(experiment)
    physics_per_control = _integer_ratio(
        settings.control_dt_s, settings.physics_dt_s, name="control_dt_s"
    )
    controls_per_output = _integer_ratio(
        OUTPUT_DT_S, settings.control_dt_s, name="control_dt_s"
    )
    control_steps = _integer_ratio(DURATION_S, settings.control_dt_s, name="control_dt_s")
    assert control_steps // controls_per_output == SAMPLE_COUNT - 1

    generator = np.random.default_rng(np.random.SeedSequence([int(seed), 0]))
    yaw_rad = float(generator.uniform(-math.pi, math.pi))
    waypoint_scale = float(
        generator.uniform(settings.waypoint_scale_min, settings.waypoint_scale_max)
    )
    offsets_world = _yaw_rotated_offsets(
        settings.waypoint_offsets_m,
        yaw_rad=yaw_rad,
        scale=waypoint_scale,
    )
    state = dynamics.hover_state(yaw_rad=yaw_rad)
    waypoint_index = 0
    target = offsets_world[waypoint_index].copy()
    active_start_s = 0.0
    settled_duration_s = 0.0
    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    diagnostics = _empty_diagnostics()
    completed_events: list[MotionEvent] = []
    command = _controller_command(dynamics, settings, state, target, yaw_rad=yaw_rad)
    _append_sample(
        positions=positions,
        velocities=velocities,
        diagnostics=diagnostics,
        state=state,
        command=command,
        target=target,
        waypoint_index=waypoint_index,
    )
    failure_reason: str | None = None

    for control_step in range(control_steps):
        command = _controller_command(dynamics, settings, state, target, yaw_rad=yaw_rad)
        for _ in range(physics_per_control):
            try:
                state = dynamics.step(state, command, settings.physics_dt_s)
            except _NonfiniteDynamicsError:
                failure_reason = "nonfinite_dynamics"
                break
            if not np.all(np.isfinite(state)):
                failure_reason = "nonfinite_dynamics"
                break
            if float(np.linalg.norm(state[3:6])) > settings.failure_speed_mps:
                failure_reason = "failure_speed_mps"
                break
            if _tilt_rad(state) > settings.failure_tilt_rad:
                failure_reason = "failure_tilt_deg"
                break
        if failure_reason is not None:
            break

        now_s = (control_step + 1) * settings.control_dt_s
        if (
            float(np.linalg.norm(target - state[0:3])) <= settings.position_tolerance_m
            and float(np.linalg.norm(state[3:6])) <= settings.velocity_tolerance_mps
        ):
            settled_duration_s += settings.control_dt_s
        else:
            settled_duration_s = 0.0
        if settled_duration_s >= settings.settling_time_s:
            completed_events.append(
                _event_record(
                    waypoint_index=waypoint_index,
                    start_s=active_start_s,
                    end_s=now_s,
                    completed=True,
                    completion_reason="settled",
                )
            )
            waypoint_index = (waypoint_index + 1) % len(offsets_world)
            target = target + offsets_world[waypoint_index]
            active_start_s = now_s
            settled_duration_s = 0.0
        if (control_step + 1) % controls_per_output == 0:
            _append_sample(
                positions=positions,
                velocities=velocities,
                diagnostics=diagnostics,
                state=state,
                command=command,
                target=target,
                waypoint_index=waypoint_index,
            )

    complete = failure_reason is None and len(positions) == SAMPLE_COUNT
    if not complete and failure_reason is None:
        failure_reason = "output_sample_count"
    steps = np.arange(len(positions), dtype=int)
    time_s = steps.astype(float) * OUTPUT_DT_S
    final_time_s = float(time_s[-1]) if len(time_s) else 0.0
    event_records = [event for event in completed_events if event.end_s <= final_time_s]
    if active_start_s <= final_time_s:
        event_records.append(
            _event_record(
                waypoint_index=waypoint_index,
                start_s=active_start_s,
                end_s=final_time_s,
                completed=False,
                completion_reason="episode_end" if complete else "simulation_failed",
            )
        )
    return MotionEpisode(
        episode_id=f"quadrotor-{int(seed):08d}",
        status="complete" if complete else "failed",
        failure_reason=None if complete else failure_reason,
        seed=int(seed),
        steps=steps,
        t_s=time_s,
        position_enu_m=np.asarray(positions, dtype=float),
        velocity_enu_mps=np.asarray(velocities, dtype=float),
        diagnostic_arrays={
            name: np.asarray(values, dtype=float) for name, values in diagnostics.items()
        },
        event_records=tuple(event_records),
    )


def _run_source_grounded_quadrotor_episode(
    seed: int,
    profile: Mapping[str, object],
    source_config: LoadedMotionReferenceConfig,
) -> MotionEpisode:
    """Track fresh smooth source-conditioned references through physical Crazyflie dynamics."""
    physics = profile.get("physics")
    experiment = profile.get("experiment")
    if not isinstance(physics, Mapping) or not isinstance(experiment, Mapping):
        raise ValueError("quadrotor profile requires physics and experiment mappings")
    dynamics = QuadrotorDynamics(physics)
    settings = _Experiment.from_mapping(experiment)
    reference_settings = SourceGroundedReferenceSettings.from_mapping(source_config.values)
    physics_per_control = _integer_ratio(
        settings.control_dt_s, settings.physics_dt_s, name="control_dt_s"
    )
    controls_per_output = _integer_ratio(
        OUTPUT_DT_S, settings.control_dt_s, name="control_dt_s"
    )
    control_steps = _integer_ratio(DURATION_S, settings.control_dt_s, name="control_dt_s")
    assert control_steps // controls_per_output == SAMPLE_COUNT - 1

    generator = np.random.default_rng(np.random.SeedSequence([int(seed), 0]))
    yaw_rad = float(generator.uniform(-math.pi, math.pi))
    state = dynamics.hover_state(yaw_rad=yaw_rad)
    planner = SourceGroundedReferencePlanner(reference_settings, seed=int(seed))
    try:
        active_leg = planner.next_leg(state[0:3])
    except ReferencePlanningError as error:
        raise ValueError("could not initialize source-grounded reference") from error
    active_start_s = 0.0
    settled_duration_s = 0.0
    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    diagnostics = _empty_diagnostics(_DIAGNOSTIC_NAMES + _SOURCE_REFERENCE_DIAGNOSTIC_NAMES)
    completed_events: list[MotionEvent] = []
    reference_state = active_leg.segment.evaluate(0.0)
    command = _controller_command(
        dynamics,
        settings,
        state,
        reference_state.position_enu_m,
        yaw_rad=yaw_rad,
        reference_velocity_enu_mps=reference_state.velocity_enu_mps,
        reference_acceleration_enu_mps2=reference_state.acceleration_enu_mps2,
    )
    _append_sample(
        positions=positions,
        velocities=velocities,
        diagnostics=diagnostics,
        state=state,
        command=command,
        target=active_leg.target_enu_m,
        waypoint_index=active_leg.index,
        reference_state=reference_state,
        reference_nominal_mean_speed_mps=active_leg.nominal_mean_speed_mps,
    )
    failure_reason: str | None = None

    for control_step in range(control_steps):
        control_start_s = control_step * settings.control_dt_s
        reference_state = active_leg.segment.evaluate(control_start_s - active_start_s)
        command = _controller_command(
            dynamics,
            settings,
            state,
            reference_state.position_enu_m,
            yaw_rad=yaw_rad,
            reference_velocity_enu_mps=reference_state.velocity_enu_mps,
            reference_acceleration_enu_mps2=reference_state.acceleration_enu_mps2,
        )
        for _ in range(physics_per_control):
            try:
                state = dynamics.step(state, command, settings.physics_dt_s)
            except (_NonfiniteDynamicsError, ValueError):
                failure_reason = "nonfinite_dynamics"
                break
            if not np.all(np.isfinite(state)):
                failure_reason = "nonfinite_dynamics"
                break
            if float(np.linalg.norm(state[3:6])) > settings.failure_speed_mps:
                failure_reason = "failure_speed_mps"
                break
            if _tilt_rad(state) > settings.failure_tilt_rad:
                failure_reason = "failure_tilt_deg"
                break
        if failure_reason is not None:
            break

        now_s = (control_step + 1) * settings.control_dt_s
        reference_state = active_leg.segment.evaluate(now_s - active_start_s)
        advance_after_sample = False
        if now_s >= active_start_s + active_leg.duration_s:
            endpoint_error_m = float(
                np.linalg.norm(active_leg.target_enu_m - state[0:3])
            )
            current_speed_mps = float(np.linalg.norm(state[3:6]))
            if (
                endpoint_error_m <= settings.position_tolerance_m
                and current_speed_mps <= settings.velocity_tolerance_mps
            ):
                settled_duration_s += settings.control_dt_s
            else:
                settled_duration_s = 0.0
            if settled_duration_s >= reference_settings.settling_duration_s:
                completed_events.append(
                    _reference_event_record(
                        leg_index=active_leg.index,
                        start_s=active_start_s,
                        end_s=now_s,
                        completed=True,
                        completion_reason="reference_endpoint_settled",
                    )
                )
                advance_after_sample = True
            elif now_s - (active_start_s + active_leg.duration_s) >= (
                reference_settings.maximum_hold_duration_s
            ):
                failure_reason = "reference_tracking_timeout"

        if (control_step + 1) % controls_per_output == 0:
            _append_sample(
                positions=positions,
                velocities=velocities,
                diagnostics=diagnostics,
                state=state,
                command=command,
                target=active_leg.target_enu_m,
                waypoint_index=active_leg.index,
                reference_state=reference_state,
                reference_nominal_mean_speed_mps=active_leg.nominal_mean_speed_mps,
            )
        if failure_reason is not None:
            break
        if advance_after_sample:
            try:
                active_leg = planner.next_leg(active_leg.target_enu_m)
            except ReferencePlanningError:
                failure_reason = "reference_planning_failed"
                break
            active_start_s = now_s
            settled_duration_s = 0.0

    complete = failure_reason is None and len(positions) == SAMPLE_COUNT
    if not complete and failure_reason is None:
        failure_reason = "output_sample_count"
    steps = np.arange(len(positions), dtype=int)
    time_s = steps.astype(float) * OUTPUT_DT_S
    final_time_s = float(time_s[-1]) if len(time_s) else 0.0
    event_records = [event for event in completed_events if event.end_s <= final_time_s]
    if active_start_s <= final_time_s:
        event_records.append(
            _reference_event_record(
                leg_index=active_leg.index,
                start_s=active_start_s,
                end_s=final_time_s,
                completed=False,
                completion_reason="episode_end" if complete else str(failure_reason),
            )
        )
    return MotionEpisode(
        episode_id=f"quadrotor-{int(seed):08d}",
        status="complete" if complete else "failed",
        failure_reason=None if complete else failure_reason,
        seed=int(seed),
        steps=steps,
        t_s=time_s,
        position_enu_m=np.asarray(positions, dtype=float),
        velocity_enu_mps=np.asarray(velocities, dtype=float),
        diagnostic_arrays={
            name: np.asarray(values, dtype=float) for name, values in diagnostics.items()
        },
        event_records=tuple(event_records),
    )


def run_quadrotor_episode(seed: int, profile: Mapping[str, object]) -> MotionEpisode:
    """Generate a legacy-compatible or explicit source-grounded Crazyflie MotionEpisode."""
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise ValueError("seed must be an integer")
    if not isinstance(profile, Mapping) or profile.get("object_id") != "quadrotor":
        raise ValueError("run_quadrotor_episode requires a resolved quadrotor profile")
    source_config = _source_grounded_config(profile)
    if source_config is None:
        return _run_legacy_quadrotor_episode(seed, profile)
    return _run_source_grounded_quadrotor_episode(seed, profile, source_config)


__all__ = ["run_quadrotor_episode"]
