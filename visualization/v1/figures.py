"""Synchronized ENU trajectory figures and immutable PNG exports."""

from __future__ import annotations

import csv
import hashlib
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import numpy as np
import plotly
import plotly.graph_objects as go

from contracts.v1.csv_io import read_evaluation_truth, read_public_dataset
from contracts.v1.types import PublicDataset
from contracts.v1.validation import (
    MAX_SAMPLE_COUNT,
    MIN_SAMPLE_COUNT,
    OUTPUT_DT_S,
    SAMPLE_COUNT,
    VARIANT_SIGMAS,
    ContractError,
)

_VIEW_AXES = {
    "xy": (0, 1, "East x (m)", "North y (m)"),
    "xz": (0, 2, "East x (m)", "Up z (m)"),
    "yz": (1, 2, "North y (m)", "Up z (m)"),
}
_TRACE_COLORS = ("#005f73", "#ca6702", "#3a86ff", "#8338ec")
_TRUTH_LINE = "truth_line"
_OBSERVATION_LINE = "observation_line"
_TRUTH_CURRENT = "truth_current"
_OBSERVATION_CURRENT = "observation_current"
_TRACE_SLOTS_WITH_TRUTH = (
    _TRUTH_LINE,
    _OBSERVATION_LINE,
    _TRUTH_CURRENT,
    _OBSERVATION_CURRENT,
)
_TRACE_SLOTS_PUBLIC = (_OBSERVATION_LINE, _OBSERVATION_CURRENT)
_CURRENT_MARKER_SLOTS = frozenset({_TRUTH_CURRENT, _OBSERVATION_CURRENT})
_TRUTH_SLOTS = frozenset({_TRUTH_LINE, _TRUTH_CURRENT})
_TRACE_SLOT_LABELS = {
    _TRUTH_LINE: "Simulation truth",
    _OBSERVATION_LINE: "Observation (measurement noise; not actual path)",
    _TRUTH_CURRENT: "Simulation truth current",
    _OBSERVATION_CURRENT: "Observation current",
}
_INK = "#1f1f1f"
_MUTED = "#6b6b6b"
_GRID = "#e5e3df"
_PAPER = "#fafaf9"
_PLOT = "#ffffff"
_SPLIT_NAMES = {
    "train": "학습",
    "validation": "검증",
    "test": "시험",
    "diagnostic": "진단",
}

assert OUTPUT_DT_S == 0.2


@dataclass(frozen=True, slots=True)
class ViewerDataset:
    """Validated public observations with optional, explicitly loaded truth."""

    source_path: Path
    public: PublicDataset
    _observations: dict[tuple[str, str], np.ndarray]
    _truth: dict[str, np.ndarray]
    _kinematics: dict[str, np.ndarray] = field(default_factory=dict)

    def kinematics(self, episode_id: str, step: int) -> np.ndarray:
        """Return saved ENU velocity/acceleration, never differentiated observations."""
        if episode_id not in self._kinematics:
            raise ContractError(f"kinematics unavailable for episode: {episode_id}")
        values = self._kinematics[episode_id]
        if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < len(values):
            raise ValueError("kinematics step is outside the stored timeline")
        return values[step].copy()

    @property
    def episode_ids(self) -> tuple[str, ...]:
        """Return episode IDs in their public metadata order."""
        return self.public.episode_ids

    @property
    def has_truth(self) -> bool:
        """Whether this viewer instance was deliberately granted truth access."""
        return bool(self._truth)

    def sample_count(self, episode_ids: tuple[str, ...] | list[str]) -> int:
        """Return the shared stored timeline; never repeat shorter episode endpoints."""
        counts = {episode.episode_id: episode.sample_count for episode in self.public.episodes}
        if not episode_ids or any(item not in counts for item in episode_ids):
            raise ValueError("select known episode IDs for playback")
        count = min(counts[item] for item in episode_ids)
        assert MIN_SAMPLE_COUNT <= count <= MAX_SAMPLE_COUNT
        return count

    def episode_display_name(self, episode_id: str) -> str:
        """Return a human-readable, public-metadata-only episode name."""
        for index, metadata in enumerate(self.public.episodes):
            if metadata.episode_id == episode_id:
                ordinal = sum(
                    candidate.split == metadata.split
                    for candidate in self.public.episodes[: index + 1]
                )
                identity_label = metadata.identity_label.strip()
                split_name = _SPLIT_NAMES[metadata.split]
                return f"{identity_label} · {split_name} 시뮬레이션 {ordinal}"
        raise ContractError(f"unknown episode ID: {episode_id}")

    def episode_option_label(self, episode_id: str) -> str:
        """Use readable labels; stable IDs remain the selection values and tooltips."""
        return self.episode_display_name(episode_id)

    def observed(self, episode_id: str, variant_id: str) -> np.ndarray:
        """Return the stored XYZ observations without interpolating or resampling."""
        try:
            return self._observations[(episode_id, variant_id)]
        except KeyError as error:
            raise ContractError(
                f"unknown observation selection: {episode_id}/{variant_id}"
            ) from error

    def truth(self, episode_id: str) -> np.ndarray:
        """Return evaluation-only XYZ truth that was explicitly loaded."""
        try:
            return self._truth[episode_id]
        except KeyError as error:
            raise ContractError(f"truth is unavailable for episode: {episode_id}") from error


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Paths for one immutable visualization export run."""

    path: Path
    png_path: Path


@dataclass(frozen=True, slots=True)
class ExportSetResult:
    """Paths for one synchronized four-view visualization export run."""

    path: Path
    png_paths: dict[str, Path]


def _to_positions(
    public: PublicDataset, value_names: tuple[str, str, str]
) -> dict[tuple[str, str], np.ndarray]:
    """Index public observations into exact stored steps."""
    indexed: dict[tuple[str, str], np.ndarray] = {}
    grouped: dict[tuple[str, str], list] = {}
    counts = {episode.episode_id: episode.sample_count for episode in public.episodes}
    for record in public.observations:
        grouped.setdefault((record.episode_id, record.variant_id), []).append(record)
    for key, sequence in grouped.items():
        sequence.sort(key=lambda record: record.step)
        count = counts[key[0]]
        if [record.step for record in sequence] != list(range(count)):
            raise ContractError(f"viewer requires stored steps 0 through {count - 1}: {key}")
        indexed[key] = np.asarray(
            [[getattr(record, field) for field in value_names] for record in sequence], dtype=float
        )
    return indexed


def _load_truth(dataset_path: Path, public: PublicDataset) -> dict[str, np.ndarray]:
    """Load truth solely from the explicit evaluation path and validate its timeline."""
    records = read_evaluation_truth(dataset_path / "evaluation" / "truth.csv")
    grouped: dict[str, list] = {}
    for record in records:
        grouped.setdefault(record.episode_id, []).append(record)

    truth: dict[str, np.ndarray] = {}
    for metadata in public.episodes:
        episode_id = metadata.episode_id
        count = metadata.sample_count
        sequence = sorted(grouped.get(episode_id, []), key=lambda record: record.step)
        if len(sequence) != count or [record.step for record in sequence] != list(range(count)):
            raise ContractError(f"truth must provide {count} stored steps for {episode_id}")
        expected_times = np.arange(count, dtype=float) * metadata.dt_s
        actual_times = np.asarray([record.t_s for record in sequence], dtype=float)
        if not np.allclose(actual_times, expected_times, rtol=0.0, atol=1e-12):
            raise ContractError(f"truth timeline is not the v1 0.2-second grid for {episode_id}")
        truth[episode_id] = np.asarray(
            [
                [
                    record.x_m,
                    record.y_m,
                    record.z_m,
                    record.vx_mps,
                    record.vy_mps,
                    record.vz_mps,
                    record.ax_mps2,
                    record.ay_mps2,
                    record.az_mps2,
                ]
                for record in sequence
            ],
            dtype=float,
        )
    return truth


def load_viewer_dataset(dataset_path: Path, *, public_only: bool = False) -> ViewerDataset:
    """Load one explicitly selected dataset; never infer a latest output directory."""
    resolved_path = Path(dataset_path).resolve()
    public = read_public_dataset(resolved_path / "public")
    observations = _to_positions(public, ("x_m", "y_m", "z_m"))
    truth = {} if public_only else _load_truth(resolved_path, public)
    return ViewerDataset(
        source_path=resolved_path,
        public=public,
        _observations=observations,
        _truth={key: values[:, :3] for key, values in truth.items()},
        _kinematics={key: values[:, 3:] for key, values in truth.items()},
    )


def _validate_selection(
    dataset: ViewerDataset,
    view: str,
    episode_ids: list[str] | tuple[str, ...],
    variant_id: str,
    step: int,
) -> tuple[str, ...]:
    """Reject unsupported viewer states instead of silently changing them."""
    if view not in {*_VIEW_AXES, "3d"}:
        raise ValueError(f"unsupported view: {view}")
    if not 1 <= len(episode_ids) <= 4:
        raise ValueError("select from one to four episodes")
    selected = tuple(episode_ids)
    if len(set(selected)) != len(selected):
        raise ValueError("episode selection cannot contain duplicates")
    unknown = set(selected) - set(dataset.episode_ids)
    if unknown:
        raise ValueError(f"unknown episode IDs: {sorted(unknown)}")
    if variant_id not in VARIANT_SIGMAS:
        raise ValueError(f"unsupported observation variant: {variant_id}")
    count = dataset.sample_count(selected)
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < count:
        raise ValueError(f"step must be an integer from 0 to {count - 1}")
    return selected


def _axis_range(values: list[np.ndarray]) -> list[float]:
    """Add deterministic whitespace around a physical coordinate range."""
    merged = np.concatenate(values)
    low = float(np.min(merged))
    high = float(np.max(merged))
    span = high - low
    padding = max(span * 0.06, 1.0)
    return [low - padding, high + padding]


def _base_layout(title: str) -> dict:
    """Return the restrained research-viewer visual language shared by all figures."""
    return {
        "title": {"text": title, "font": {"size": 15, "color": _INK}, "x": 0.01},
        "paper_bgcolor": _PAPER,
        "plot_bgcolor": _PLOT,
        "font": {"family": "Inter, Segoe UI, Arial, sans-serif", "color": _INK, "size": 12},
        "margin": {"l": 58, "r": 24, "t": 48, "b": 52},
        "legend": {
            "orientation": "h",
            "yanchor": "top",
            "y": -0.18,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 10},
        },
        "hovermode": "closest",
        "uirevision": "stored-trajectory-grid-v1",
    }


def _trace_slots(include_truth: bool) -> tuple[str, ...]:
    """Return the one trace order shared by static figures and playback updates."""
    return _TRACE_SLOTS_WITH_TRUTH if include_truth else _TRACE_SLOTS_PUBLIC


def _current_marker_trace_indexes(episode_count: int, *, include_truth: bool) -> list[int]:
    """Locate current markers within the shared per-Episode trace ordering."""
    slots = _trace_slots(include_truth)
    return [
        episode_index * len(slots) + trace_index
        for episode_index in range(episode_count)
        for trace_index, trace_slot in enumerate(slots)
        if trace_slot in _CURRENT_MARKER_SLOTS
    ]


def _current_marker_positions(
    dataset: ViewerDataset,
    *,
    episode_ids: tuple[str, ...],
    variant_id: str,
    step: int,
    include_truth: bool,
) -> list[np.ndarray]:
    """Order exact stored current positions to match the marker trace indexes."""
    positions: list[np.ndarray] = []
    for episode_id in episode_ids:
        truth = dataset.truth(episode_id) if include_truth else None
        observed = dataset.observed(episode_id, variant_id)
        for trace_slot in _trace_slots(include_truth):
            if trace_slot == _TRUTH_CURRENT:
                assert truth is not None
                positions.append(truth[step])
            elif trace_slot == _OBSERVATION_CURRENT:
                positions.append(observed[step])
    return positions


def build_projection_figure(
    dataset: ViewerDataset,
    *,
    view: str,
    episode_ids: list[str] | tuple[str, ...],
    variant_id: str,
    step: int,
    show_truth: bool,
) -> go.Figure:
    """Render exact stored samples in one XY, XZ, YZ, or physical-scale 3D view."""
    selected = _validate_selection(dataset, view, episode_ids, variant_id, step)
    include_truth = show_truth and dataset.has_truth
    title = "Stored trajectory · current marker follows playback"
    figure = go.Figure(layout=_base_layout(title))
    full_coordinate_sets: list[np.ndarray] = []
    trace_slots = _trace_slots(include_truth)

    for index, episode_id in enumerate(selected):
        color = _TRACE_COLORS[index]
        observed = dataset.observed(episode_id, variant_id)
        truth = dataset.truth(episode_id) if include_truth else None
        episode_label = dataset.episode_display_name(episode_id)
        full_coordinate_sets.append(observed)
        if truth is not None:
            full_coordinate_sets.append(truth)

        for trace_slot in trace_slots:
            is_current = trace_slot in _CURRENT_MARKER_SLOTS
            values = truth if trace_slot in _TRUTH_SLOTS else observed
            assert values is not None
            if is_current:
                values = values[step : step + 1]

            trace_kwargs = {
                "mode": "markers" if is_current else "lines",
                "name": f"{_TRACE_SLOT_LABELS[trace_slot]} · {episode_label}",
                "legendgroup": episode_id,
            }
            if is_current:
                trace_kwargs["marker"] = {
                    "color": color,
                    "size": 6 if view == "3d" else 10,
                    "symbol": "circle" if trace_slot == _TRUTH_CURRENT else "diamond",
                }
            else:
                is_observation = trace_slot == _OBSERVATION_LINE
                line_width = (
                    (3 if is_observation else 5)
                    if view == "3d"
                    else (1.8 if is_observation else 2.4)
                )
                trace_kwargs["line"] = {
                    "color": color,
                    "width": line_width,
                    **({"dash": "dash"} if is_observation else {}),
                }
                if is_observation:
                    trace_kwargs["opacity"] = 0.78

            if view == "3d":
                figure.add_trace(
                    go.Scatter3d(
                        x=values[:, 0].tolist() if is_current else values[:, 0],
                        y=values[:, 1].tolist() if is_current else values[:, 1],
                        z=values[:, 2].tolist() if is_current else values[:, 2],
                        **trace_kwargs,
                    )
                )
            else:
                first_axis, second_axis, _, _ = _VIEW_AXES[view]
                figure.add_trace(
                    go.Scatter(
                        x=(values[:, first_axis].tolist() if is_current else values[:, first_axis]),
                        y=(
                            values[:, second_axis].tolist()
                            if is_current
                            else values[:, second_axis]
                        ),
                        **trace_kwargs,
                    )
                )

    if view == "3d":
        figure.update_layout(
            scene={
                "xaxis": {"title": "East x (m)", "backgroundcolor": _PLOT, "gridcolor": _GRID},
                "yaxis": {"title": "North y (m)", "backgroundcolor": _PLOT, "gridcolor": _GRID},
                "zaxis": {"title": "Up z (m)", "backgroundcolor": _PLOT, "gridcolor": _GRID},
                "aspectmode": "data",
            }
        )
    else:
        first_axis, second_axis, x_title, y_title = _VIEW_AXES[view]
        figure.update_xaxes(
            title=x_title,
            range=_axis_range([points[:, first_axis] for points in full_coordinate_sets]),
            gridcolor=_GRID,
            zerolinecolor=_GRID,
            showline=True,
            linecolor="#b9b6b0",
        )
        figure.update_yaxes(
            title=y_title,
            range=_axis_range([points[:, second_axis] for points in full_coordinate_sets]),
            gridcolor=_GRID,
            zerolinecolor=_GRID,
            showline=True,
            linecolor="#b9b6b0",
            scaleanchor="x",
            scaleratio=1,
            constraintoward="center",
        )
    return figure


def build_current_marker_extensions(
    dataset: ViewerDataset,
    *,
    episode_ids: list[str] | tuple[str, ...],
    variant_id: str,
    step: int,
    show_truth: bool,
) -> dict[str, tuple[dict[str, list[list[float]]], list[int], int]]:
    """Build lightweight Plotly updates that replace only current-position markers."""
    selected = _validate_selection(dataset, "xy", episode_ids, variant_id, step)
    include_truth = show_truth and dataset.has_truth
    marker_trace_indexes = _current_marker_trace_indexes(len(selected), include_truth=include_truth)
    positions = _current_marker_positions(
        dataset,
        episode_ids=selected,
        variant_id=variant_id,
        step=step,
        include_truth=include_truth,
    )
    extensions: dict[str, tuple[dict[str, list[list[float]]], list[int], int]] = {}

    for view, axes in _VIEW_AXES.items():
        first_axis, second_axis, _, _ = axes
        extensions[view] = (
            {
                "x": [[float(position[first_axis])] for position in positions],
                "y": [[float(position[second_axis])] for position in positions],
            },
            marker_trace_indexes,
            1,
        )
    extensions["3d"] = (
        {
            "x": [[float(position[0])] for position in positions],
            "y": [[float(position[1])] for position in positions],
            "z": [[float(position[2])] for position in positions],
        },
        marker_trace_indexes,
        1,
    )
    return extensions


def advance_player(
    state: dict[str, int | bool],
    *,
    trigger: str,
    requested_step: int | None = None,
    sample_count: int = SAMPLE_COUNT,
) -> dict[str, int | bool]:
    """Advance a player only along the stored integer sample index grid."""
    current_step = state.get("step")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or not MIN_SAMPLE_COUNT <= sample_count <= MAX_SAMPLE_COUNT
    ):
        raise ValueError("sample_count must be a supported stored timeline length")
    playing = state.get("playing")
    if (
        isinstance(current_step, bool)
        or not isinstance(current_step, int)
        or not 0 <= current_step < sample_count
    ):
        raise ValueError("player state step must be a stored v1 sample index")
    if not isinstance(playing, bool):
        raise ValueError("player state playing must be boolean")

    if trigger == "seek":
        if isinstance(requested_step, bool) or not isinstance(requested_step, int):
            raise ValueError("seek requires an integer stored step")
        if not 0 <= requested_step < sample_count:
            raise ValueError(f"seek step must be from 0 to {sample_count - 1}")
        return {"step": requested_step, "playing": playing}
    if trigger == "play":
        return {"step": current_step, "playing": current_step < sample_count - 1}
    if trigger == "pause":
        return {"step": current_step, "playing": False}
    if trigger == "tick":
        if not playing or current_step == sample_count - 1:
            return {"step": current_step, "playing": False}
        next_step = current_step + 1
        return {"step": next_step, "playing": next_step < sample_count - 1}
    raise ValueError(f"unsupported player trigger: {trigger}")


def _code_hash() -> str:
    """Hash this module as the viewer-export implementation identifier."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _file_hash(path: Path) -> str:
    """Return the SHA-256 digest of one completed export file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    """Write explicit CSV headers for a visualization output record."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_png(
    figure: go.Figure,
    *,
    dataset_path: Path,
    view: str,
    episode_ids: list[str] | tuple[str, ...],
    variant_id: str,
    step: int,
    output_root: Path,
) -> ExportResult:
    """Export one PNG into a new immutable visualization run directory."""
    if view not in {*_VIEW_AXES, "3d"}:
        raise ValueError(f"unsupported view: {view}")
    if not 1 <= len(episode_ids) <= 4 or len(set(episode_ids)) != len(episode_ids):
        raise ValueError("PNG export requires one to four distinct episode IDs")
    if variant_id not in VARIANT_SIGMAS:
        raise ValueError(f"unsupported observation variant: {variant_id}")
    _validate_selection(
        load_viewer_dataset(dataset_path, public_only=True), view, episode_ids, variant_id, step
    )

    input_path = Path(dataset_path).resolve()
    run_path = Path(output_root).resolve() / "visualization" / "v1" / str(uuid4())
    run_path.mkdir(parents=True, exist_ok=False)
    png_path = run_path / f"{view}_step_{step:03d}.png"
    try:
        figure.write_image(png_path, format="png", scale=2)
    except Exception as error:
        raise RuntimeError(
            "PNG export failed; Plotly/Kaleido image support is unavailable"
        ) from error

    environment_path = run_path / "environment.txt"
    environment_path.write_text(
        "\n".join(
            (
                f"python={sys.version}",
                f"platform={platform.platform()}",
                f"plotly={plotly.__version__}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(
        run_path / "view_state.csv",
        ("view", "episode_ids", "variant_id", "step", "t_s", "input_dataset_path"),
        [
            {
                "view": view,
                "episode_ids": ";".join(episode_ids),
                "variant_id": variant_id,
                "step": str(step),
                "t_s": f"{step * OUTPUT_DT_S:.1f}",
                "input_dataset_path": str(input_path),
            }
        ],
    )
    _write_csv(
        run_path / "manifest.csv",
        (
            "kind",
            "version",
            "contract_version",
            "input_dataset_id",
            "input_dataset_path",
            "code_hash",
            "view",
            "variant_id",
            "step",
            "environment_path",
            "status",
        ),
        [
            {
                "kind": "visualization",
                "version": "v1",
                "contract_version": "v1",
                "input_dataset_id": input_path.name,
                "input_dataset_path": str(input_path),
                "code_hash": _code_hash(),
                "view": view,
                "variant_id": variant_id,
                "step": str(step),
                "environment_path": environment_path.name,
                "status": "complete",
            }
        ],
    )
    _write_csv(
        run_path / "files.csv",
        ("path", "sha256"),
        [
            {"path": png_path.name, "sha256": _file_hash(png_path)},
            {"path": "view_state.csv", "sha256": _file_hash(run_path / "view_state.csv")},
            {"path": "environment.txt", "sha256": _file_hash(environment_path)},
        ],
    )
    return ExportResult(path=run_path, png_path=png_path)


def export_png_set(
    figures: dict[str, go.Figure],
    *,
    dataset_path: Path,
    episode_ids: list[str] | tuple[str, ...],
    variant_id: str,
    step: int,
    output_root: Path,
) -> ExportSetResult:
    """Export XY/XZ/YZ/3D snapshots together into one immutable run directory."""
    expected_views = ("xy", "xz", "yz", "3d")
    if set(figures) != set(expected_views):
        raise ValueError("four-view export requires exactly xy, xz, yz, and 3d figures")
    if not 1 <= len(episode_ids) <= 4 or len(set(episode_ids)) != len(episode_ids):
        raise ValueError("four-view export requires one to four distinct episode IDs")
    if variant_id not in VARIANT_SIGMAS:
        raise ValueError(f"unsupported observation variant: {variant_id}")
    _validate_selection(
        load_viewer_dataset(dataset_path, public_only=True), "xy", episode_ids, variant_id, step
    )

    input_path = Path(dataset_path).resolve()
    run_path = Path(output_root).resolve() / "visualization" / "v1" / str(uuid4())
    run_path.mkdir(parents=True, exist_ok=False)
    png_paths = {view: run_path / f"{view}_step_{step:03d}.png" for view in expected_views}
    try:
        for view in expected_views:
            figures[view].write_image(png_paths[view], format="png", scale=2)
    except Exception as error:
        raise RuntimeError(
            "PNG export failed; Plotly/Kaleido image support is unavailable"
        ) from error

    environment_path = run_path / "environment.txt"
    environment_path.write_text(
        "\n".join(
            (
                f"python={sys.version}",
                f"platform={platform.platform()}",
                f"plotly={plotly.__version__}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(
        run_path / "view_state.csv",
        ("views", "episode_ids", "variant_id", "step", "t_s", "input_dataset_path"),
        [
            {
                "views": ";".join(expected_views),
                "episode_ids": ";".join(episode_ids),
                "variant_id": variant_id,
                "step": str(step),
                "t_s": f"{step * OUTPUT_DT_S:.1f}",
                "input_dataset_path": str(input_path),
            }
        ],
    )
    _write_csv(
        run_path / "manifest.csv",
        (
            "kind",
            "version",
            "contract_version",
            "input_dataset_id",
            "input_dataset_path",
            "code_hash",
            "views",
            "variant_id",
            "step",
            "environment_path",
            "status",
        ),
        [
            {
                "kind": "visualization",
                "version": "v1",
                "contract_version": "v1",
                "input_dataset_id": input_path.name,
                "input_dataset_path": str(input_path),
                "code_hash": _code_hash(),
                "views": ";".join(expected_views),
                "variant_id": variant_id,
                "step": str(step),
                "environment_path": environment_path.name,
                "status": "complete",
            }
        ],
    )
    file_rows = [
        {"path": png_path.name, "sha256": _file_hash(png_path)} for png_path in png_paths.values()
    ]
    file_rows.extend(
        (
            {"path": "view_state.csv", "sha256": _file_hash(run_path / "view_state.csv")},
            {"path": "environment.txt", "sha256": _file_hash(environment_path)},
        )
    )
    _write_csv(run_path / "files.csv", ("path", "sha256"), file_rows)
    return ExportSetResult(path=run_path, png_paths=png_paths)
