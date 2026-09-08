"""Observation synthesis and durable X8-GEN-V1 dataset writing."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import math
import platform
import re
import sys
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf

from contracts.v1.validation import (
    MAX_SAMPLE_COUNT,
    MIN_SAMPLE_COUNT,
    OUTPUT_DT_S,
    PUBLIC_EPISODE_COLUMNS,
    PUBLIC_OBSERVATION_COLUMNS,
    SAMPLE_COUNT,
    TRUTH_COLUMNS,
    VARIANT_SIGMAS,
)

from .motion_check import MotionCheckReport
from .records import MotionEpisode, MotionEvent, SourceFileRecord
from .scenario import EventRecord, GeneratedEpisode
from .source import X8_DATASET_DOI, X8_SOURCE_ID

NOISE_STREAM_IDS = {"sigma_1m": 11, "sigma_3m": 13, "sigma_5m": 15}
CONTRACT_VERSION = "v1"
GENERATOR_VERSION = "v1"
X8_OBJECT_ID = "x8_fixed_wing"
X8_MODEL_ID = "x8_nonlinear_6dof_2025"
X8_INPUT_ID = f"doi:{X8_DATASET_DOI}"

_EVENT_COLUMNS = (
    "episode_id",
    "event_id",
    "start_s",
    "end_s",
    "trigger_kind",
    "completed",
    "completion_reason",
)
_X8_DIAGNOSTIC_COLUMNS = (
    "episode_id",
    "step",
    "t_s",
    "airspeed_mps",
    "alpha_rad",
    "beta_rad",
    "roll_rad",
    "pitch_rad",
    "yaw_rad",
    "left_elevon_rad",
    "right_elevon_rad",
    "throttle",
)
_GENERIC_DIAGNOSTIC_PREFIX = ("episode_id", "step", "t_s")
_PROVENANCE_COLUMNS = ("path", "sha256", "url")
_PROVENANCE_PATH = "provenance.csv"
_MODEL_CONFIG_PATH = "effective_model_config.yaml"

assert set(NOISE_STREAM_IDS) == set(VARIANT_SIGMAS)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Explicit configuration recorded beside every generated dataset."""

    master_seed: int
    mode: str
    source_motion_check_status: str
    identity_label: str = "X8 고정익 UAV"
    object_id: str = X8_OBJECT_ID
    model_id: str = X8_MODEL_ID
    source_id: str = X8_SOURCE_ID
    input_id: str = X8_INPUT_ID
    source_files: tuple[SourceFileRecord, ...] = ()
    model_config: Mapping[str, object] = field(default_factory=dict)
    code_hash_at_start: str | None = None

    def __post_init__(self) -> None:
        _validate_generation_request(self)


@dataclass(frozen=True, slots=True)
class DatasetWriteResult:
    """A newly allocated output location and its completion state."""

    dataset_id: str
    path: Path
    status: str


MotionRecord = GeneratedEpisode | MotionEpisode


def _validate_source_file(source_file: SourceFileRecord) -> None:
    """Check declared provenance syntax without reading or hashing external files."""
    if not isinstance(source_file, SourceFileRecord):
        raise ValueError("source_files must contain SourceFileRecord values")
    if not all(
        isinstance(value, str) and value
        for value in (source_file.path, source_file.sha256, source_file.url)
    ):
        raise ValueError("source file path, sha256, and url must be non-empty")
    if len(source_file.sha256) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in source_file.sha256
    ):
        raise ValueError("source file sha256 must be a 64-character hexadecimal digest")


def _validate_generation_request(request: GenerationRequest) -> None:
    """Validate live request metadata before construction and before publication."""
    if request.mode not in {"diagnostic", "pilot", "corpus"}:
        raise ValueError("mode must be diagnostic, pilot or corpus")
    if not isinstance(request.identity_label, str) or not request.identity_label:
        raise ValueError("identity_label must be non-empty")
    if not all(
        isinstance(value, str) and value
        for value in (request.object_id, request.model_id, request.source_id, request.input_id)
    ):
        raise ValueError("object, model, source, and input identifiers must be non-empty")
    if not isinstance(request.source_files, tuple):
        raise ValueError("source_files must be a concrete tuple")
    if not isinstance(request.model_config, Mapping):
        raise ValueError("model_config must be a mapping")
    if request.code_hash_at_start is not None and (
        not isinstance(request.code_hash_at_start, str)
        or len(request.code_hash_at_start) != 64
        or any(
            character not in "0123456789abcdefABCDEF" for character in request.code_hash_at_start
        )
    ):
        raise ValueError("code_hash_at_start must be a 64-character hexadecimal digest")
    for source_file in request.source_files:
        _validate_source_file(source_file)

    is_x8 = request.object_id == X8_OBJECT_ID
    if is_x8:
        if request.source_id != X8_SOURCE_ID or request.input_id != X8_INPUT_ID:
            raise ValueError("X8 requests must retain the X8 source and input identifiers")
        if request.model_id != X8_MODEL_ID:
            profile = request.model_config.get("profile")
            if (
                not request.source_files
                or not isinstance(profile, Mapping)
                or profile.get("engine") != "point_mass"
                or profile.get("object_id") != X8_OBJECT_ID
                or profile.get("model_id") != request.model_id
                or profile.get("source_id") != request.source_id
                or profile.get("input_id") != request.input_id
            ):
                raise ValueError(
                    "non-legacy X8 requests require an explicit point-mass profile and provenance"
                )
    else:
        if (
            request.model_id == X8_MODEL_ID
            or request.source_id == X8_SOURCE_ID
            or request.input_id == X8_INPUT_ID
        ):
            raise ValueError("non-X8 requests cannot reuse X8 model or source provenance")
        if not request.source_files:
            raise ValueError("non-X8 requests require explicit source_files provenance")
        if not request.model_config:
            raise ValueError("non-X8 requests require a resolved model_config")
        if request.source_motion_check_status != "NOT_RUN":
            raise ValueError("non-X8 requests cannot claim the X8 source motion check")


def _resolved_model_config(model_config: Mapping[str, object]) -> dict[str, object]:
    """Resolve a serializable model snapshot before an output directory is allocated."""
    resolved = OmegaConf.to_container(OmegaConf.create(dict(model_config)), resolve=True)
    if not isinstance(resolved, dict):
        raise ValueError("resolved model_config must be a mapping")
    return resolved


def _numeric_array(value: object, *, name: str) -> np.ndarray:
    """Convert only for validation, leaving the caller's record untouched."""
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be finite")
    return array


def _validate_event_values(
    record: MotionEvent | EventRecord,
    *,
    label: str,
    episode_end_s: float,
) -> None:
    """Check fields serialized into the unchanged evaluation event schema."""
    if not all(
        isinstance(value, str) and value
        for value in (record.event_id, record.trigger_kind, record.completion_reason)
    ):
        raise ValueError(f"{label} event string fields must be non-empty")
    if not isinstance(record.completed, bool):
        raise ValueError(f"{label} event completed must be a boolean")
    try:
        start_s = float(record.start_s)
        end_s = float(record.end_s)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} event times must be numeric") from error
    if not all(math.isfinite(value) for value in (start_s, end_s)):
        raise ValueError(f"{label} event times must be finite")
    if end_s < start_s:
        raise ValueError(f"{label} event end_s must not precede start_s")
    if start_s < 0.0 or end_s > episode_end_s:
        raise ValueError(f"{label} event times must stay within the episode window")


def _validate_common_motion_fields(
    episode: MotionRecord,
    *,
    label: str,
) -> tuple[int, np.ndarray]:
    """Reject malformed core arrays before a record can enter an output directory."""
    if not isinstance(episode.episode_id, str) or not episode.episode_id:
        raise ValueError(f"{label} episode_id must be non-empty")
    if isinstance(episode.seed, bool) or not isinstance(episode.seed, (int, np.integer)):
        raise ValueError(f"{label} episode seed must be an integer")
    if not isinstance(episode.status, str) or episode.status not in {
        "complete",
        "partial",
        "failed",
    }:
        raise ValueError(f"{label} episode status must be complete, partial, or failed")
    if episode.status == "complete" and episode.failure_reason is not None:
        raise ValueError(f"complete {label} episodes cannot carry a failure_reason")
    if episode.status != "complete" and (
        not isinstance(episode.failure_reason, str) or not episode.failure_reason
    ):
        raise ValueError(f"partial or failed {label} episodes require a failure_reason")

    steps = np.asarray(episode.steps)
    if steps.ndim != 1 or not np.issubdtype(steps.dtype, np.integer):
        raise ValueError(f"{label} steps must be a one-dimensional integer array")
    sample_count = len(steps)
    if not np.array_equal(steps, np.arange(sample_count, dtype=int)):
        raise ValueError(f"{label} steps must start at zero and be contiguous")
    time_s = _numeric_array(episode.t_s, name=f"{label} t_s")
    position = _numeric_array(episode.position_enu_m, name=f"{label} position_enu_m")
    velocity = _numeric_array(episode.velocity_enu_mps, name=f"{label} velocity_enu_mps")
    if time_s.shape != (sample_count,):
        raise ValueError(f"{label} t_s shape must match steps")
    if position.shape != (sample_count, 3) or velocity.shape != (sample_count, 3):
        raise ValueError(f"{label} position and velocity must have shape N×3")
    if not np.allclose(
        time_s,
        steps.astype(float) * OUTPUT_DT_S,
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError(f"{label} t_s must match the declared {OUTPUT_DT_S:g}-second step")
    if (
        isinstance(episode, MotionEpisode)
        and not MIN_SAMPLE_COUNT <= sample_count <= MAX_SAMPLE_COUNT
    ):
        raise ValueError(
            f"generic episodes must contain {MIN_SAMPLE_COUNT}..{MAX_SAMPLE_COUNT} samples"
        )
    if (
        isinstance(episode, GeneratedEpisode)
        and episode.status == "complete"
        and sample_count != SAMPLE_COUNT
    ):
        raise ValueError(f"complete {label} episodes must contain 301 samples")
    return sample_count, time_s


def _validate_motion_episode(episode: MotionEpisode) -> None:
    """Validate a generic record before it can create an output directory."""
    sample_count, time_s = _validate_common_motion_fields(episode, label="generic")

    if not isinstance(episode.diagnostic_arrays, Mapping):
        raise ValueError("generic diagnostic_arrays must be a mapping")
    for name, values in episode.diagnostic_arrays.items():
        if not isinstance(name, str) or not name or name in _GENERIC_DIAGNOSTIC_PREFIX:
            raise ValueError("generic diagnostic names must be non-empty non-core columns")
        if name in {"left_elevon_rad", "right_elevon_rad", "throttle"}:
            raise ValueError("generic diagnostics cannot reuse X8 actuator columns")
        diagnostics = _numeric_array(values, name=f"generic diagnostic {name}")
        if diagnostics.shape != (sample_count,):
            raise ValueError(f"generic diagnostic {name} shape must match steps")
    if not isinstance(episode.event_records, tuple):
        raise ValueError("generic event_records must be a tuple")
    if episode.event_records and sample_count == 0:
        raise ValueError("generic event_records require at least one stored sample")
    for record in episode.event_records:
        if not isinstance(record, MotionEvent):
            raise ValueError("generic event_records must contain MotionEvent values")
        _validate_event_values(
            record,
            label="generic",
            episode_end_s=float(time_s[-1]),
        )


def _validate_generated_episode(episode: GeneratedEpisode) -> None:
    """Validate all X8 arrays without changing legacy CSV serialization."""
    sample_count, time_s = _validate_common_motion_fields(episode, label="X8")
    euler = _numeric_array(episode.euler_rad, name="X8 euler_rad")
    airspeed = _numeric_array(episode.airspeed_mps, name="X8 airspeed_mps")
    alpha = _numeric_array(episode.alpha_rad, name="X8 alpha_rad")
    beta = _numeric_array(episode.beta_rad, name="X8 beta_rad")
    commands = _numeric_array(episode.raw_commands, name="X8 raw_commands")
    if euler.shape != (sample_count, 3) or commands.shape != (sample_count, 3):
        raise ValueError("X8 euler_rad and raw_commands must have shape N×3")
    if any(values.shape != (sample_count,) for values in (airspeed, alpha, beta)):
        raise ValueError("X8 scalar diagnostic arrays must have shape N")
    if not isinstance(episode.event_records, tuple):
        raise ValueError("X8 event_records must be a tuple")
    if episode.event_records and sample_count == 0:
        raise ValueError("X8 event_records require at least one stored sample")
    for record in episode.event_records:
        if not isinstance(record, EventRecord):
            raise ValueError("X8 event_records must contain EventRecord values")
        _validate_event_values(
            record,
            label="X8",
            episode_end_s=float(time_s[-1]),
        )


def _prepare_records(
    episodes: Iterable[MotionRecord],
) -> tuple[list[MotionRecord], str, tuple[str, ...] | None]:
    """Validate identity/type invariants before allocating an output location."""
    records = list(episodes)
    if not records:
        raise ValueError("at least one generated episode is required")
    if all(isinstance(episode, GeneratedEpisode) for episode in records):
        record_kind = "x8_generated_episode"
        generic_diagnostic_names = None
        for episode in records:
            assert isinstance(episode, GeneratedEpisode)
            _validate_generated_episode(episode)
    elif all(isinstance(episode, MotionEpisode) for episode in records):
        record_kind = "motion_episode"
        motion_records = [episode for episode in records if isinstance(episode, MotionEpisode)]
        for episode in motion_records:
            _validate_motion_episode(episode)
        generic_diagnostic_names = tuple(sorted(motion_records[0].diagnostic_arrays))
        if any(
            tuple(sorted(episode.diagnostic_arrays)) != generic_diagnostic_names
            for episode in motion_records[1:]
        ):
            raise ValueError("generic episodes must use matching diagnostic columns")
    else:
        raise ValueError("writer accepts a homogeneous list of GeneratedEpisode or MotionEpisode")

    ordered_records = sorted(records, key=lambda episode: episode.episode_id)
    identifiers = [episode.episode_id for episode in ordered_records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("episode IDs must be unique for dataset writing")
    return ordered_records, record_kind, generic_diagnostic_names


def sample_variant_noise(
    *, master_seed: int, episode_index: int, variant_id: str, count: int
) -> np.ndarray:
    """Draw independent zero-mean XYZ Gaussian noise from the declared stream identity."""
    if variant_id not in VARIANT_SIGMAS:
        raise ValueError(f"unsupported variant_id: {variant_id}")
    if count <= 0:
        raise ValueError("count must be positive")
    generator = np.random.default_rng(
        np.random.SeedSequence([master_seed, episode_index, NOISE_STREAM_IDS[variant_id]])
    )
    noise = generator.normal(loc=0.0, scale=VARIANT_SIGMAS[variant_id], size=(count, 3))
    assert noise.shape == (count, 3)
    return noise


def make_observation_variants(
    truth_position_enu_m: np.ndarray, *, master_seed: int, episode_index: int
) -> dict[str, np.ndarray]:
    """Create all three noisy observations from the same truth trajectory exactly once."""
    truth = np.asarray(truth_position_enu_m, dtype=float)
    if truth.ndim != 2 or truth.shape[1] != 3 or not np.all(np.isfinite(truth)):
        raise ValueError("truth_position_enu_m must be a finite N×3 array")
    return {
        variant_id: truth
        + sample_variant_noise(
            master_seed=master_seed,
            episode_index=episode_index,
            variant_id=variant_id,
            count=len(truth),
        )
        for variant_id in VARIANT_SIGMAS
    }


def assign_episode_splits(
    episode_ids: Iterable[str], *, master_seed: int, diagnostic: bool
) -> dict[str, str]:
    """Assign split labels once per truth episode; variants inherit that label."""
    identifiers = list(episode_ids)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("episode IDs must be unique for split assignment")
    if diagnostic:
        return {episode_id: "diagnostic" for episode_id in identifiers}
    generator = np.random.default_rng(np.random.SeedSequence([master_seed, 0, 99]))
    shuffled_indices = generator.permutation(len(identifiers))
    train_count = int(len(identifiers) * 0.70)
    validation_count = int(len(identifiers) * 0.15)
    labels = ["test"] * len(identifiers)
    for index in shuffled_indices[:train_count]:
        labels[int(index)] = "train"
    for index in shuffled_indices[train_count : train_count + validation_count]:
        labels[int(index)] = "validation"
    return dict(zip(identifiers, labels, strict=True))


def _format_float(value: float) -> str:
    return format(float(value), ".17g")


def _write_csv(
    path: Path, columns: tuple[str, ...] | list[str], rows: Iterable[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_hash() -> str:
    """Hash source and configuration files without relying on an absent Git repository."""
    root = Path(__file__).resolve().parents[2]
    paths = [root / "pyproject.toml"]
    for directory in (root / "contracts" / "v1", root / "data_generation" / "v1"):
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".yaml", ".yml"}
            and "tests" not in path.parts
        )
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _environment_text() -> str:
    versions = []
    for package in ("numpy", "scipy", "dash", "plotly", "kaleido", "omegaconf"):
        versions.append(f"{package}={importlib.metadata.version(package)}")
    return "\n".join(
        [
            f"python={sys.version}",
            f"platform={platform.platform()}",
            *versions,
            "",
        ]
    )


def _episode_is_complete(episode: GeneratedEpisode) -> bool:
    return (
        episode.status == "complete"
        and len(episode.steps) == SAMPLE_COUNT
        and episode.position_enu_m.shape == (SAMPLE_COUNT, 3)
        and episode.velocity_enu_mps.shape == (SAMPLE_COUNT, 3)
        and np.array_equal(episode.steps, np.arange(SAMPLE_COUNT, dtype=int))
        and np.allclose(episode.t_s, np.arange(SAMPLE_COUNT, dtype=float) * OUTPUT_DT_S)
    )


def _record_is_complete(episode: MotionRecord) -> bool:
    """Keep legacy X8 completion semantics while accepting validated generic records."""
    if isinstance(episode, GeneratedEpisode):
        return _episode_is_complete(episode)
    return episode.status == "complete"


def _truth_rows(episode: MotionRecord) -> list[dict[str, str]]:
    count = len(episode.steps)
    acceleration = np.gradient(
        episode.velocity_enu_mps, OUTPUT_DT_S, axis=0, edge_order=min(2, count - 1)
    )
    assert acceleration.shape == (count, 3)
    if not np.all(np.isfinite(acceleration)):
        raise ValueError("derived truth acceleration is not finite")
    rows: list[dict[str, str]] = []
    for step in range(count):
        position = episode.position_enu_m[step]
        velocity = episode.velocity_enu_mps[step]
        accel = acceleration[step]
        rows.append(
            {
                "episode_id": episode.episode_id,
                "step": str(step),
                "t_s": f"{step * OUTPUT_DT_S:.1f}",
                "x_m": _format_float(position[0]),
                "y_m": _format_float(position[1]),
                "z_m": _format_float(position[2]),
                "vx_mps": _format_float(velocity[0]),
                "vy_mps": _format_float(velocity[1]),
                "vz_mps": _format_float(velocity[2]),
                "ax_mps2": _format_float(accel[0]),
                "ay_mps2": _format_float(accel[1]),
                "az_mps2": _format_float(accel[2]),
            }
        )
    return rows


def _observation_rows(
    episode: MotionRecord, variants: dict[str, np.ndarray]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for variant_id in VARIANT_SIGMAS:
        sigma = VARIANT_SIGMAS[variant_id]
        values = variants[variant_id]
        for step in range(len(episode.steps)):
            position = values[step]
            rows.append(
                {
                    "episode_id": episode.episode_id,
                    "variant_id": variant_id,
                    "step": str(step),
                    "t_s": f"{step * OUTPUT_DT_S:.1f}",
                    "x_m": _format_float(position[0]),
                    "y_m": _format_float(position[1]),
                    "z_m": _format_float(position[2]),
                    "sigma_x_m": _format_float(sigma),
                    "sigma_y_m": _format_float(sigma),
                    "sigma_z_m": _format_float(sigma),
                    "valid": "1",
                }
            )
    return rows


def _command_rows(episode: MotionRecord) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in episode.event_records:
        rows.append(
            {
                "episode_id": episode.episode_id,
                "event_id": record.event_id,
                "start_s": _format_float(record.start_s),
                "end_s": _format_float(record.end_s),
                "trigger_kind": record.trigger_kind,
                "completed": "1" if record.completed else "0",
                "completion_reason": record.completion_reason,
            }
        )
    return rows


def _diagnostic_rows(episode: GeneratedEpisode) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for step in range(len(episode.steps)):
        command = episode.raw_commands[step]
        euler = episode.euler_rad[step]
        rows.append(
            {
                "episode_id": episode.episode_id,
                "step": str(int(episode.steps[step])),
                "t_s": _format_float(episode.t_s[step]),
                "airspeed_mps": _format_float(episode.airspeed_mps[step]),
                "alpha_rad": _format_float(episode.alpha_rad[step]),
                "beta_rad": _format_float(episode.beta_rad[step]),
                "roll_rad": _format_float(euler[0]),
                "pitch_rad": _format_float(euler[1]),
                "yaw_rad": _format_float(euler[2]),
                "left_elevon_rad": _format_float(command[0]),
                "right_elevon_rad": _format_float(command[1]),
                "throttle": _format_float(command[2]),
            }
        )
    return rows


def _motion_diagnostic_rows(
    episode: MotionEpisode, diagnostic_names: tuple[str, ...]
) -> list[dict[str, str]]:
    """Write only diagnostics whose names and units belong to the generic motion record."""
    rows: list[dict[str, str]] = []
    for step_index, step in enumerate(episode.steps):
        row = {
            "episode_id": episode.episode_id,
            "step": str(int(step)),
            "t_s": _format_float(episode.t_s[step_index]),
        }
        row.update(
            {
                name: _format_float(episode.diagnostic_arrays[name][step_index])
                for name in diagnostic_names
            }
        )
        rows.append(row)
    return rows


def _write_inventory(root: Path) -> None:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "files.csv":
            rows.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path),
                    "size_bytes": str(path.stat().st_size),
                }
            )
    _write_csv(root / "files.csv", ("relative_path", "sha256", "size_bytes"), rows)


def _write_request_snapshots(
    dataset_path: Path,
    *,
    request: GenerationRequest,
    model_config: dict[str, object],
    record_kind: str,
    episodes: list[MotionRecord],
) -> None:
    """Persist resolved request/model provenance before publishing any public records."""
    effective_config = OmegaConf.create(
        {
            "master_seed": request.master_seed,
            "mode": request.mode,
            "source_motion_check_status": request.source_motion_check_status,
            "identity_label": request.identity_label,
            "object_id": request.object_id,
            "model_id": request.model_id,
            "source_id": request.source_id,
            "input_id": request.input_id,
            "source_files": [asdict(source_file) for source_file in request.source_files],
            "model_config": model_config,
            "generator_version": GENERATOR_VERSION,
            "contract_version": CONTRACT_VERSION,
            "sample_count": (
                len(episodes[0].steps)
                if len({len(episode.steps) for episode in episodes}) == 1
                else None
            ),
            "episode_sample_counts": {
                episode.episode_id: len(episode.steps) for episode in episodes
            },
            "output_dt_s": OUTPUT_DT_S,
            "record_kind": record_kind,
        }
    )
    (dataset_path / "effective_config.yaml").write_text(
        OmegaConf.to_yaml(effective_config, resolve=True), encoding="utf-8", newline="\n"
    )
    (dataset_path / _MODEL_CONFIG_PATH).write_text(
        OmegaConf.to_yaml(OmegaConf.create(model_config), resolve=True),
        encoding="utf-8",
        newline="\n",
    )
    _write_csv(
        dataset_path / _PROVENANCE_PATH,
        _PROVENANCE_COLUMNS,
        (asdict(source_file) for source_file in request.source_files),
    )


def write_dataset(
    episodes: Iterable[MotionRecord],
    *,
    request: GenerationRequest,
    output_root: Path,
    source_motion_check_report: MotionCheckReport | None = None,
    training_config: Mapping[str, object] | None = None,
) -> DatasetWriteResult:
    """Write a new ID-addressed output folder; never replace an existing result."""
    _validate_generation_request(request)
    ordered_episodes, record_kind, generic_diagnostic_names = _prepare_records(episodes)
    model_config = _resolved_model_config(request.model_config)
    if training_config is not None:
        from .training_data import validate_training_config

        validate_training_config(dict(training_config))
    if record_kind == "x8_generated_episode" and (
        request.object_id != X8_OBJECT_ID or request.model_id != X8_MODEL_ID
    ):
        raise ValueError("GeneratedEpisode records require the legacy X8 model request")
    if record_kind == "motion_episode" and (
        request.object_id == X8_OBJECT_ID and request.model_id == X8_MODEL_ID
    ):
        raise ValueError("MotionEpisode records require an explicit non-legacy X8 model")
    if record_kind == "motion_episode" and source_motion_check_report is not None:
        raise ValueError("generic motion records cannot include the X8 source motion check")
    if request.code_hash_at_start is not None and _code_hash() != request.code_hash_at_start:
        raise ValueError(
            "generator code hash changed after simulation started; refusing publication"
        )

    # Keep a unique suffix while making folders identifiable without opening CSVs.
    label = re.sub(r"[^\w-]+", "_", request.identity_label, flags=re.UNICODE).strip("_")
    label = label[:32] or "dataset"
    dataset_id = (
        f"{label}_{request.mode}_{len(ordered_episodes)}episodes_"
        f"seed{request.master_seed}_{uuid.uuid4().hex[:12]}"
    )
    dataset_path = output_root / "data_generation" / GENERATOR_VERSION / dataset_id
    if dataset_path.exists():
        raise FileExistsError(f"refusing to overwrite existing dataset output: {dataset_path}")
    dataset_path.mkdir(parents=True, exist_ok=False)
    complete = all(_record_is_complete(episode) for episode in ordered_episodes)
    status = "complete" if complete else "failed"

    _write_request_snapshots(
        dataset_path,
        request=request,
        model_config=model_config,
        record_kind=record_kind,
        episodes=ordered_episodes,
    )
    (dataset_path / "environment.txt").write_text(
        _environment_text(), encoding="utf-8", newline="\n"
    )

    if complete:
        splits = assign_episode_splits(
            [episode.episode_id for episode in ordered_episodes],
            master_seed=request.master_seed,
            diagnostic=request.mode == "diagnostic",
        )
        episodes_rows = [
            {
                "episode_id": episode.episode_id,
                "split": splits[episode.episode_id],
                "duration_s": _format_float(episode.t_s[-1]),
                "sample_count": str(len(episode.steps)),
                "dt_s": f"{OUTPUT_DT_S:g}",
                "identity_label": request.identity_label,
            }
            for episode in ordered_episodes
        ]

        def observation_rows():
            for episode_index, episode in enumerate(ordered_episodes):
                variants = make_observation_variants(
                    episode.position_enu_m,
                    master_seed=request.master_seed,
                    episode_index=episode_index,
                )
                yield from _observation_rows(episode, variants)

        def diagnostic_rows():
            for episode in ordered_episodes:
                if isinstance(episode, GeneratedEpisode):
                    yield from _diagnostic_rows(episode)
                else:
                    assert generic_diagnostic_names is not None
                    yield from _motion_diagnostic_rows(episode, generic_diagnostic_names)

        commands = [row for episode in ordered_episodes for row in _command_rows(episode)]
        _write_csv(dataset_path / "public" / "episodes.csv", PUBLIC_EPISODE_COLUMNS, episodes_rows)
        _write_csv(
            dataset_path / "public" / "observations.csv",
            PUBLIC_OBSERVATION_COLUMNS,
            observation_rows(),
        )
        _write_csv(
            dataset_path / "evaluation" / "truth.csv",
            TRUTH_COLUMNS,
            (row for episode in ordered_episodes for row in _truth_rows(episode)),
        )
        _write_csv(
            dataset_path / "evaluation" / "commands.csv",
            _EVENT_COLUMNS,
            commands,
        )
        _write_csv(
            dataset_path / "evaluation" / "triggers.csv",
            _EVENT_COLUMNS,
            commands,
        )
        diagnostic_columns = _X8_DIAGNOSTIC_COLUMNS
        if record_kind == "motion_episode":
            assert generic_diagnostic_names is not None
            diagnostic_columns = _GENERIC_DIAGNOSTIC_PREFIX + generic_diagnostic_names
        _write_csv(
            dataset_path / "evaluation" / "diagnostics.csv",
            diagnostic_columns,
            diagnostic_rows(),
        )
        if training_config is not None:
            from .training_data import episode_metadata, write_sequence_index

            write_sequence_index(
                dataset_path,
                episode_metadata(episodes_rows),
                dict(training_config),
                seed=request.master_seed,
                object_type=request.object_id,
            )
        if source_motion_check_report is not None:
            _write_csv(
                dataset_path / "evaluation" / "source_motion_check.csv",
                ("scope", "metric", "rmse", "bias", "max_abs"),
                source_motion_check_report.csv_rows(),
            )
    else:
        _write_csv(
            dataset_path / "failed_episodes.csv",
            ("episode_id", "status", "failure_reason", "sample_count"),
            (
                {
                    "episode_id": episode.episode_id,
                    "status": episode.status,
                    "failure_reason": episode.failure_reason or "unknown",
                    "sample_count": str(len(episode.steps)),
                }
                for episode in ordered_episodes
            ),
        )

    _write_csv(
        dataset_path / "manifest.csv",
        (
            "kind",
            "version",
            "contract_version",
            "source_id",
            "input_id",
            "code_hash",
            "seed",
            "config_path",
            "environment_path",
            "status",
            "source_motion_check_status",
            "object_id",
            "model_id",
            "provenance_path",
            "model_config_path",
            "record_kind",
        ),
        [
            {
                "kind": "data_generation",
                "version": GENERATOR_VERSION,
                "contract_version": CONTRACT_VERSION,
                "source_id": request.source_id,
                "input_id": request.input_id,
                "code_hash": (
                    request.code_hash_at_start
                    if request.code_hash_at_start is not None
                    else _code_hash()
                ),
                "seed": str(request.master_seed),
                "config_path": "effective_config.yaml",
                "environment_path": "environment.txt",
                "status": status,
                "source_motion_check_status": request.source_motion_check_status,
                "object_id": request.object_id,
                "model_id": request.model_id,
                "provenance_path": _PROVENANCE_PATH,
                "model_config_path": _MODEL_CONFIG_PATH,
                "record_kind": record_kind,
            }
        ],
    )
    _write_inventory(dataset_path)
    return DatasetWriteResult(dataset_id=dataset_id, path=dataset_path, status=status)
