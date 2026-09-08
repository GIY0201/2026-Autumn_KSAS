"""Conditional steady-force exploration, not flight-certified performance.

Uses the pinned reference forces, bypassing the approximate tracking controller.
Wing speed is total speed; rotor speed is horizontal speed. A numerical failure
to find trim is not proof that no trim exists. Search ceilings remain open.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares

from .gazebo_reference import body_axes, forces


def solve_trim(model, mode, speed, vertical, turn, config, tolerance):
    """Balance longitudinal/vertical force, plus lateral force in rotor mode.

    Wing: vertical=path angle (deg), turn=bank (deg).
    Rotor: vertical=vertical speed (m/s), turn=track turn rate (deg/s).
    The prescribed-attitude force model does not establish moment equilibrium.
    """
    if mode not in ("wing", "rotor") or speed < 0 or tolerance <= 0:
        raise ValueError("invalid trim request")
    if not np.isfinite([speed, vertical, turn, tolerance]).all():
        raise ValueError("nonfinite trim request")
    settings, control = config["settings"], config["controller"]
    g = settings["gravity_mps2"]
    wing = mode == "wing"
    gamma = math.radians(vertical) if wing else 0.0
    velocity = np.array([speed * math.cos(gamma), 0, speed * math.sin(gamma)])
    if not wing:
        velocity = np.array([speed, 0, vertical], dtype=float)
    target = np.array([0, 0 if wing else speed * math.radians(turn), g])
    if wing:
        surfaces = [s for s in model.surfaces if s["upward"] == [0.0, 0.0, 1.0]]
        main = surfaces[0]
        cap = control["alpha_stall_fraction"] * main["alpha_stall"]
        lower, upper = [-cap - main["a0"], 0], [cap - main["a0"], 1]
        seeds = [[0.02, 0.5], [upper[0] * 0.8, 0.9], [lower[0] * 0.5, 0.9]]
    else:
        cap = math.radians(control["maximum_tilt_deg"])
        blade_cap = (
            control["alpha_stall_fraction"] * min(s["alpha_stall"] for s in model.blades)
            if model.blades else 1.0
        )
        lower, upper = [0, -math.pi, -blade_cap if model.blades else 0], [cap, math.pi, blade_cap]
        direction = math.atan2(target[1], max(speed * 0.1, 0.01))
        seeds = [[cap * f, direction, blade_cap * 0.3] for f in (0.1, 0.6, 0.95)]
        if model.surfaces:
            # Wing forces during rotor-mode climb can require rearward tilt.
            # Forward-only starts at azimuth zero cannot cross this symmetry.
            seeds += [[cap * f, sign * (math.pi - 0.1), 0.8]
                      for sign in (-1, 1) for f in (0.1, 0.6)]

    def actuator(parameters):
        if wing:
            alpha, power = parameters
            axis = np.array([0.0, 0.0, 1.0])
            bank, collective = math.radians(turn), 0.0
        else:
            tilt, azimuth, power = parameters
            axis = np.array([math.sin(tilt) * math.cos(azimuth), math.sin(tilt) * math.sin(azimuth), math.cos(tilt)])
            alpha, bank, collective = 0.0, 0.0, power if model.blades else 0.0
        role = "forward" if wing else "lift"
        return dict(
            axes=body_axes(velocity, alpha, bank, axis, float(wing)),
            omegas=[power * m["max_omega"] if m["role"] == role else 0.0 for m in model.motors],
            rotor_omega=model.definition.get("rotor_omega_rad_s", 0.0),
            collective=collective,
        )

    def residual(parameters):
        value = forces(model, velocity, actuator(parameters), settings["azimuth_samples"]) / model.mass - target
        return value[[0, 2]] if wing else value

    best = None
    for seed in seeds:
        fit = least_squares(residual, np.clip(seed, np.array(lower) + 1e-8, np.array(upper) - 1e-8), bounds=(lower, upper),
                            max_nfev=120, ftol=1e-10, xtol=1e-10, gtol=1e-10)
        error = float(np.linalg.norm(residual(fit.x)))
        if best is None or error < best[0]:
            best = error, fit
        if error <= tolerance:
            break
    error, fit = best
    net = forces(model, velocity, actuator(fit.x), settings["azimuth_samples"]) / model.mass - np.array([0, 0, g])
    omega = net[1] / velocity[0] if velocity[0] > 1e-9 else 0.0
    names = ["alpha", "motor"] if wing else ["tilt", "tilt_azimuth", "collective" if model.blades else "motor"]
    active = [names[i] + ("_upper" if upper[i] - x < 1e-4 else "_lower") for i, x in enumerate(fit.x)
              if min(upper[i] - x, x - lower[i]) < 1e-4]
    assert np.isfinite([error, *net, *fit.x]).all(), "nonfinite trim result"
    return dict(speed_mps=float(speed), feasible=error <= tolerance, force_residual_mps2=error,
                ax_mps2=float(net[0]), ay_mps2=float(net[1]), az_mps2=float(net[2]),
                turn_deg_s=math.degrees(omega), radius_m=float(velocity[0] / abs(omega)) if abs(omega) > 1e-9 else None,
                alpha_deg=math.degrees(fit.x[0]) if wing else 0.0,
                tilt_deg=0.0 if wing else math.degrees(fit.x[0]),
                collective_deg=math.degrees(fit.x[2]) if model.blades else 0.0,
                motor_fraction=float(fit.x[-1]) if model.motors else 0.0,
                active_bounds=";".join(active), solver_success=bool(fit.success))


def find_upper_boundary(probe, speeds, precision):
    """Scan the entire supplied range then refine its highest feasible bracket.

    Disconnected feasible intervals narrower than the scan can be missed. The
    result is a sampled numerical boundary, never a proof of the global maximum.
    """
    speeds = list(speeds)
    if precision <= 0 or not math.isfinite(precision) or len(speeds) < 2:
        raise ValueError("invalid boundary search")
    if not all(math.isfinite(x) and x >= 0 for x in speeds) or any(b <= a for a, b in zip(speeds, speeds[1:])):
        raise ValueError("speeds must be finite, nonnegative and strictly increasing")
    rows = [probe(speed) for speed in speeds]
    accepted = [i for i, row in enumerate(rows) if row["feasible"]]
    if not accepted:
        return dict(status="no_trim_found_on_scan", feasible_speed_mps=None, infeasible_speed_mps=None), rows
    last = accepted[-1]
    low = speeds[last]
    if last == len(speeds) - 1:
        return dict(status="open_at_search_ceiling", feasible_speed_mps=low, infeasible_speed_mps=None), rows
    high = speeds[last + 1]
    while high - low > precision:
        mid = (low + high) / 2
        row = probe(mid)
        rows.append(row)
        if row["feasible"]:
            low = mid
        else:
            high = mid
    assert 0 < high - low <= precision
    return dict(status="bracketed_trim_boundary", feasible_speed_mps=low, infeasible_speed_mps=high), rows
