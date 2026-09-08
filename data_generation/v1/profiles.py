"""Load an explicitly selected model profile and verify its local source evidence."""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

from omegaconf import OmegaConf

from .bounded_motion import validate_bounded_motion_profile
from .motion_reference_analysis import (
    MotionReferenceError,
    load_motion_reference_config,
)
from .motion_reference_metadata import validate_source_grounded_metadata
from .point_mass_profile import validate_point_mass_profile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = Path(__file__).resolve().parent / "configs" / "profiles"


def freeze_motion_reference_profile(profile: dict) -> dict:
    """Freeze one verified source-grounded config into a generation-local profile snapshot.

    The dataset writer persists this returned mapping in ``effective_model_config.yaml``.
    Its resolved external values and source-config digest are then the only settings
    passed to every episode in that generation.
    """
    if not isinstance(profile, dict):
        raise ValueError("profile must be a mapping before motion-reference freezing")
    frozen_profile = copy.deepcopy(profile)
    motion_reference = frozen_profile.get("motion_reference")
    if motion_reference is None:
        return frozen_profile
    if not isinstance(motion_reference, dict):
        raise ValueError("quadrotor motion_reference must be a mapping")
    mode = motion_reference.get("mode")
    if mode == "legacy_waypoints":
        return frozen_profile
    if mode != "source_grounded":
        raise ValueError("quadrotor motion_reference mode is unsupported")
    if "resolved_config" in motion_reference:
        raise ValueError("profile motion_reference must not predefine resolved_config")
    config_path = motion_reference.get("config_path")
    if not isinstance(config_path, str) or not config_path:
        raise ValueError("source-grounded motion_reference requires config_path")
    try:
        config = load_motion_reference_config(Path(config_path))
    except MotionReferenceError as error:
        raise ValueError("source-grounded motion reference config is invalid") from error
    validate_source_grounded_metadata(motion_reference, config.values)
    try:
        relative_config_path = config.path.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ValueError("source-grounded motion reference config escapes project root") from error
    motion_reference["resolved_config"] = {
        "config_path": relative_config_path.as_posix(),
        "config_sha256": config.sha256,
        "values": copy.deepcopy(config.values),
    }
    return frozen_profile


def load_generation_profile(profile_id: str) -> dict:
    """Reject unknown profiles, mismatched identities, and missing or modified sources."""
    if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", profile_id):
        raise ValueError("profile must be an explicit configuration identifier")
    path = PROFILE_ROOT / f"{profile_id}.yaml"
    if not path.is_file():
        raise ValueError(f"unsupported generation profile: {profile_id}")
    profile = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(profile, dict) or profile.get("model_id") != profile_id:
        raise ValueError("profile model_id does not match its explicit filename")
    for field in ("object_id", "identity_label", "source_id", "input_id", "evidence_status"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise ValueError(f"profile requires a nonempty {field}")
    engine = profile.get("engine")
    if engine is None:
        if profile["object_id"] != "quadrotor":
            raise ValueError(
                "profiles without an engine support only the published quadrotor model"
            )
        required_model_config = "physics"
    elif engine == "bounded_motion":
        if profile["object_id"] not in {"vtol", "helicopter"}:
            raise ValueError("bounded_motion engine supports only VTOL or helicopter profiles")
        if "physics" in profile:
            raise ValueError("bounded_motion profiles use motion, not physics")
        required_model_config = "motion"
    elif engine == "point_mass":
        if profile["object_id"] not in {"x8_fixed_wing", "quadrotor"}:
            raise ValueError("point_mass engine supports only fixed-wing or quadrotor profiles")
        if "physics" in profile:
            raise ValueError("point_mass profiles use motion, not physics")
        required_model_config = "motion"
    elif engine == "full_flight":
        from .full_flight_config import load_settings

        if profile["object_id"] not in {"fixed_wing", "helicopter", "vtol"}:
            raise ValueError("unsupported full-flight object")
        profile["motion"] = load_settings()
        required_model_config = "motion"
    else:
        raise ValueError(f"unsupported generation profile engine: {engine}")
    required_sections = (required_model_config,) if engine in {"point_mass", "full_flight"} else (
        required_model_config,
        "experiment",
    )
    for field in required_sections:
        if not isinstance(profile.get(field), dict) or not profile[field]:
            raise ValueError(f"profile requires {field} configuration")
    if not isinstance(profile.get("limitations"), list) or not profile["limitations"]:
        raise ValueError("profile requires explicit limitations")
    if engine in {"bounded_motion", "point_mass"}:
        if not isinstance(profile.get("metadata"), dict) or not profile["metadata"]:
            raise ValueError(f"{engine} profile requires metadata")
        if not isinstance(profile.get("source_evidence"), list) or not profile["source_evidence"]:
            raise ValueError(f"{engine} profile requires separate source_evidence")
    sources = profile.get("source_files")
    if not isinstance(sources, list) or not sources:
        raise ValueError("profile requires local source_files")
    source_root = (PROJECT_ROOT / "data_sources" / profile["source_id"]).resolve()
    if not source_root.is_relative_to((PROJECT_ROOT / "data_sources").resolve()):
        raise ValueError("profile source_id escapes data_sources")
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "sha256", "url"}:
            raise ValueError("invalid profile source record")
        if not all(isinstance(value, str) and value for value in source.values()):
            raise ValueError("source path, hash, and URL must be nonempty strings")
        source_path = (PROJECT_ROOT / source["path"]).resolve()
        if not source_path.is_relative_to(source_root) or source_path in seen:
            raise ValueError("source path must be unique and inside the declared source folder")
        seen.add(source_path)
        if not source_path.is_file():
            raise ValueError(f"source file missing: {source['path']}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", source["sha256"]):
            raise ValueError("invalid source SHA-256 hash")
        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual != source["sha256"].lower():
            raise ValueError(f"source hash mismatch: {source['path']}")
    if engine == "bounded_motion":
        validate_bounded_motion_profile(profile)
    if engine == "point_mass":
        validate_point_mass_profile(profile)
    return profile
