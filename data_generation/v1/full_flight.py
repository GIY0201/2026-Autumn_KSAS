"""Continuous ENU point-mass full flights; no actuator or attitude dynamics."""

from __future__ import annotations

import math
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from .full_flight_config import catalog, validate_settings
from .full_flight_config import load_settings as load_settings
from .records import MotionEpisode, MotionEvent


def _smooth(u):
    return u**3 * (10 + u * (-15 + 6 * u))


class _Flight:
    """Compose continuous velocity targets, then integrate their common time axis."""

    def __init__(self, settings, obj, heading, direction, stretch):
        self.s, self.obj = settings, obj
        self.heading, self.direction, self.stretch = heading, direction, stretch
        self.t = [0.0]
        self.q = [np.zeros(3)]
        self.z = 0.0
        self.events = []

    def transition_time(self, target):
        delta = np.abs(np.asarray(target)[:2] - self.q[-1][:2])
        acceleration = np.array(
            [self.s["tangential_acceleration_mps2"], self.s["vertical_acceleration_mps2"]]
        )
        # Analytic extrema of the quintic ramp; vector check is performed below.
        duration = max(
            self.s["transition_min_s"],
            float(np.max(1.875 * delta / acceleration)),
            float(np.max(np.sqrt((10 / math.sqrt(3)) * delta / self.s["jerk_mps3"]))),
        )
        return duration * self.stretch

    def segment(self, target, duration, event_id):
        if duration <= 1e-9:
            return
        if self.t[-1] + duration > self.s["max_duration_s"]:
            raise ValueError(f"duration budget exceeded during {event_id}")
        start = self.t[-1]
        n = max(2, math.ceil(duration / self.s["dt_s"]))
        u = np.linspace(0, 1, n + 1)
        q = self.q[-1] + _smooth(u)[:, None] * (np.asarray(target) - self.q[-1])
        self.z += float(np.trapezoid(q[:, 1], dx=duration / n))
        self.t.extend((start + duration * u[1:]).tolist())
        self.q.extend(q[1:])
        self.events.append(
            MotionEvent(
                event_id, start, self.t[-1], "full_flight", True, "continuous_target_completion"
            )
        )

    def ramp(self, target, name, duration=None):
        self.segment(target, self.transition_time(target) if duration is None else duration, name)

    def maneuver(self, start_index, behavior):
        if behavior not in {"turn", "s_turn", "spiral", "orbit"}:
            return
        time = np.asarray(self.t[start_index:])
        duration = time[-1] - time[0]
        u = (time - time[0]) / duration
        fraction = self.s["turn_ramp_fraction"]
        envelope = _smooth(np.minimum(u / fraction, 1)) * _smooth(np.minimum((1 - u) / fraction, 1))
        rate = self.obj["turn_rate_rad_s"]
        if behavior in {"spiral", "orbit"}:
            rate = 2 * math.pi / float(np.trapezoid(envelope, time))
            if rate > self.obj["turn_rate_rad_s"] * (1 + 1e-9):
                raise ValueError("orbit duration cannot support configured turn rate")
        if behavior == "s_turn":
            envelope *= np.sin(2 * math.pi * u)
        for i, omega in enumerate(self.direction * rate * envelope):
            self.q[start_index + i][2] = float(omega)

    def maneuver_duration(self, behavior):
        if behavior in {"spiral", "orbit"}:
            return 2 * math.pi / (self.obj["turn_rate_rad_s"] * (1 - self.s["turn_ramp_fraction"]))
        return 0.0

    def altitude(self, altitude, horizontal, peak_vertical, behavior, name, end_vertical=0):
        """Solve hold time/peak vertical velocity; never overwrite altitude state."""
        height = altitude - self.z
        if height * peak_vertical <= 0:
            raise ValueError(f"invalid altitude direction: {name}")
        begin = len(self.t) - 1
        start_vertical = self.q[-1][1]
        ramp = max(
            self.transition_time([horizontal, peak_vertical, 0]),
            self.transition_time([horizontal, end_vertical, 0]),
        )
        hold = (height - 0.5 * ramp * (start_vertical + end_vertical)) / peak_vertical - ramp
        total = max(2 * ramp + max(hold, 0), self.maneuver_duration(behavior))
        hold = total - 2 * ramp
        peak = (height - 0.5 * ramp * (start_vertical + end_vertical)) / (hold + ramp)
        if peak * peak_vertical <= 0 or abs(peak) > abs(peak_vertical) * (1 + 1e-9):
            raise ValueError(f"insufficient altitude for smooth recovery: {name}")
        self.segment([horizontal, peak, 0], ramp, name + "_entry")
        self.segment([horizontal, peak, 0], hold, name + "_hold")
        self.segment([horizontal, end_vertical, 0], ramp, name + "_recovery")
        self.maneuver(begin, behavior)
        if abs(self.z - altitude) > self.s["altitude_tolerance_m"]:
            raise ValueError(f"integrated altitude mismatch: {name}")

    def cruise(self, horizontal, behavior):
        target = 0 if behavior == "hover" else horizontal
        self.ramp([target, 0, 0], "cruise_entry")
        begin = len(self.t) - 1
        duration = max(self.s["cruise_hold_s"], self.maneuver_duration(behavior))
        if behavior == "stop_restart":
            self.segment([horizontal, 0, 0], duration / 2, "cruise_forward")
            self.ramp([0, 0, 0], "cruise_stop")
            self.segment([0, 0, 0], self.s["hover_hold_s"], "cruise_hover")
            self.ramp([horizontal, 0, 0], "cruise_restart")
            self.segment([horizontal, 0, 0], duration / 2, "cruise_forward_again")
        else:
            self.segment([target, 0, 0], duration, "cruise_" + behavior)
            self.maneuver(begin, behavior)

    def arrays(self):
        time, q = np.asarray(self.t), np.asarray(self.q)
        dt = np.diff(time)
        heading = self.heading + np.r_[0, np.cumsum(0.5 * (q[1:, 2] + q[:-1, 2]) * dt)]
        velocity = np.column_stack([q[:, 0] * np.cos(heading), q[:, 0] * np.sin(heading), q[:, 1]])
        position = np.vstack(
            [np.zeros(3), np.cumsum(0.5 * (velocity[1:] + velocity[:-1]) * dt[:, None], axis=0)]
        )
        acceleration = np.gradient(velocity, time, axis=0, edge_order=2)
        jerk = np.gradient(acceleration, time, axis=0, edge_order=2)
        assert np.isfinite(position).all() and np.isfinite(jerk).all()
        return time, q, position, velocity, acceleration, jerk


def _compile(settings, obj, object_id, choice, rng_values, stretch):
    heading, direction, scale = rng_values
    f = _Flight(settings, obj, heading, direction, stretch)
    speed = obj["horizontal_speed_mps"] * scale
    f.segment([0, 0, 0], settings["ground_hold_s"], "ground_start")
    if object_id == "fixed_wing":
        f.ramp([speed, 0, 0], "ground_takeoff_run")
        low_w = speed * math.tan(math.radians(obj["low_angle_deg"]))
        f.altitude(obj["initial_altitude_m"], speed, low_w, "straight", "initial_climb", low_w)
    else:
        f.altitude(
            obj["initial_altitude_m"],
            0,
            obj["takeoff_vertical_speed_mps"],
            "vertical",
            "vertical_takeoff",
        )
        f.segment([0, 0, 0], settings["hover_hold_s"], "initial_hover")
        if object_id == "vtol":
            f.ramp(
                [obj["transition_speed_mps"], 0, 0], "transition_out", obj["outbound_transition_s"]
            )
    wing = object_id != "helicopter"
    w = speed * math.tan(math.radians(obj["max_angle_deg"])) if wing else obj["vertical_speed_mps"]
    climb_speed = 0 if choice["climb"] == "vertical" else speed
    f.altitude(
        obj["altitude_m"], climb_speed, w, choice["climb"], "selected_climb_" + choice["climb"]
    )
    f.cruise(speed, choice["cruise"])
    descent_speed = 0 if choice["descent"] == "vertical" else speed
    # Establish descent horizontal speed before spending the altitude budget.
    f.ramp([descent_speed, 0, 0], "descent_alignment")
    end_w = -low_w if object_id == "fixed_wing" else 0
    f.altitude(
        obj["approach_altitude_m"],
        descent_speed,
        -w,
        choice["descent"],
        "selected_descent_" + choice["descent"],
        end_w,
    )
    if object_id == "fixed_wing":
        # Constant -3 degree final approach precedes a separately solved flare.
        flare = obj["flare_altitude_m"]
        f.segment([speed, -low_w, 0], (f.z - flare) / low_w, "final_approach")
        flare_time = 2 * flare / low_w
        f.segment([speed, 0, 0], flare_time, "landing_flare")
        f.ramp([0, 0, 0], "ground_braking")
    else:
        if object_id == "vtol":
            start = f.t[-1]
            duration = max(f.transition_time([0, 0, 0]), obj["inbound_transition_s"])
            f.ramp([0, 0, 0], "inbound_deceleration", duration)
            f.events.pop()
            split = start + obj["inbound_transition_s"]
            f.events.append(
                MotionEvent(
                    "transition_in", start, split, "full_flight", True, "rotor_mode_not_stop"
                )
            )
            if f.t[-1] > split:
                f.events.append(
                    MotionEvent(
                        "residual_braking", split, f.t[-1], "full_flight", True, "hover_reached"
                    )
                )
        else:
            f.ramp([0, 0, 0], "landing_horizontal_braking")
        f.segment([0, 0, 0], settings["hover_hold_s"], "landing_hover")
        slow_height = obj["slow_landing_altitude_m"]
        slow_speed = obj["slow_landing_speed_mps"]
        f.altitude(
            slow_height,
            0,
            -obj["landing_vertical_speed_mps"],
            "vertical",
            "vertical_approach",
            -slow_speed,
        )
        f.segment([0, 0, 0], 2 * slow_height / slow_speed, "slow_vertical_landing")
    output_dt = settings["output_dt_s"]
    end = math.ceil((f.t[-1] + settings["ground_hold_s"]) / output_dt) * output_dt
    f.segment([0, 0, 0], end - f.t[-1], "ground_stop")
    return f


def run_full_flight_episode(seed, *, settings, object_id, scenario_id=None, direction=None):
    """Generate one named flight or reject an infeasible configuration explicitly."""
    validate_settings(settings)
    if (
        isinstance(seed, bool)
        or not isinstance(seed, (int, np.integer))
        or not 0 <= seed <= 2**32 - 1
    ):
        raise ValueError("invalid full-flight seed")
    entries = catalog(settings, object_id)
    rng = np.random.default_rng(seed)
    if scenario_id is None:
        scenario_id = str(rng.choice(list(entries)))
    if scenario_id not in entries:
        raise ValueError("unsupported scenario for selected object")
    if direction is not None and direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    choice = entries[scenario_id]
    heading = math.radians(rng.uniform(*settings["heading_range_deg"]))
    sampled_direction = int(rng.choice([-1, 1]))
    speed_scale = rng.uniform(*settings["speed_scale_range"])
    values = (
        heading,
        sampled_direction if direction is None else direction,
        speed_scale,
    )
    obj = settings["objects"][object_id]
    for attempt in range(settings["max_attempts"]):
        f = _compile(
            settings, obj, object_id, choice, values, settings["stretch_factor"] ** attempt
        )
        time, q, p, v, a, j = f.arrays()
        tol = settings["numeric_tolerance"]
        checks = {
            "horizontal acceleration": np.linalg.norm(a[:, :2], axis=1).max()
            <= settings["horizontal_acceleration_mps2"] + tol,
            "vertical acceleration": abs(a[:, 2]).max()
            <= settings["vertical_acceleration_mps2"] + tol,
            "jerk": np.linalg.norm(j, axis=1).max() <= settings["jerk_mps3"] + tol,
        }
        if all(checks.values()):
            break
    else:
        raise ValueError(
            "full-flight constraint failure: " + ", ".join(k for k, ok in checks.items() if not ok)
        )
    altitude_tol = settings["altitude_tolerance_m"]
    if (
        p[:, 2].min() < -altitude_tol
        or p[:, 2].max() > settings["max_altitude_m"] + altitude_tol
        or abs(p[-1, 2]) > altitude_tol
    ):
        raise ValueError("full-flight altitude bounds not met")
    count = round(time[-1] / settings["output_dt_s"]) + 1
    output_time = np.arange(count) * settings["output_dt_s"]

    def sample(array):
        return np.column_stack(
            [np.interp(output_time, time, array[:, axis]) for axis in range(array.shape[1])]
        )

    diagnostic = sample(np.column_stack([q, np.linalg.norm(a, axis=1), np.linalg.norm(j, axis=1)]))
    names = (
        "horizontal_speed_mps",
        "vertical_speed_mps",
        "track_turn_rate_rad_s",
        "acceleration_mps2",
        "jerk_mps3",
    )
    identity = f"full-flight-v1/{object_id}/{scenario_id}/{seed}"
    if direction is not None:
        identity += f"/direction={values[1]}"
    return MotionEpisode(
        str(uuid5(NAMESPACE_URL, identity)),
        "complete",
        None,
        int(seed),
        np.arange(count),
        output_time,
        sample(p),
        sample(v),
        dict(zip(names, diagnostic.T, strict=True)),
        (
            MotionEvent(
                f"scenario:{scenario_id}:{choice['label']}",
                0,
                0,
                "scenario_selection",
                True,
                f"seed={seed}",
            ),
            *(
                (
                    MotionEvent(
                        "turn_direction:" + ("left" if values[1] > 0 else "right"),
                        0,
                        0,
                        "schedule_control",
                        True,
                        "explicit" if direction is not None else "seeded_random",
                    ),
                )
                if any(
                    choice[phase] in {"turn", "s_turn", "spiral", "orbit"}
                    for phase in ("climb", "cruise", "descent")
                )
                else ()
            ),
            *f.events,
        ),
    )
