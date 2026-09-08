"""Deterministic, fixed-step interactive kinematics with private simulation truth.

Aircraft attitude cues are command visualizations, not six-DOF dynamics.
Only drain_observations' public fields may reach the predictor adapter.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from omegaconf import OmegaConf

from contracts.v1.validation import VARIANT_SIGMAS

from .full_flight import run_full_flight_episode
from .full_flight_config import catalog, load_settings


def load_simulation_settings() -> dict:
    path = Path(__file__).parent / "configs/simulation_settings.yaml"
    settings = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    settings["ui"] = settings.pop("display")
    settings["flight"] = load_settings(path.parent / settings["full_flight_settings"])
    if settings["dt_s"] <= 0 or not math.isclose(
        settings["output_dt_s"] / settings["dt_s"],
        round(settings["output_dt_s"] / settings["dt_s"]),
    ):
        raise ValueError("Observation period must be an integer number of simulation steps")
    return settings


def simulation_catalog() -> dict:
    settings = load_simulation_settings()
    flight = settings["flight"]
    return {
        "defaults": {**settings["defaults"], "max_objects": settings["max_objects"]},
        "aircraft": [
            {
                "id": kind,
                "label": obj["label"],
                "ctol_initial_speed_mps": obj["horizontal_speed_mps"],
                "minimum_airborne_speed_mps": obj.get("minimum_airborne_horizontal_speed_mps", 0),
                "max_speed_mps": obj["horizontal_speed_mps"],
                "initial_speed_mps": obj["horizontal_speed_mps"] if kind == "fixed_wing" else 0,
                "initial_altitude_m": obj["initial_altitude_m"],
                "scenarios": [
                    {"id": key, "label": value["label"]}
                    for key, value in catalog(flight, kind).items()
                ],
            }
            for kind, obj in flight["objects"].items()
        ],
        "models": [],
        "limits": {
            "max_objects": settings["max_objects"],
            "max_duration_s": flight["max_duration_s"],
            "max_altitude_m": flight["max_altitude_m"],
            "sigma_m": list(VARIANT_SIGMAS.values()),
        },
        "ui": {**settings["ui"], "initial_spacing_m": settings["default_spacing_m"]},
    }


def _number(value, name: str, low=-math.inf, high=math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} outside allowed range")
    return result


def _toward(current, target, rate, dt):
    return current + np.clip(target - current, -rate * dt, rate * dt)


@dataclass
class Aircraft:
    id: str
    kind: str
    position: np.ndarray
    velocity: np.ndarray
    heading: float
    control_mode: str
    power: float
    target_speed: float
    pilot: str
    phase: str
    rng: np.random.Generator
    initial: dict
    yaw_rate: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    transition_start: float | None = None
    transition_duration: float = 0.0
    transition_progress: float = 0.0
    notice: str = ""
    has_flown: bool = False
    terminal: bool = False
    scenario: object = None
    scenario_offset_s: float = 0.0
    scenario_origin: np.ndarray = field(default_factory=lambda: np.zeros(3))
    scenario_rotation: float = 0.0
    history: list = field(default_factory=list)


class SimulationEngine:
    """Pure simulation owner. Wall clock, HTTP, files and inference live outside."""

    def __init__(self, config: dict, settings: dict | None = None):
        self.settings = copy.deepcopy(settings or load_simulation_settings())
        self.config = {**self.settings["defaults"], **copy.deepcopy(config)}
        self.flight = self.settings["flight"]
        self.control = self.settings["control"]
        if self.config["mode"] not in ("manual", "scenario"):
            raise ValueError("Unknown operating mode")
        if self.config["start_mode"] not in ("ground", "air"):
            raise ValueError("Unknown starting mode")
        seed = self.config["seed"]
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be uint32")
        sigma = _number(self.config["sigma_m"], "sigma_m")
        if sigma not in VARIANT_SIGMAS.values():
            raise ValueError("Unsupported observation sigma")
        rows = self.config.get("objects")
        if not isinstance(rows, list) or not 1 <= len(rows) <= self.settings["max_objects"]:
            raise ValueError("Aircraft count outside configured range")
        self.status = "ready"
        self.step = 0
        self.notice = ""
        self.keys: set[str] = set()
        self.events: list[dict] = []
        self.objects: list[Aircraft] = []
        self._observations: list[dict] = []
        self._observation_stride = round(self.settings["output_dt_s"] / self.settings["dt_s"])
        self._initialized = False
        for index, row in enumerate(rows):
            self.objects.append(self._aircraft(index, row))
        self.selected_id = self.objects[0].id
        self.config["objects"] = [copy.deepcopy(obj.initial) for obj in self.objects]

    @property
    def time_s(self) -> float:
        return self.step * self.settings["dt_s"]

    def _aircraft(self, index, row) -> Aircraft:
        if not isinstance(row, dict) or row.get("kind") not in self.flight["objects"]:
            raise ValueError("Unsupported aircraft kind")
        kind = row["kind"]
        profile = self.flight["objects"][kind]
        air = self.config["start_mode"] == "air"
        raw = row.get(
            "position_enu_m",
            [
                index * self.settings["default_spacing_m"],
                0,
                profile["initial_altitude_m"] if air else 0,
            ],
        )
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ValueError("position_enu_m must contain three coordinates")
        position = np.array([_number(x, "position") for x in raw])
        if not 0 <= position[2] <= self.flight["max_altitude_m"]:
            raise ValueError("Initial altitude outside bounds")
        if not air and position[2] != 0:
            raise ValueError("Ground start requires altitude zero")
        if air and position[2] <= 0:
            raise ValueError("Air start requires positive altitude")
        heading_deg = _number(row.get("heading_deg", 0), "heading_deg", -360, 360)
        heading = math.radians(heading_deg)
        mode = row.get("initial_mode", "CTOL" if kind == "fixed_wing" else "VTOL")
        if mode not in ("VTOL", "CTOL"):
            raise ValueError("Unsupported initial control mode")
        mode = "CTOL" if kind == "fixed_wing" else "VTOL" if kind == "helicopter" else mode
        if not air and kind == "vtol" and mode != "VTOL":
            raise ValueError("VTOL ground start must use rotor mode")
        speed = _number(
            row.get("initial_speed_mps", profile["horizontal_speed_mps"] if mode == "CTOL" else 0),
            "initial_speed_mps",
            0,
            profile["horizontal_speed_mps"],
        )
        speed = speed if air else 0
        if air and mode == "CTOL" and speed < profile["minimum_airborne_horizontal_speed_mps"]:
            raise ValueError("Initial CTOL speed below airborne minimum")
        normalized = {
            **row,
            "kind": kind,
            "position_enu_m": position.tolist(),
            "heading_deg": heading_deg,
            "initial_speed_mps": speed,
            "initial_mode": mode,
        }
        pilot = "manual" if index == 0 and self.config["mode"] == "manual" else "scenario"
        obj = Aircraft(
            str(uuid5(NAMESPACE_URL, f"interactive/{self.config['seed']}/{index}")),
            kind,
            position,
            np.array([speed * math.cos(heading), speed * math.sin(heading), 0]),
            heading,
            mode,
            self.control["hover_power"] if air else self.control["ground_power"],
            speed,
            pilot,
            "cruise" if air and mode == "CTOL" else "hover" if air else "ground",
            np.random.default_rng(np.random.SeedSequence([self.config["seed"], index, 1])),
            normalized,
            has_flown=air,
        )
        if pilot == "scenario":
            entries = catalog(self.flight, kind)
            scenario_id = row.get("scenario_id") or next(iter(entries))
            obj.initial["scenario_id"] = scenario_id
            obj.scenario = run_full_flight_episode(
                int(np.random.SeedSequence([self.config["seed"], index, 2]).generate_state(1)[0]),
                settings=self.flight,
                object_id=kind,
                scenario_id=scenario_id,
            )
            source = obj.scenario
            start = 0
            if air:
                candidates = np.flatnonzero(source.position_enu_m[:, 2] >= position[2])
                if not len(candidates):
                    raise ValueError("Scenario never reaches requested airborne start altitude")
                start = int(candidates[0])
                obj.scenario_offset_s = float(source.t_s[start])
            moving = np.flatnonzero(np.linalg.norm(source.velocity_enu_mps[:, :2], axis=1) > 0.01)
            first = int(moving[0]) if len(moving) else 0
            vel = source.velocity_enu_mps[first]
            obj.scenario_rotation = heading - math.atan2(vel[1], vel[0])
            obj.scenario_origin = position - self._rotate(source.position_enu_m[start], obj)
            # Preserve the ground plane when entering a precomputed airborne scenario.
            obj.scenario_origin[2] = 0
            self._scenario_step(obj, 0)
            obj.initial["initial_mode"] = obj.control_mode
            obj.initial["initial_speed_mps"] = float(np.linalg.norm(obj.velocity[:2]))
            if air:
                obj.notice = "공중 시나리오는 지정 고도의 시나리오 상태에서 시작합니다."
        return obj

    def _event(self, action, **values):
        self.events.append(
            {
                "sequence": len(self.events),
                "step": self.step,
                "t_s": self.time_s,
                "action": action,
                **values,
            }
        )

    def start(self):
        if self.status != "ready":
            raise ValueError("Start requires a ready simulation")
        self.status = "running"
        self._event("start")
        if not self._initialized:
            self._observe()
            self._initialized = True

    def pause(self):
        self.keys.clear()
        if self.status == "running":
            self.status = "paused"
            self._event("pause")

    def resume(self):
        if self.status != "paused":
            raise ValueError("Resume requires paused state")
        self.keys.clear()
        self.status = "running"
        self._event("resume")

    def stop(self, reason="user"):
        self.keys.clear()
        self.status = "completed" if reason in ("all_finished", "duration_limit") else "stopped"
        self.notice = reason
        self._event("stop", reason=reason)

    def select(self, object_id):
        chosen = next((obj for obj in self.objects if obj.id == object_id), None)
        if chosen is None or chosen.terminal:
            raise ValueError("Select a live aircraft")
        self.keys.clear()
        if self.config["mode"] == "manual" and object_id != self.selected_id:
            for obj in self.objects:
                if obj.pilot == "manual":
                    obj.pilot = "hold"
                    obj.target_speed = float(np.linalg.norm(obj.velocity[:2]))
                    obj.power = self.control["hover_power"]
            chosen.pilot = "manual"
            chosen.target_speed = float(np.linalg.norm(chosen.velocity[:2]))
            if chosen.control_mode == "VTOL":
                max_climb = self._max_vertical(chosen)
                chosen.power = float(
                    np.clip(
                        self.control["hover_power"] + chosen.velocity[2] / (2 * max_climb), 0, 1
                    )
                )
        self.selected_id = object_id
        self._event("select", object_id=object_id)

    def set_keys(self, keys):
        if not isinstance(keys, list) or any(key not in self.control["keys"] for key in keys):
            raise ValueError("Unknown simulation key")
        new = set(keys)
        if self.status != "running" or self.config["mode"] != "manual":
            new = set()
        rising_t = "KeyT" in new and "KeyT" not in self.keys
        if new != self.keys:
            self.keys = new
            self._event("keys", object_id=self.selected_id, keys=sorted(new))
        if rising_t:
            obj = next(x for x in self.objects if x.id == self.selected_id)
            self._transition(obj)

    def _transition(self, obj):
        if obj.kind != "vtol" or obj.terminal:
            return
        if obj.position[2] <= 0 or obj.transition_start is not None:
            obj.notice = "지상 또는 천이 중에는 천이 명령을 받을 수 없습니다."
            return
        obj.control_mode = "CTOL" if obj.control_mode == "VTOL" else "VTOL"
        obj.transition_start = self.time_s
        profile = self.flight["objects"][obj.kind]
        obj.transition_duration = (
            profile["outbound_transition_s"]
            if obj.control_mode == "CTOL"
            else profile["inbound_transition_s"]
        )
        obj.transition_progress = 0
        obj.phase = "transition_to_ctol" if obj.control_mode == "CTOL" else "transition_to_vtol"
        obj.target_speed = (
            max(float(np.linalg.norm(obj.velocity[:2])), profile["transition_speed_mps"])
            if obj.control_mode == "CTOL"
            else float(np.linalg.norm(obj.velocity[:2]))
        )
        obj.power = float(
            np.clip(
                self.control["hover_power"] + obj.velocity[2] / (2 * self._max_vertical(obj)), 0, 1
            )
        )
        obj.notice = "고정익 조종" if obj.control_mode == "CTOL" else "헬기 조종"
        self._event("transition", object_id=obj.id, target_mode=obj.control_mode)

    def _max_vertical(self, obj):
        profile = self.flight["objects"][obj.kind]
        return profile.get(
            "vertical_speed_mps",
            profile.get(
                "takeoff_vertical_speed_mps",
                profile["horizontal_speed_mps"]
                * math.sin(math.radians(profile.get("max_angle_deg", 10))),
            ),
        )

    def advance(self, steps=1):
        if type(steps) is not int or steps < 0:
            raise ValueError("steps must be a nonnegative integer")
        for _ in range(steps):
            if self.status != "running":
                break
            self.step += 1
            for obj in self.objects:
                if obj.terminal:
                    continue
                if obj.pilot == "scenario":
                    self._scenario_step(obj, self.time_s)
                else:
                    self._manual_step(obj)
            if self.step % self._observation_stride == 0:
                self._observe()
            if all(obj.terminal for obj in self.objects):
                self.stop("all_finished")
            elif self.time_s >= self.flight["max_duration_s"]:
                self.stop("duration_limit")

    @staticmethod
    def _rotate(vector, obj):
        c, s = math.cos(obj.scenario_rotation), math.sin(obj.scenario_rotation)
        return np.array([c * vector[0] - s * vector[1], s * vector[0] + c * vector[1], vector[2]])

    def _scenario_step(self, obj, time_s):
        source = obj.scenario
        at = min(time_s + obj.scenario_offset_s, float(source.t_s[-1]))
        pos = np.array(
            [np.interp(at, source.t_s, source.position_enu_m[:, axis]) for axis in range(3)]
        )
        vel = np.array(
            [np.interp(at, source.t_s, source.velocity_enu_mps[:, axis]) for axis in range(3)]
        )
        obj.position = self._rotate(pos, obj) + obj.scenario_origin
        obj.velocity = self._rotate(vel, obj)
        if np.linalg.norm(obj.velocity[:2]) > 1e-8:
            obj.heading = math.atan2(obj.velocity[1], obj.velocity[0])
        phase = next(
            (event.event_id for event in source.event_records if event.start_s <= at < event.end_s),
            "ground",
        )
        obj.phase = phase
        if obj.kind == "vtol":
            obj.control_mode = "VTOL"
            obj.transition_start = None
            for event in source.event_records:
                if event.start_s > at:
                    continue
                if event.event_id == "transition_out":
                    obj.control_mode = "CTOL"
                elif event.event_id == "transition_in":
                    obj.control_mode = "VTOL"
                else:
                    continue
                if at < event.end_s:
                    obj.transition_start = event.start_s - obj.scenario_offset_s
                    obj.transition_duration = event.end_s - event.start_s
                    obj.transition_progress = (at - event.start_s) / obj.transition_duration
                else:
                    obj.transition_start = None
                    obj.transition_progress = 1
        obj.has_flown = obj.has_flown or obj.position[2] > 0
        if time_s and at >= source.t_s[-1]:
            obj.phase = "landed"
            obj.terminal = True

    def _manual_step(self, obj):
        dt = self.settings["dt_s"]
        profile = self.flight["objects"][obj.kind]
        keys = self.keys if obj.id == self.selected_id and obj.pilot == "manual" else set()
        def axis(positive, negative):
            return int(positive in keys) - int(negative in keys)
        airborne = obj.position[2] > 0
        old_velocity = obj.velocity.copy()
        desired = old_velocity.copy()
        pitch_target = roll_target = 0.0
        if obj.control_mode == "CTOL":
            minimum = (
                profile["minimum_airborne_horizontal_speed_mps"]
                if airborne and obj.transition_start is None
                else 0
            )
            obj.target_speed = float(
                np.clip(
                    obj.target_speed
                    + axis("KeyW", "KeyS") * self.flight["tangential_acceleration_mps2"] * dt,
                    minimum,
                    profile["horizontal_speed_mps"],
                )
            )
            turn = axis("ArrowLeft", "ArrowRight") * profile["turn_rate_rad_s"]
            obj.yaw_rate = float(
                _toward(
                    obj.yaw_rate,
                    turn,
                    profile["turn_rate_rad_s"] / self.control["attitude_response_s"],
                    dt,
                )
            )
            obj.heading += obj.yaw_rate * dt
            desired[:2] = obj.target_speed * np.array(
                [math.cos(obj.heading), math.sin(obj.heading)]
            )
            desired[2] = axis("ArrowUp", "ArrowDown") * self._max_vertical(obj)
            if (
                not airborne
                and np.linalg.norm(old_velocity[:2])
                < profile["minimum_airborne_horizontal_speed_mps"]
            ):
                desired[2] = 0
                if "ArrowUp" in keys:
                    obj.notice = "이륙 가능 속도까지 W로 가속하세요."
            roll_target = -axis("ArrowLeft", "ArrowRight") * self.control["rotor_roll_display_rad"]
            pitch_target = axis("ArrowUp", "ArrowDown") * self.control["rotor_pitch_display_rad"]
        else:
            obj.power = float(
                np.clip(
                    obj.power + axis("KeyW", "KeyS") * self.control["power_rate_per_s"] * dt, 0, 1
                )
            )
            yaw_target = axis("KeyA", "KeyD") * self.control["rotor_yaw_rate_rad_s"]
            obj.yaw_rate = float(
                _toward(
                    obj.yaw_rate,
                    yaw_target,
                    self.control["rotor_yaw_rate_rad_s"] / self.control["attitude_response_s"],
                    dt,
                )
            )
            obj.heading += obj.yaw_rate * dt
            forward = np.array([math.cos(obj.heading), math.sin(obj.heading)])
            left = np.array([-math.sin(obj.heading), math.cos(obj.heading)])
            command = (
                axis("ArrowUp", "ArrowDown") * forward + axis("ArrowLeft", "ArrowRight") * left
            )
            norm = np.linalg.norm(command)
            if norm > 1:
                command /= norm
            desired[:2] += command * self.flight["horizontal_acceleration_mps2"] * dt
            if obj.transition_start is not None:
                desired[:2] = command * profile["horizontal_speed_mps"]
            horizontal_speed = np.linalg.norm(desired[:2])
            if horizontal_speed > profile["horizontal_speed_mps"]:
                desired[:2] *= profile["horizontal_speed_mps"] / horizontal_speed
            desired[2] = (obj.power - self.control["hover_power"]) * 2 * self._max_vertical(obj)
            pitch_target = -axis("ArrowUp", "ArrowDown") * self.control["rotor_pitch_display_rad"]
            roll_target = -axis("ArrowLeft", "ArrowRight") * self.control["rotor_roll_display_rad"]
        if obj.pilot == "hold":
            desired[2] = 0
        horizontal_delta = desired[:2] - old_velocity[:2]
        bound = self.flight["horizontal_acceleration_mps2"] * dt
        length = float(np.linalg.norm(horizontal_delta))
        if length > bound:
            horizontal_delta *= bound / length
        obj.velocity[:2] += horizontal_delta
        obj.velocity[2] = _toward(
            old_velocity[2], desired[2], self.flight["vertical_acceleration_mps2"], dt
        )
        if not airborne and obj.velocity[2] < 0:
            obj.velocity[2] = 0
        new_position = obj.position + (old_velocity + obj.velocity) * 0.5 * dt
        if not airborne:
            new_position[2] = max(0, new_position[2])
        if airborne and new_position[2] <= 0:
            fraction = obj.position[2] / max(obj.position[2] - new_position[2], 1e-12)
            impact = old_velocity + fraction * (obj.velocity - old_velocity)
            contact_kind = "fixed_wing" if obj.control_mode == "CTOL" else obj.kind
            limits = self.settings["contact"][contact_kind]
            landed = (
                abs(impact[2]) <= limits["vertical_speed_max_mps"]
                and np.linalg.norm(impact[:2]) <= limits["horizontal_speed_max_mps"]
            )
            obj.phase = "landed" if landed else "crashed"
            new_position = obj.position + fraction * (new_position - obj.position)
            new_position[2] = 0
            obj.velocity[2] = 0
            if not landed:
                obj.terminal = True
                obj.velocity[:] = 0
                obj.notice = "접지 속도 기준 초과 · 충돌 종료"
            self._event(obj.phase, object_id=obj.id, impact_velocity_enu_m=impact.tolist())
        obj.position = new_position
        obj.pitch = float(
            _toward(
                obj.pitch,
                pitch_target,
                self.control["rotor_pitch_display_rad"] / self.control["attitude_response_s"],
                dt,
            )
        )
        obj.roll = float(
            _toward(
                obj.roll,
                roll_target,
                self.control["rotor_roll_display_rad"] / self.control["attitude_response_s"],
                dt,
            )
        )
        obj.heading = (obj.heading + math.pi) % (2 * math.pi) - math.pi
        if obj.position[2] > self.flight["max_altitude_m"]:
            obj.phase, obj.terminal, obj.notice = "limit_reached", True, "고도 한도 도달"
        elif not obj.terminal and obj.position[2] > 0:
            obj.has_flown = True
            if obj.transition_start is None:
                obj.phase = (
                    "climb"
                    if obj.velocity[2] > 0.1
                    else "descent"
                    if obj.velocity[2] < -0.1
                    else "cruise"
                    if obj.control_mode == "CTOL"
                    else "hover"
                    if np.linalg.norm(obj.velocity[:2]) < 0.1
                    else "flight"
                )
        elif not obj.terminal and obj.phase != "landed":
            obj.phase = "ground"
        if obj.transition_start is not None and not obj.terminal:
            elapsed = self.time_s - obj.transition_start
            obj.transition_progress = min(1, elapsed / obj.transition_duration)
            ready = (
                obj.control_mode == "VTOL"
                or np.linalg.norm(obj.velocity[:2])
                >= profile["minimum_airborne_horizontal_speed_mps"]
                - self.control["transition_speed_tolerance_mps"]
            )
            if elapsed >= obj.transition_duration and ready:
                obj.transition_start = None
                obj.phase = "cruise" if obj.control_mode == "CTOL" else "flight"
                self._event("transition_complete", object_id=obj.id, mode=obj.control_mode)
            elif elapsed >= obj.transition_duration * self.control["transition_timeout_factor"]:
                obj.phase, obj.terminal, obj.notice = "failed", True, "천이 목표 속도 도달 실패"
        assert np.all(np.isfinite(obj.position)) and np.all(np.isfinite(obj.velocity))
        assert obj.position[2] >= 0

    def _observe(self):
        for obj in self.objects:
            if obj.terminal:
                continue
            position = obj.position + obj.rng.normal(0, self.config["sigma_m"], 3)
            self._observations.append(
                {
                    "object_id": obj.id,
                    "t_s": self.time_s,
                    "position_enu_m": position.tolist(),
                    "sigma_m": self.config["sigma_m"],
                    "valid": True,
                    "truth_position_enu_m": obj.position.tolist(),
                    "phase": obj.phase,
                }
            )
            obj.history.append(position.tolist())
            del obj.history[: -self.settings["history_display_samples"]]

    def drain_observations(self):
        result, self._observations = self._observations, []
        return result

    def snapshot(self):
        return {
            "status": self.status,
            "time_s": self.time_s,
            "step": self.step,
            "selected_id": self.selected_id,
            "notice": self.notice,
            "config": copy.deepcopy(self.config),
            "objects": [
                {
                    "id": obj.id,
                    "kind": obj.kind,
                    "position_enu_m": obj.position.tolist(),
                    "velocity_enu_m": obj.velocity.tolist(),
                    "heading_rad": obj.heading,
                    "pitch_rad": obj.pitch,
                    "roll_rad": obj.roll,
                    "phase": obj.phase,
                    "pilot": obj.pilot,
                    "control_mode": obj.control_mode,
                    "transition_progress": obj.transition_progress,
                    "power": obj.power,
                    "speed_mps": float(np.linalg.norm(obj.velocity[:2])),
                    "altitude_m": float(obj.position[2]),
                    "notice": obj.notice,
                    "terminal": obj.terminal,
                    "history": copy.deepcopy(obj.history),
                    "prediction": None,
                }
                for obj in self.objects
            ],
        }


def replay_events(config, events, until_step, settings=None):
    """Replay only externally applied commands at their recorded integer steps."""
    engine = SimulationEngine(config, settings=settings)
    for event in events:
        target = event["step"]
        if target > until_step:
            break
        if target < engine.step:
            raise ValueError("Input events are not ordered")
        if target > engine.step:
            engine.advance(target - engine.step)
            if engine.step != target:
                raise ValueError("Cannot advance paused or terminated replay to event")
        action = event["action"]
        if action in ("start", "pause", "resume"):
            getattr(engine, action)()
        elif action == "keys":
            engine.set_keys(event["keys"])
        elif action == "select":
            engine.select(event["object_id"])
        elif action == "stop" and engine.status in ("running", "paused", "ready"):
            engine.stop(event["reason"])
    if until_step > engine.step:
        engine.advance(until_step - engine.step)
    return engine
