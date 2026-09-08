"""Offline Gazebo force primitives and explicitly reduced translational models.

LiftDrag formulas follow the pinned Apache-2.0 Gazebo implementation recorded in
the source catalog. Rotational rigid-body dynamics and joint mechanisms are not
ported. No legacy kinematic profile participates in these calculations.
"""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf


def motor_thrust(omega: float, coefficient: float, maximum: float) -> float:
    """Positive normal rotation; omega is physical rad/s, not slowed joint rate."""
    if not all(math.isfinite(x) for x in (omega, coefficient, maximum)):
        raise ValueError("nonfinite motor input")
    if coefficient <= 0 or maximum <= 0:
        raise ValueError("positive motor parameters required")
    return coefficient * min(max(omega, 0.0), maximum) ** 2


def unit(vector: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    size = float(np.linalg.norm(vector))
    if size < 1e-12:
        if fallback is None:
            raise ValueError("undefined direction")
        return np.array(fallback, dtype=float)
    return vector / size


def lift_drag(
    velocity: np.ndarray, forward: np.ndarray, upward: np.ndarray, surface: dict
) -> np.ndarray:
    """Pinned LiftDrag zero-control branch, including sweep and stall, in ENU.

    Velocity is local air-relative velocity at the force point. Control deflection
    must be represented explicitly in the supplied axes, not silently added here.
    """
    speed = float(np.linalg.norm(velocity))
    if speed <= 0.01 or float(np.dot(forward, velocity)) <= 0:
        return np.zeros(3)
    span = unit(np.cross(forward, upward))
    sweep_sine = float(np.clip(np.dot(span, velocity) / speed, -1, 1))
    sweep_factor = 1 - sweep_sine * sweep_sine
    plane_velocity = velocity - np.dot(velocity, span) * span
    plane_speed = float(np.linalg.norm(plane_velocity))
    if plane_speed < 1e-12:
        return np.zeros(3)
    lift_direction = unit(np.cross(span, plane_velocity))
    angle = math.acos(float(np.clip(np.dot(lift_direction, upward), -1, 1)))
    alpha = surface["a0"] + (angle if np.dot(lift_direction, forward) >= 0 else -angle)
    alpha = (alpha + math.pi / 2) % math.pi - math.pi / 2
    stall = surface["alpha_stall"]
    if alpha > stall:
        cl = max(0.0, surface["cla"] * stall + surface["cla_stall"] * (alpha - stall))
        cd = surface["cda"] * stall + surface["cda_stall"] * (alpha - stall)
    elif alpha < -stall:
        cl = min(0.0, -surface["cla"] * stall + surface["cla_stall"] * (alpha + stall))
        cd = -surface["cda"] * stall + surface["cda_stall"] * (alpha + stall)
    else:
        cl, cd = surface["cla"] * alpha, surface["cda"] * alpha
    scale = 0.5 * surface["air_density"] * plane_speed**2 * surface["area"] * sweep_factor
    force = scale * (cl * lift_direction - abs(cd) * plane_velocity / plane_speed)
    assert np.isfinite(force).all(), "nonfinite aerodynamic force"
    return force


def integrate(state: np.ndarray, dt: float, acceleration) -> np.ndarray:
    """RK4 on position and velocity; actuator values are held during this step."""
    if not math.isfinite(dt) or dt <= 0 or state.shape != (6,) or not np.isfinite(state).all():
        raise ValueError("invalid integration state or dt")

    def derivative(value):
        return np.concatenate((value[3:], acceleration(value[3:])))

    k1 = derivative(state)
    k2 = derivative(state + dt * k1 / 2)
    k3 = derivative(state + dt * k2 / 2)
    k4 = derivative(state + dt * k3)
    result = state + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6
    if not np.isfinite(result).all():
        raise FloatingPointError("nonfinite integrated state")
    return result


def checked_xml(path: Path, expected_hash: str) -> ET.Element:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest().lower() != expected_hash.lower():
        raise ValueError(f"source hash mismatch: {path.name}")
    return ET.fromstring(payload)


def _rpy_rotation(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Gazebo/SDF roll-pitch-yaw rotation, expressed in the parent frame."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def _pose_rotation(pose: ET.Element | None) -> np.ndarray:
    if pose is None:
        return np.eye(3)
    values = [float(value) for value in pose.text.split()]
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        raise ValueError("invalid SDF pose")
    roll, pitch, yaw = values[3:]
    if pose.get("degrees") == "true":
        roll, pitch, yaw = (math.radians(value) for value in (roll, pitch, yaw))
    return _rpy_rotation(roll, pitch, yaw)


def _body_frames(
    trees: dict[str, ET.Element],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Resolve selected SDF link and joint rotations in their model body frame.

    The reduced model treats each source model frame as its body frame. It still
    resolves link/joint poses and their ``relative_to`` relationships instead of
    assuming a local +Z axis is already a body +Z axis. This supports PX4's
    merged X500 model, whose motor plugins and rotor geometry live in separate
    source files.
    """
    links: dict[str, ET.Element] = {}
    joints: dict[str, ET.Element] = {}
    model_names = set()
    for root in trees.values():
        for node in root.findall(".//model"):
            if node.get("name"):
                model_names.add(node.get("name"))
        for node in root.findall(".//link"):
            name = node.get("name")
            if not name or name in links:
                raise ValueError("ambiguous SDF link")
            links[name] = node
        for node in root.findall(".//joint"):
            name = node.get("name")
            if not name or name in joints:
                raise ValueError("ambiguous SDF joint")
            joints[name] = node

    link_cache: dict[str, np.ndarray] = {}
    joint_cache: dict[str, np.ndarray] = {}
    resolving: set[str] = set()

    def frame_rotation(name: str) -> np.ndarray:
        if name in {"__model__", *model_names}:
            return np.eye(3)
        if name in links:
            return link_rotation(name)
        if name in joints:
            return joint_rotation(name)
        raise ValueError(f"unresolved SDF frame: {name}")

    def resolve_pose(node: ET.Element, default_frame: str) -> np.ndarray:
        pose = node.find("pose")
        reference = pose.get("relative_to") if pose is not None else None
        return frame_rotation(reference or default_frame) @ _pose_rotation(pose)

    def link_rotation(name: str) -> np.ndarray:
        if name in link_cache:
            return link_cache[name]
        key = f"link:{name}"
        if key in resolving:
            raise ValueError("cyclic SDF frame reference")
        resolving.add(key)
        try:
            link_cache[name] = resolve_pose(links[name], "__model__")
        finally:
            resolving.remove(key)
        return link_cache[name]

    def joint_rotation(name: str) -> np.ndarray:
        if name in joint_cache:
            return joint_cache[name]
        key = f"joint:{name}"
        if key in resolving:
            raise ValueError("cyclic SDF frame reference")
        parent = joints[name].findtext("parent")
        if not parent:
            raise ValueError("SDF joint without parent")
        resolving.add(key)
        try:
            joint_cache[name] = resolve_pose(joints[name], parent)
        finally:
            resolving.remove(key)
        return joint_cache[name]

    for name in links:
        link_rotation(name)
    for name in joints:
        joint_rotation(name)
    return link_cache, joint_cache


def _motor_axes(
    trees: dict[str, ET.Element], files: tuple[str, ...], link_name: str, joint_name: str
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    selected = {filename: trees[filename] for filename in files}
    link_frames, joint_frames = _body_frames(selected)
    if link_name not in link_frames or joint_name not in joint_frames:
        raise ValueError(f"motor source geometry not found: {link_name}/{joint_name}")
    joint = next(
        node
        for root in selected.values()
        for node in root.findall(".//joint")
        if node.get("name") == joint_name
    )
    axis_text = joint.findtext("axis/xyz")
    if axis_text is None:
        raise ValueError(f"motor joint axis not found: {joint_name}")
    joint_axis = unit(np.array([float(value) for value in axis_text.split()]))
    if joint_axis.shape != (3,) or not np.isfinite(joint_axis).all():
        raise ValueError("invalid motor joint axis")
    thrust_axis = unit(link_frames[link_name] @ np.array([0.0, 0.0, 1.0]))
    drag_axis = unit(joint_frames[joint_name] @ joint_axis)
    return tuple(thrust_axis.tolist()), tuple(drag_axis.tolist())


@dataclass(frozen=True)
class ReferenceModel:
    model_id: str
    kind: str
    mass: float
    motors: tuple[dict, ...]
    surfaces: tuple[dict, ...]
    blades: tuple[dict, ...]
    definition: dict


def _surfaces(root: ET.Element) -> tuple[dict, ...]:
    fields = ("a0", "cla", "cda", "alpha_stall", "cla_stall", "cda_stall", "area", "air_density")
    result = []
    for node in root.findall(".//plugin"):
        if node.get("filename") != "gz-sim-lift-drag-system":
            continue
        surface = {key: float(node.findtext(key)) for key in fields}
        surface["forward"] = [float(x) for x in node.findtext("forward").split()]
        surface["upward"] = [float(x) for x in node.findtext("upward").split()]
        surface["link"] = node.findtext("link_name")
        surface["cp"] = [float(x) for x in node.findtext("cp").split()]
        result.append(surface)
    return tuple(result)


def load_catalog(config_path: Path) -> tuple[dict[str, ReferenceModel], dict]:
    """Offline, hash-checked source loading; no downloads or latest selection."""
    config_path = Path(config_path).resolve()
    config = OmegaConf.to_container(
        OmegaConf.load(config_path), resolve=True, throw_on_missing=True
    )
    if config["schema_version"] != "gazebo-reduced-force-v1":
        raise ValueError("unsupported schema")
    source_root = (config_path.parent / config["source_root"]).resolve()
    trees = {}
    for filename, record in config["sources"].items():
        path = (source_root / filename).resolve()
        if not path.is_relative_to(source_root):
            raise ValueError("source path escapes root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"].lower():
            raise ValueError(f"source hash mismatch: {filename}")
        if path.suffix == ".sdf":
            trees[filename] = checked_xml(path, record["sha256"])
    models = {}
    for model_id, definition in config["models"].items():
        if definition["kind"] not in ("wing", "multirotor", "vtol", "helicopter"):
            raise ValueError("unsupported model kind")
        mass = sum(
            float(n.text)
            for name in definition["mass_files"]
            for n in trees[name].findall(".//link/inertial/mass")
        )
        if mass <= 0 or not math.isfinite(mass):
            raise ValueError("invalid total mass")
        motors = []
        if definition.get("motor_file"):
            geometry_files = tuple(
                dict.fromkeys([*definition["mass_files"], definition["motor_file"]])
            )
            for node in trees[definition["motor_file"]].findall(".//plugin"):
                if node.get("filename") != "gz-sim-multicopter-motor-model-system":
                    continue
                link_name = node.findtext("linkName")
                joint_name = node.findtext("jointName")
                if not link_name or not joint_name:
                    raise ValueError("motor plugin missing link or joint name")
                thrust_axis, drag_axis = _motor_axes(trees, geometry_files, link_name, joint_name)
                motors.append(
                    dict(
                        link=link_name,
                        joint=joint_name,
                        role=definition["motor_roles"][link_name],
                        k=float(node.findtext("motorConstant")),
                        max_omega=float(node.findtext("maxRotVelocity")),
                        tau_up=float(node.findtext("timeConstantUp")),
                        tau_down=float(node.findtext("timeConstantDown")),
                        drag=float(node.findtext("rotorDragCoefficient")),
                        thrust_axis_body=thrust_axis,
                        drag_axis_body=drag_axis,
                    )
                )
        surfaces = (
            _surfaces(trees[definition["surface_file"]]) if definition.get("surface_file") else ()
        )
        blades = _surfaces(trees[definition["blade_file"]]) if definition.get("blade_file") else ()
        if blades:
            # Zero-articulation geometry: blade link pose plus rotated local cp.
            for blade in blades:
                node = trees[definition["blade_file"]].find(
                    f".//link[@name='{blade['link']}']/pose"
                )
                pose = [float(x) for x in node.text.split()]
                yaw = math.radians(pose[5]) if node.get("degrees") == "true" else pose[5]
                cp = blade["cp"]
                point = np.array(pose[:3]) + np.array(
                    [
                        math.cos(yaw) * cp[0] - math.sin(yaw) * cp[1],
                        math.sin(yaw) * cp[0] + math.cos(yaw) * cp[1],
                        cp[2],
                    ]
                )
                blade["radius"] = float(np.linalg.norm(point[:2]))
        models[model_id] = ReferenceModel(
            model_id, definition["kind"], mass, tuple(motors), surfaces, blades, definition
        )
    return models, config


def body_axes(
    velocity: np.ndarray, alpha: float, bank: float, rotor_axis: np.ndarray, wing_fraction: float
) -> np.ndarray:
    """Prescribed reduced attitude; columns are forward, left, and up (FLU)."""
    forward_v = unit(velocity, np.array([1.0, 0.0, 0.0]))
    left = unit(np.cross(np.array([0.0, 0.0, 1.0]), forward_v), np.array([0.0, 1.0, 0.0]))
    normal = np.cross(forward_v, left)
    lift_axis = math.cos(bank) * normal + math.sin(bank) * left
    wing_forward = math.cos(alpha) * forward_v + math.sin(alpha) * lift_axis
    wing_up = -math.sin(alpha) * forward_v + math.cos(alpha) * lift_axis
    heading = unit(np.array([velocity[0], velocity[1], 0.0]), np.array([1.0, 0.0, 0.0]))
    rotor_forward = unit(heading - np.dot(heading, rotor_axis) * rotor_axis)
    forward = unit(wing_fraction * wing_forward + (1 - wing_fraction) * rotor_forward)
    up_guess = wing_fraction * wing_up + (1 - wing_fraction) * rotor_axis
    up = unit(up_guess - np.dot(up_guess, forward) * forward)
    return np.column_stack((forward, np.cross(up, forward), up))


def forces(
    model: ReferenceModel, velocity: np.ndarray, actuator: dict, quadrature: int
) -> np.ndarray:
    """Aerodynamic + rotor forces. Gravity is added by the integrator caller."""
    axes = actuator["axes"]
    force = np.zeros(3)
    for surface in model.surfaces:
        force += lift_drag(velocity, axes @ surface["forward"], axes @ surface["upward"], surface)
    for index, motor in enumerate(model.motors):
        thrust_axis = unit(axes @ np.asarray(motor["thrust_axis_body"], dtype=float))
        drag_axis = unit(axes @ np.asarray(motor["drag_axis_body"], dtype=float))
        omega = actuator["omegas"][index]
        force += motor_thrust(omega, motor["k"], motor["max_omega"]) * thrust_axis
        force -= abs(omega) * motor["drag"] * (velocity - np.dot(velocity, drag_axis) * drag_axis)
    if model.blades:
        # Average source LiftDrag over rotor azimuth, omitting flapping/inflow dynamics.
        for blade in model.blades:
            for theta in np.arange(quadrature) * (2 * math.pi / quadrature):
                tangent = math.cos(theta) * axes[:, 0] + math.sin(theta) * axes[:, 1]
                collective = actuator["collective"]
                forward = math.cos(collective) * tangent + math.sin(collective) * axes[:, 2]
                up = -math.sin(collective) * tangent + math.cos(collective) * axes[:, 2]
                local_v = velocity + actuator["rotor_omega"] * blade["radius"] * tangent
                force += lift_drag(local_v, forward, up, blade) / quadrature
    assert np.isfinite(force).all(), "nonfinite total force"
    return force
