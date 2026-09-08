"""Explicit scenario catalogs and experimental settings for full flights."""

import math
from pathlib import Path

from omegaconf import OmegaConf

SETTINGS_PATH = Path(__file__).parent / "configs" / "full_flight_settings.yaml"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """Load a named configuration; never discover a latest version."""
    settings = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    validate_settings(settings)
    return settings


def validate_settings(settings: dict) -> None:
    """Check budgets before allocating or publishing any trajectory."""
    if not isinstance(settings, dict) or settings.get("schema") != "full-flight-settings-v1":
        raise ValueError("unsupported full-flight settings schema")
    keys = (
        "dt_s",
        "output_dt_s",
        "max_duration_s",
        "max_altitude_m",
        "horizontal_acceleration_mps2",
        "tangential_acceleration_mps2",
        "vertical_acceleration_mps2",
        "jerk_mps3",
        "transition_min_s",
        "numeric_tolerance",
        "altitude_tolerance_m",
        "ground_hold_s",
        "hover_hold_s",
        "cruise_hold_s",
    )
    for key in keys:
        v = settings.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"invalid positive setting: {key}")
    if settings["max_duration_s"] > 600 or settings["max_altitude_m"] > 1000:
        raise ValueError("full-flight duration/altitude exceeds agreed budget")
    if settings["output_dt_s"] != 0.2 or settings["dt_s"] > 0.02:
        raise ValueError("full-flight output must be 5 Hz with integration dt <=0.02")
    if not 0 < settings["turn_ramp_fraction"] < 0.5 or settings["stretch_factor"] <= 1:
        raise ValueError("invalid ramp/stretch setting")
    if type(settings["max_attempts"]) is not int or not 1 <= settings["max_attempts"] <= 10:
        raise ValueError("invalid attempt budget")
    low, high = settings["speed_scale_range"]
    if not 0 < low <= high <= 1:
        raise ValueError("invalid speed scale range")
    for object_id, obj in settings["objects"].items():
        if object_id not in {"fixed_wing", "helicopter", "vtol"}:
            raise ValueError("unsupported full-flight object")
        allowed = {
            "straight",
            "turn",
            "s_turn",
            "spiral",
            "vertical",
            "hover",
            "orbit",
            "stop_restart",
        }
        for phase in ("climb", "cruise", "descent"):
            choices = obj.get(phase)
            if (
                not isinstance(choices, list)
                or not choices
                or len(set(choices)) != len(choices)
                or any(name not in allowed or name not in settings["labels"] for name in choices)
            ):
                raise ValueError("unsupported or duplicate maneuver")
        if (
            object_id != "helicopter"
            and obj["horizontal_speed_mps"] * low < obj["minimum_airborne_horizontal_speed_mps"]
        ):
            raise ValueError("configured speed range falls below airborne minimum")
        if (
            object_id == "vtol"
            and obj["transition_speed_mps"] < obj["minimum_airborne_horizontal_speed_mps"]
        ):
            raise ValueError("transition end speed falls below airborne minimum")
        for key, value in obj.items():
            if key.endswith(("_m", "_mps", "_rad_s", "_deg", "_s")):
                if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"invalid object setting {object_id}.{key}")
        if not 0 < obj["approach_altitude_m"] < obj["altitude_m"] <= settings["max_altitude_m"]:
            raise ValueError("invalid flight altitude ordering")


def catalog(settings: dict, object_id: str) -> dict[str, dict]:
    """Map stable F/H/V identifiers to exactly one maneuver per flight phase."""
    if object_id not in settings["objects"]:
        raise ValueError("unsupported full-flight object")
    obj = settings["objects"][object_id]
    entries = {}
    for i, climb in enumerate(obj["climb"]):
        for j, descent in enumerate(obj["descent"]):
            for k, cruise in enumerate(obj["cruise"]):
                key = f"{obj['prefix']}{i * len(obj['descent']) + j + 1:02d}-{chr(65 + k)}"
                label = " · ".join(
                    [
                        obj["label"],
                        settings["labels"][climb] + " 상승",
                        settings["labels"][cruise],
                        settings["labels"][descent] + " 하강",
                    ]
                )
                entries[key] = dict(climb=climb, cruise=cruise, descent=descent, label=label)
    return entries
