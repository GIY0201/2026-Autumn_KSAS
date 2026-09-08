"""Force-driven reference trials with explicit experimental attitude control."""

from __future__ import annotations

import itertools
import math

import numpy as np

from .gazebo_reference import ReferenceModel, body_axes, forces, integrate, unit


def _lag(current, target, tau, dt):
    return current + (target - current) * (-math.expm1(-dt / tau))


def schedule(t: float, settings: dict) -> tuple[str, float]:
    first = settings["settle_s"]
    second = first + settings["entry_s"]
    third = second + settings["hold_s"]
    if t < first:
        return "settle", 0.0
    if t < second:
        x = (t - first) / settings["entry_s"]
        return "entry", x * x * (3 - 2 * x)
    if t <= third:
        return "hold", 1.0
    x = min(1.0, (t - third) / settings["recovery_s"])
    return "recovery", 1 - x * x * (3 - 2 * x)


def validate(case: dict, settings: dict, controller: dict, model: ReferenceModel):
    if case["model_id"] != model.model_id or case["mode"] not in model.definition["modes"]:
        raise ValueError("unsupported model/mode combination")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in settings.values()):
        raise ValueError("nonfinite or invalid settings")
    if any(x <= 0 for x in settings.values()):
        raise ValueError("settings must be positive")
    for key in ("speed_mps", "vertical_mps", "path_angle_deg", "turn_deg_s", "bank_deg"):
        if not math.isfinite(case[key]):
            raise ValueError("nonfinite case")
    if case["speed_mps"] < 0 or abs(case["path_angle_deg"]) >= 85:
        raise ValueError("invalid speed or path angle")
    if not all(math.isfinite(x) and x > 0 for x in controller.values()):
        raise ValueError("invalid controller")
    if not 0 < controller["alpha_stall_fraction"] <= 1:
        raise ValueError("invalid stall fraction")
    if max(controller["maximum_tilt_deg"], controller["maximum_bank_deg"]) >= 80:
        raise ValueError("attitude cap too large for reduced controller")
    if settings["azimuth_samples"] < 4 or settings["azimuth_samples"] % 2:
        raise ValueError("azimuth samples must be even and at least four")
    dt = settings["dt_s"]
    for key in (
        "output_dt_s",
        "settle_s",
        "entry_s",
        "hold_s",
        "recovery_s",
        "terminal_window_s",
    ):
        ratio = settings[key] / dt
        if abs(ratio - round(ratio)) > 1e-8:
            raise ValueError(f"{key} must be a multiple of dt")
    if settings["output_dt_s"] < dt:
        raise ValueError("output interval must not be below integration dt")
    if settings["terminal_window_s"] > settings["hold_s"]:
        raise ValueError("terminal window exceeds hold")


def cases_from_config(config: dict) -> list[dict]:
    result = []
    for model_id, definition in config["models"].items():
        for mode in definition["modes"]:
            grid = config["grids"][mode]
            if mode == "wing":
                combinations = itertools.product(
                    grid["speeds_mps"], grid["path_angles_deg"], grid["banks_deg"]
                )
            elif mode == "rotor":
                combinations = itertools.product(
                    grid["speeds_mps"], grid["vertical_speeds_mps"], grid["turns_deg_s"]
                )
            else:
                combinations = ((v, 0.0, 0.0) for v in grid["speeds_mps"])
            for speed, vertical, turn in combinations:
                if mode == "rotor" and speed == 0 and turn != 0:
                    continue
                if mode == "wing":
                    motion_label = f"속력 {speed:g} m/s · 경로각 {vertical:g}° · bank {turn:g}°"
                elif mode == "rotor":
                    motion_label = (
                        f"수평 속력 {speed:g} m/s · 수직 {vertical:g} m/s · 선회 {turn:g}°/s"
                    )
                else:
                    motion_label = f"속력 {speed:g} m/s"
                result.append(
                    dict(
                        trial_id=f"{model_id}_{mode}_{len(result) + 1:04d}",
                        label=f"{definition['label']} · {mode} · {motion_label}",
                        model_id=model_id,
                        mode=mode,
                        speed_mps=speed,
                        vertical_mps=vertical if mode == "rotor" else 0.0,
                        path_angle_deg=vertical if mode == "wing" else 0.0,
                        turn_deg_s=turn if mode == "rotor" else 0.0,
                        bank_deg=turn if mode == "wing" else 0.0,
                    )
                )
    return result


def _commands(case, model, blend, heading):
    wing = case["mode"] == "wing"
    start_speed = model.definition["initial_speed_mps"] if wing else 0.0
    speed = start_speed + blend * (case["speed_mps"] - start_speed)
    gamma = math.radians(case["path_angle_deg"]) * blend
    vertical = speed * math.sin(gamma) if wing else case["vertical_mps"] * blend
    horizontal = speed * math.cos(gamma) if wing else speed
    return dict(
        speed=speed,
        gamma=gamma,
        vertical=vertical,
        horizontal=horizontal,
        bank=math.radians(case["bank_deg"]) * blend,
        turn=math.radians(case["turn_deg_s"]) * blend,
        velocity=np.array(
            [horizontal * math.cos(heading), horizontal * math.sin(heading), vertical]
        ),
    )


def _controller(model, velocity, actuator, command, wing_fraction, settings, control):
    dt, g = settings["dt_s"], settings["gravity_mps2"]
    weight = model.mass * g
    speed = float(np.linalg.norm(velocity))
    horizontal = math.hypot(velocity[0], velocity[1])
    gamma = math.atan2(velocity[2], horizontal)
    requested = control["velocity_gain_per_s"] * (command["velocity"] - velocity)
    requested += np.array(
        [-command["velocity"][1] * command["turn"], command["velocity"][0] * command["turn"], 0.0]
    )
    requested[2] += g
    tilt = math.radians(control["maximum_tilt_deg"])
    requested[2] = max(requested[2], 1e-6)
    radial = float(np.linalg.norm(requested[:2]))
    limit = requested[2] * math.tan(tilt)
    saturated = radial > limit
    if saturated:
        requested[:2] *= limit / radial
    target_axis = unit(requested)
    actuator["rotor_axis"] = unit(
        _lag(actuator["rotor_axis"], target_axis, control["attitude_tau_s"], dt)
    )
    bank_cap = math.radians(control["maximum_bank_deg"])
    bank_target = float(np.clip(command["bank"], -bank_cap, bank_cap))
    saturated |= bank_target != command["bank"]
    actuator["bank"] = _lag(actuator["bank"], bank_target, control["bank_tau_s"], dt)
    wing_surfaces = [s for s in model.surfaces if s["upward"] == [0.0, 0.0, 1.0]]
    if wing_surfaces:
        q = 0.5 * wing_surfaces[0]["air_density"] * max(speed * speed, 1e-6)
        denominator = sum(s["area"] * s["cla"] for s in wing_surfaces)
        offset = sum(s["area"] * s["cla"] * s["a0"] for s in wing_surfaces) / denominator
        normal_a = g * math.cos(gamma) + control["path_gain_per_s"] * speed * (
            command["gamma"] - gamma
        )
        target_lift = model.mass * normal_a / max(math.cos(actuator["bank"]), 1e-6)
        target_alpha = wing_fraction * target_lift / (q * denominator) - offset
        main = wing_surfaces[0]
        low = -control["alpha_stall_fraction"] * main["alpha_stall"] - main["a0"]
        high = control["alpha_stall_fraction"] * main["alpha_stall"] - main["a0"]
        clipped = float(np.clip(target_alpha, low, high))
        saturated |= clipped != target_alpha
        actuator["alpha"] = _lag(actuator["alpha"], clipped, control["alpha_tau_s"], dt)
    actuator["axes"] = body_axes(
        velocity, actuator["alpha"], actuator["bank"], actuator["rotor_axis"], wing_fraction
    )
    axes = actuator["axes"]
    # Measure aerodynamic load with propulsion disabled; do not replace net force.
    passive = dict(actuator, omegas=[0.0] * len(model.motors), rotor_omega=0.0)
    aerodynamic = (
        forces(model, velocity, passive, settings["azimuth_samples"])
        if model.surfaces
        else np.zeros(3)
    )
    drag = -float(np.dot(aerodynamic, unit(velocity, np.array([1.0, 0.0, 0.0]))))
    forward_t = (
        max(
            0.0,
            drag
            + weight * math.sin(gamma)
            + model.mass * control["speed_gain_per_s"] * (command["speed"] - speed),
        )
        * wing_fraction
    )
    lift_t = max(0.0, float(np.dot(model.mass * requested - aerodynamic, axes[:, 2]))) * (
        1 - wing_fraction
    )
    for role, target_t in (("forward", forward_t), ("lift", lift_t)):
        selected = [i for i, motor in enumerate(model.motors) if motor["role"] == role]
        total_k = sum(model.motors[i]["k"] for i in selected)
        if not selected:
            continue
        desired_omega = math.sqrt(target_t / total_k)
        for i in selected:
            motor = model.motors[i]
            target = min(desired_omega, motor["max_omega"])
            saturated |= target < desired_omega
            tau = motor["tau_up"] if target > actuator["omegas"][i] else motor["tau_down"]
            actuator["omegas"][i] = _lag(actuator["omegas"][i], target, tau, dt)
    if model.blades:
        # Collective inversion is a controller approximation; actual force uses
        # the full local-velocity LiftDrag primitive at each azimuth sample.
        axial = float(np.dot(velocity, axes[:, 2]))
        denominator = sum(
            0.5
            * s["air_density"]
            * (actuator["rotor_omega"] * s["radius"]) ** 2
            * s["area"]
            * s["cla"]
            for s in model.blades
        )
        reference_u = actuator["rotor_omega"] * model.blades[0]["radius"]
        target = lift_t / denominator + math.atan2(axial, reference_u)
        cap = model.blades[0]["alpha_stall"] * control["alpha_stall_fraction"]
        clipped = float(np.clip(target, -cap, cap))
        saturated |= clipped != target
        actuator["collective"] = _lag(
            actuator["collective"], clipped, control["collective_tau_s"], dt
        )
    actuator["saturated"] = bool(saturated)


def run_trial(model: ReferenceModel, case: dict, settings: dict, controller: dict) -> dict:
    validate(case, settings, controller, model)
    dt, g = settings["dt_s"], settings["gravity_mps2"]
    duration = sum(settings[k] for k in ("settle_s", "entry_s", "hold_s", "recovery_s"))
    count = round(duration / dt)
    stride = round(settings["output_dt_s"] / dt)
    initial_speed = model.definition["initial_speed_mps"] if case["mode"] == "wing" else 0.0
    state = np.array([0.0, 0.0, settings["initial_altitude_m"], initial_speed, 0.0, 0.0])
    actuator = dict(
        alpha=0.0,
        bank=0.0,
        collective=0.0,
        rotor_axis=np.array([0.0, 0.0, 1.0]),
        axes=np.eye(3),
        omegas=[0.0] * len(model.motors),
        rotor_omega=model.definition.get("rotor_omega_rad_s", 0.0),
        saturated=False,
    )
    heading = 0.0
    rows, diagnostic_rows = [], []
    failure = ""
    for index in range(count + 1):
        t = index * dt
        phase, blend = schedule(t, settings)
        fraction = 1.0 if case["mode"] == "wing" else blend if case["mode"] == "transition" else 0.0
        command = _commands(case, model, blend, heading)
        row = None
        try:
            _controller(model, state[3:], actuator, command, fraction, settings, controller)
            held = dict(
                actuator,
                axes=actuator["axes"].copy(),
                omegas=list(actuator["omegas"]),
            )

            def acceleration(v, held=held):
                return forces(model, v, held, settings["azimuth_samples"]) / model.mass - np.array(
                    [0.0, 0.0, g]
                )

            a = acceleration(state[3:])
            horizontal = math.hypot(state[3], state[4])
            speed = float(np.linalg.norm(state[3:]))
            omega = (
                (state[3] * a[1] - state[4] * a[0]) / horizontal**2 if horizontal > 1e-6 else 0.0
            )
            radius = horizontal / abs(omega) if horizontal > 1e-6 and abs(omega) > 1e-6 else ""
            row = dict(
                trial_id=case["trial_id"],
                model_id=model.model_id,
                mode=case["mode"],
                t_s=t,
                phase=phase,
                x_m=state[0],
                y_m=state[1],
                z_m=state[2],
                vx_mps=state[3],
                vy_mps=state[4],
                vz_mps=state[5],
                ax_mps2=a[0],
                ay_mps2=a[1],
                az_mps2=a[2],
                speed_mps=speed,
                horizontal_mps=horizontal,
                path_angle_deg=math.degrees(math.atan2(state[5], horizontal)),
                turn_deg_s=math.degrees(omega),
                radius_m=radius,
                acceleration_mps2=float(np.linalg.norm(a)),
                horizontal_acceleration_mps2=math.hypot(a[0], a[1]),
                command_speed_mps=command["speed"],
                command_vertical_mps=command["vertical"],
                command_path_angle_deg=math.degrees(command["gamma"]),
                command_turn_deg_s=math.degrees(command["turn"]),
                command_bank_deg=math.degrees(command["bank"]),
                actual_bank_deg=math.degrees(actuator["bank"]),
                alpha_deg=math.degrees(actuator["alpha"]),
                collective_deg=math.degrees(actuator["collective"]),
                wing_fraction=fraction,
                actuator_saturated=actuator["saturated"],
            )
            diagnostic_rows.append(row)
            if index % stride == 0 or index == count:
                rows.append(row)
            if state[2] < 0:
                raise FloatingPointError("ground_crossing; no ground-contact model")
            if speed > settings["failure_speed_mps"]:
                raise FloatingPointError("diagnostic speed cutoff exceeded")
            if index != count:
                state = integrate(state, dt, acceleration)
                heading += command["turn"] * dt
        except (FloatingPointError, ValueError, OverflowError) as exc:
            failure = str(exc)
            if row is not None and (not rows or rows[-1]["t_s"] != row["t_s"]):
                rows.append(row)
            break
    hold_end = settings["settle_s"] + settings["entry_s"] + settings["hold_s"]
    terminal = [
        r
        for r in diagnostic_rows
        if hold_end - settings["terminal_window_s"] <= r["t_s"] <= hold_end
    ]
    covered = bool(terminal) and terminal[-1]["t_s"] >= hold_end - 1e-8
    wing_target = case["mode"] in ("wing", "transition")
    matches = []
    for r in terminal:
        if wing_target:
            matches.append(
                abs(r["speed_mps"] - case["speed_mps"]) <= settings["speed_tolerance_mps"]
                and abs(r["path_angle_deg"] - case["path_angle_deg"])
                <= settings["angle_tolerance_deg"]
                and abs(r["actual_bank_deg"] - case["bank_deg"]) <= settings["angle_tolerance_deg"]
            )
        else:
            matches.append(
                abs(r["horizontal_mps"] - case["speed_mps"]) <= settings["speed_tolerance_mps"]
                and abs(r["vz_mps"] - case["vertical_mps"]) <= settings["vertical_tolerance_mps"]
                and (
                    case["speed_mps"] == 0
                    or abs(r["turn_deg_s"] - case["turn_deg_s"]) <= settings["turn_tolerance_deg_s"]
                )
            )
    summary = dict(
        **case,
        status="failed" if failure else "complete",
        failure_reason=failure,
        target_met=bool(covered and all(matches)),
        terminal_window_complete=covered,
        recovery_complete=bool(
            not failure and diagnostic_rows and diagnostic_rows[-1]["t_s"] >= duration - 1e-8
        ),
        sample_count=len(rows),
        elapsed_s=rows[-1]["t_s"] if rows else 0.0,
        terminal_sample_count=len(terminal),
        saturated_fraction=float(np.mean([r["actuator_saturated"] for r in diagnostic_rows]))
        if diagnostic_rows
        else "",
    )
    for key in (
        "speed_mps",
        "horizontal_mps",
        "vz_mps",
        "path_angle_deg",
        "turn_deg_s",
        "radius_m",
        "horizontal_acceleration_mps2",
        "az_mps2",
        "acceleration_mps2",
    ):
        values = [r[key] for r in terminal if r[key] != ""]
        for statistic, function in (("mean", np.mean), ("min", np.min), ("max", np.max)):
            summary[f"achieved_{key}_{statistic}"] = float(function(values)) if values else ""
    return dict(summary=summary, samples=rows)
