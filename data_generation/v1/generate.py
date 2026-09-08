"""Hydra entry point for explicit object-specific v1 dataset generation."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from numbers import Integral
from pathlib import Path

import hydra
import numpy as np
from omegaconf import DictConfig, OmegaConf

from . import dataset as dataset_module
from .bounded_motion import run_bounded_motion_episode
from .dataset import DatasetWriteResult, GenerationRequest, write_dataset
from .motion_check import run_training_motion_check
from .point_mass_scenario import run_point_mass_episode
from .profiles import freeze_motion_reference_profile, load_generation_profile
from .records import SourceFileRecord
from .scenario import run_default_x8_episode

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def corpus_schedule(config: DictConfig) -> list[tuple[str, int]]:
    """Enumerate all configured maneuvers with independent deterministic child seeds."""
    from .full_flight_config import catalog

    profile = load_generation_profile(config.profile_id)
    if profile.get("engine") != "full_flight":
        raise ValueError("corpus requires a full-flight profile")
    resolved = OmegaConf.to_container(config, resolve=True)
    repeats = _integer_setting(resolved, "repeats_per_scenario", 1, 10)
    seed = _integer_setting(resolved, "seed", 0, int(np.iinfo(np.uint32).max))
    scenarios = list(catalog(profile["motion"], profile["object_id"])) * repeats
    children = np.random.SeedSequence(seed).spawn(len(scenarios))
    return [
        (scenario, int(child.generate_state(1, dtype=np.uint32)[0]))
        for scenario, child in zip(scenarios, children, strict=True)
    ]


def configured_episode_count(config: DictConfig) -> int:
    """UI and worker derive the same batch size from the selected catalog."""
    return len(corpus_schedule(config)) if config.mode == "corpus" else int(config.episode_count)


def _integer_setting(config: dict, name: str, minimum: int, maximum: int) -> int:
    """Reject truncation, boolean seeds, and values outside the supported range."""
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(value)


def generate_from_config(
    config: DictConfig,
    *,
    output_root: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> DatasetWriteResult:
    """Generate a diagnostic or pilot corpus from an explicit resolved configuration."""
    resolved = OmegaConf.to_container(config, resolve=True)
    if not isinstance(resolved, dict):
        raise ValueError("generation configuration must be a mapping")
    mode = str(resolved["mode"])
    schedule = corpus_schedule(config) if mode == "corpus" else None
    episode_count = (
        len(schedule) if schedule else _integer_setting(resolved, "episode_count", 1, 100)
    )
    if mode == "diagnostic" and episode_count != 1:
        raise ValueError("diagnostic mode must generate exactly one episode")
    if mode == "pilot" and episode_count != 100:
        raise ValueError("pilot mode must generate exactly 100 episodes")
    master_seed = _integer_setting(resolved, "seed", 0, int(np.iinfo(np.uint32).max))
    source_check = resolved.get("source_motion_check_status")
    training_config = resolved.get("training")
    if training_config is not None:
        from .training_data import validate_training_config

        validate_training_config(training_config)
    if source_check not in {"NOT_RUN", "REPORT_ONLY"}:
        raise ValueError("source_motion_check_status must be NOT_RUN or REPORT_ONLY")
    code_hash_at_start = dataset_module._code_hash()
    if "profile_id" in resolved:
        profile = freeze_motion_reference_profile(load_generation_profile(resolved["profile_id"]))
        if resolved.get("identity_label", profile["identity_label"]) != profile["identity_label"]:
            raise ValueError("identity_label must match the explicitly selected profile")
        request = GenerationRequest(
            master_seed=master_seed,
            mode=mode,
            source_motion_check_status=source_check,
            identity_label=profile["identity_label"],
            object_id=profile["object_id"],
            model_id=profile["model_id"],
            source_id=profile["source_id"],
            input_id=profile["input_id"],
            source_files=tuple(SourceFileRecord(**source) for source in profile["source_files"]),
            model_config={"profile": profile, "run": resolved},
            code_hash_at_start=code_hash_at_start,
        )
        if profile.get("engine") == "full_flight":
            from .full_flight import run_full_flight_episode

            simulate_episode = partial(
                run_full_flight_episode,
                settings=profile["motion"],
                object_id=profile["object_id"],
                scenario_id=resolved.get("scenario_id", profile.get("default_scenario_id")),
            )
        elif profile.get("engine") == "point_mass":
            simulate_episode = partial(run_point_mass_episode, profile=profile)
        elif profile.get("engine") == "bounded_motion":
            simulate_episode = partial(run_bounded_motion_episode, profile=profile)
        elif profile["object_id"] == "quadrotor":
            from .quadrotor_scenario import run_quadrotor_episode

            simulate_episode = partial(run_quadrotor_episode, profile=profile)
        else:
            raise ValueError(f"unsupported generation profile object: {profile['object_id']}")
    else:
        request = GenerationRequest(
            master_seed=master_seed,
            mode=mode,
            source_motion_check_status=source_check,
            code_hash_at_start=code_hash_at_start,
        )
        if resolved.get("identity_label", request.identity_label) != request.identity_label:
            raise ValueError("identity_label must match the canonical X8 generation identity")
        simulate_episode = run_default_x8_episode
    child_sequences = np.random.SeedSequence(master_seed).spawn(episode_count)
    episodes = []
    if progress is not None:
        progress(0, episode_count, "simulating")
    for index, child_sequence in enumerate(child_sequences):
        if schedule is not None:
            scenario_id, child_seed = schedule[index]
            episodes.append(simulate_episode(child_seed, scenario_id=scenario_id))
        else:
            episodes.append(
                simulate_episode(int(child_sequence.generate_state(1, dtype=np.uint32)[0]))
            )
        if progress is not None:
            progress(len(episodes), episode_count, "simulating")
    destination = PROJECT_ROOT / "outputs" if output_root is None else output_root
    source_report = None
    if request.source_motion_check_status == "REPORT_ONLY":
        if progress is not None:
            progress(len(episodes), episode_count, "source_check")
        source_report = run_training_motion_check(
            PROJECT_ROOT / "data_sources" / "x8_dataverse_2024" / "raw"
        )
    if progress is not None:
        progress(len(episodes), episode_count, "writing")
    return write_dataset(
        episodes,
        request=request,
        output_root=destination,
        source_motion_check_report=source_report,
        training_config=training_config,
    )


@hydra.main(version_base=None, config_path="configs", config_name="diagnostic")
def main(config: DictConfig) -> None:
    """Run the selected config; Hydra accepts explicit key=value overrides only."""
    result = generate_from_config(config)
    print(f"dataset_id={result.dataset_id}")
    print(f"status={result.status}")
    print(f"path={result.path}")
