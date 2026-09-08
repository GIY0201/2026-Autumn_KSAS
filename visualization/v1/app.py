"""Local Dash application for synchronized v1 trajectory playback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dash import Dash, Input, Output, Patch, State, ctx, dcc, html, no_update

from contracts.v1.validation import OUTPUT_DT_S, SAMPLE_COUNT, VARIANT_SIGMAS
from data_generation.v1.service import GenerationService

from .figures import (
    ViewerDataset,
    advance_player,
    build_current_marker_extensions,
    build_projection_figure,
    export_png_set,
    load_viewer_dataset,
)
from .generation_ui import DatasetRegistry, generation_panel, register_generation_callbacks
from .telemetry import telemetry_content, telemetry_panel

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VIEW_IDS = ("xy", "xz", "yz", "3d")
_SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
_MAX_COMPARED_EPISODES = 4
_DEFAULT_PLAYER_STATE = {"step": 0, "playing": False}
_GRAPH_CANVAS_HEIGHT = "clamp(22rem, 36vw, 34rem)"

assert OUTPUT_DT_S == 0.2
assert _SPEEDS == tuple(sorted(_SPEEDS))


def _selected_episode_ids(value: Any, dataset: ViewerDataset) -> tuple[tuple[str, ...], str]:
    """Apply the explicit maximum-four comparison rule with a visible message."""
    if isinstance(value, str):
        requested = (value,)
    elif isinstance(value, (list, tuple)):
        requested = tuple(item for item in value if isinstance(item, str))
    else:
        requested = ()
    known = tuple(item for item in requested if item in dataset.episode_ids)
    if not known:
        return (dataset.episode_ids[0],), "Select one to four episodes to compare."
    if len(known) > _MAX_COMPARED_EPISODES:
        return known[:_MAX_COMPARED_EPISODES], (
            f"Only the first {_MAX_COMPARED_EPISODES} selected episodes are displayed; "
            f"comparison is limited to {_MAX_COMPARED_EPISODES}."
        )
    return known, ""


def _episode_availability_message(episode_count: int) -> str:
    """Describe whether this dataset has alternatives for an Episode comparison."""
    if episode_count == 1:
        return (
            "이 dataset에는 Episode가 1개뿐입니다. 여러 Episode를 비교하려면 "
            "Episode가 둘 이상인 dataset을 여세요."
        )
    return (
        f"이 dataset에는 Episode가 {episode_count}개 있습니다. "
        f"최대 {_MAX_COMPARED_EPISODES}개를 함께 비교할 수 있습니다."
    )


def _normalize_step(value: Any, fallback: int, sample_count: int = SAMPLE_COUNT) -> int:
    """Accept only a stored integer sample index from Dash component state."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and 0 <= value < sample_count:
        return value
    if isinstance(value, float) and value.is_integer() and 0 <= int(value) < sample_count:
        return int(value)
    return fallback


def _player_status(state: dict[str, int | bool], sample_count: int = SAMPLE_COUNT) -> str:
    """Describe current stored time and playback state without implying interpolation."""
    step = int(state["step"])
    phase = "Playing" if state["playing"] else "Paused"
    return f"{phase}. Stored step {step} of {sample_count - 1}; t = {step * OUTPUT_DT_S:.1f} s."


def _slider_marks(sample_count: int) -> dict[int, str]:
    """Use stored indices, including both endpoints, for timeline labels."""
    end = sample_count - 1
    return {
        step: f"{step * OUTPUT_DT_S:g} s"
        for step in sorted({round(end * fraction / 4) for fraction in range(5)})
    }


def _timeline_warning(dataset: ViewerDataset, selected: tuple[str, ...]) -> str:
    counts = {
        episode.sample_count
        for episode in dataset.public.episodes
        if episode.episode_id in selected
    }
    if len(counts) > 1:
        duration = (min(counts) - 1) * OUTPUT_DT_S
        return f" Episode 길이가 달라 공통 구간 0–{duration:g} s까지만 재생합니다."
    return ""


def _build_figures(
    dataset: ViewerDataset,
    selected: tuple[str, ...],
    variant_id: str,
    state: dict[str, int | bool],
    show_truth: bool,
) -> tuple:
    """Build all panels from one exact selection and one shared integer time step."""
    step = _normalize_step(state["step"], 0, dataset.sample_count(selected))
    return tuple(
        build_projection_figure(
            dataset,
            view=view,
            episode_ids=selected,
            variant_id=variant_id,
            step=step,
            show_truth=show_truth,
        )
        for view in _VIEW_IDS
    )


def _graph_section(figure_id: str, heading_id: str, title: str, figure) -> html.Section:
    """Give each Plotly canvas a visible, semantic projection heading."""
    graph_config = {
        "displaylogo": False,
        "responsive": True,
        "toImageButtonOptions": {"format": "png", "filename": "x8-trajectory-view"},
    }
    return html.Section(
        [
            html.H3(title, id=heading_id, className="figure-heading"),
            dcc.Graph(
                id=figure_id,
                figure=figure,
                config=graph_config,
                style={"height": _GRAPH_CANVAS_HEIGHT},
            ),
        ],
        className="figure-card",
        **{"aria-labelledby": heading_id},
    )


def _viewer_layout(dataset: ViewerDataset) -> html.Div:
    """Create a semantic, keyboard-operable local research viewer layout."""
    episode_count = len(dataset.episode_ids)
    initial_figures = dict(
        zip(
            _VIEW_IDS,
            _build_figures(
                dataset,
                dataset.episode_ids[:1],
                "sigma_3m",
                _DEFAULT_PLAYER_STATE,
                dataset.has_truth,
            ),
            strict=True,
        )
    )
    sample_count = dataset.sample_count(dataset.episode_ids[:1])
    durations = [episode.duration_s for episode in dataset.public.episodes]
    duration_label = (
        f"{durations[0]:g}"
        if min(durations) == max(durations)
        else f"{min(durations):g}–{max(durations):g}"
    )
    episode_options = [
        {
            "label": dataset.episode_option_label(episode.episode_id),
            "value": episode.episode_id,
            "title": f"ID: {episode.episode_id}",
        }
        for episode in dataset.public.episodes
    ]
    variant_options = [
        {"label": f"{variant_id.replace('_', ' ')} (σ = {sigma:.0f} m)", "value": variant_id}
        for variant_id, sigma in VARIANT_SIGMAS.items()
    ]
    truth_control = (
        dcc.Checklist(
            id="truth-toggle",
            options=[{"label": "Show evaluation-only truth", "value": "show"}],
            value=["show"],
            className="truth-toggle",
        )
        if dataset.has_truth
        else html.P(
            "Public-only mode: evaluation truth and diagnostic files are not loaded.",
            className="boundary-note",
        )
    )
    return html.Div(
        [
            dcc.Store(id="dataset-key", data=dataset.source_path.name),
            dcc.Store(id="render-context", data=None),
            dcc.Store(id="camera-interacting", data=False),
            html.A("Skip to trajectory viewer", href="#viewer-main", className="skip-link"),
            html.Header(
                [
                    html.P("GEN-V1 · local ENU trajectory data", className="eyebrow"),
                    html.H2("Trajectory playback", className="page-title"),
                    html.P(
                        [
                            html.Span("Dataset ", className="muted-label"),
                            html.Code(dataset.source_path.name),
                            html.Span(
                                f" · {duration_label} s · {1 / OUTPUT_DT_S:g} Hz"
                                " · stored samples only"
                            ),
                        ],
                        className="subtitle",
                    ),
                ],
                className="masthead",
            ),
            html.Main(
                [
                    html.Section(
                        [
                            html.Div(
                                [
                                    html.H2("Playback controls", className="section-title"),
                                    html.Div(
                                        [
                                            html.Label(
                                                f"Episodes (up to {_MAX_COMPARED_EPISODES})",
                                                id="episode-select-label",
                                                htmlFor="episode-select",
                                            ),
                                            dcc.Dropdown(
                                                id="episode-select",
                                                options=episode_options,
                                                value=[dataset.episode_ids[0]],
                                                multi=True,
                                                clearable=False,
                                                disabled=episode_count == 1,
                                                className="episode-select",
                                                optionHeight=64,
                                                maxHeight=320,
                                            ),
                                            html.P(
                                                _episode_availability_message(episode_count),
                                                id="episode-availability",
                                                className="control-note",
                                            ),
                                        ],
                                        role="group",
                                        **{"aria-labelledby": "episode-select-label"},
                                    ),
                                    html.Div(
                                        id="selection-warning",
                                        className="control-note",
                                        **{"aria-live": "polite"},
                                    ),
                                ],
                                className="control-block control-episodes",
                            ),
                            html.Div(
                                [
                                    html.H2("Observation", className="section-title"),
                                    html.Div(
                                        [
                                            html.Label(
                                                "Position-noise variant",
                                                id="variant-select-label",
                                                htmlFor="variant-select",
                                            ),
                                            dcc.RadioItems(
                                                id="variant-select",
                                                options=variant_options,
                                                value="sigma_3m",
                                                inline=True,
                                                className="variant-select",
                                            ),
                                        ],
                                        role="group",
                                        **{"aria-labelledby": "variant-select-label"},
                                    ),
                                    truth_control,
                                ],
                                className="control-block",
                            ),
                            html.Div(
                                [
                                    html.H2("Time", className="section-title"),
                                    html.Div(
                                        [
                                            html.Button(
                                                "Play",
                                                id="play-toggle",
                                                className="button button-primary",
                                                **{"aria-label": "Start trajectory playback"},
                                            ),
                                            html.Div(
                                                dcc.RadioItems(
                                                    id="playback-speed",
                                                    options=[
                                                        {"label": f"{speed:g}×", "value": speed}
                                                        for speed in _SPEEDS
                                                    ],
                                                    value=1.0,
                                                    inline=True,
                                                    className="speed-select",
                                                ),
                                                role="group",
                                                **{"aria-label": "Playback speed"},
                                            ),
                                        ],
                                        className="time-actions",
                                    ),
                                    html.Div(
                                        [
                                            html.Label(
                                                "Stored trajectory step",
                                                id="time-step-label",
                                                htmlFor="step-slider",
                                            ),
                                            dcc.Slider(
                                                id="step-slider",
                                                min=0,
                                                max=sample_count - 1,
                                                step=1,
                                                value=0,
                                                marks=_slider_marks(sample_count),
                                                tooltip={
                                                    "placement": "bottom",
                                                    "always_visible": False,
                                                },
                                            ),
                                        ],
                                        role="group",
                                        **{"aria-labelledby": "time-step-label"},
                                    ),
                                    html.Div(
                                        _player_status(_DEFAULT_PLAYER_STATE, sample_count),
                                        id="playback-status",
                                        className="control-note",
                                        **{"aria-live": "polite"},
                                    ),
                                    dcc.Store(id="player-state", data=dict(_DEFAULT_PLAYER_STATE)),
                                    dcc.Interval(id="playback-tick", interval=200, disabled=True),
                                ],
                                className="control-block control-time",
                            ),
                            html.Div(
                                [
                                    html.H2("Output", className="section-title"),
                                    html.P(
                                        "Writes four selected PNGs to a new immutable "
                                        "visualization run.",
                                        className="output-copy",
                                    ),
                                    html.Button(
                                        "Export four-view PNG",
                                        id="export-png",
                                        className="button button-secondary",
                                    ),
                                    html.Div(
                                        id="export-status",
                                        className="control-note",
                                        **{"aria-live": "polite"},
                                    ),
                                ],
                                className="control-block control-output",
                            ),
                        ],
                        className="controls-grid",
                        **{"aria-label": "Trajectory viewer controls"},
                    ),
                    html.Section(
                        [
                            html.H2("Synchronized ENU views", className="visually-hidden"),
                            telemetry_panel(dataset),
                            html.Div(
                                [
                                    _graph_section(
                                        "figure-xy",
                                        "xy-view-heading",
                                        "XY 평면 · 수평 이동 (East–North)",
                                        initial_figures["xy"],
                                    ),
                                    _graph_section(
                                        "figure-xz",
                                        "xz-view-heading",
                                        "XZ 평면 · 고도 변화 (East–Up)",
                                        initial_figures["xz"],
                                    ),
                                    _graph_section(
                                        "figure-yz",
                                        "yz-view-heading",
                                        "YZ 평면 · 측면 이동 (North–Up)",
                                        initial_figures["yz"],
                                    ),
                                    _graph_section(
                                        "figure-3d",
                                        "three-d-view-heading",
                                        "3D ENU 공간",
                                        initial_figures["3d"],
                                    ),
                                ],
                                className="figure-grid",
                            ),
                        ],
                        className="viewer-panel",
                    ),
                ],
                id="viewer-main",
                className="viewer-main",
                tabIndex=-1,
            ),
        ],
        className="app-shell",
    )


def create_app(
    dataset_path: Path,
    *,
    public_only: bool = False,
    output_root: Path | None = None,
    generation_service: GenerationService | None = None,
) -> Dash:
    """Build a local-only generator/viewer starting with one explicit dataset path."""
    dataset = load_viewer_dataset(Path(dataset_path), public_only=public_only)
    exports_root = Path(output_root) if output_root is not None else _PROJECT_ROOT / "outputs"
    assets_folder = Path(__file__).resolve().parent / "assets"
    app = Dash(
        __name__,
        assets_folder=str(assets_folder),
        external_scripts=[],
        title="Trajectory data generator · v1",
        update_title=None,
    )
    jobs = generation_service or GenerationService(output_root=_PROJECT_ROOT / "outputs")
    registry = DatasetRegistry(dataset, public_only=public_only)
    registry.discover_saved(jobs.output_root)
    app.server.extensions["generation_service"] = jobs
    app.layout = html.Div(
        [
            html.Header(
                html.H1("데이터 생성 · 궤적 재생", className="page-title"),
                className="generation-masthead",
            ),
            generation_panel(jobs, registry),
            html.Div(id="dataset-load-error", className="dataset-load-error", role="alert"),
            html.Div(_viewer_layout(dataset), id="viewer-container"),
        ]
    )
    register_generation_callbacks(app, jobs, registry)

    @app.callback(
        Output("telemetry-content", "children"),
        Input("player-state", "data"),
        Input("episode-select", "value"),
        Input("dataset-key", "data"),
    )
    def render_telemetry(state, requested_ids, dataset_key):
        active = registry.get(dataset_key)
        selected, _ = _selected_episode_ids(requested_ids, active)
        step = _normalize_step(
            (state or _DEFAULT_PLAYER_STATE).get("step"), 0, active.sample_count(selected)
        )
        return telemetry_content(active, selected, step)

    @app.callback(
        Output("viewer-container", "children"),
        Output("dataset-load-error", "children"),
        Input("dataset-select", "value"),
        prevent_initial_call=True,
    )
    def select_dataset(dataset_key: str):
        try:
            return _viewer_layout(registry.get(dataset_key)), ""
        except (ValueError, OSError) as error:
            return no_update, f"데이터를 열 수 없습니다. 기존 재생 화면을 유지합니다: {error}"

    @app.callback(
        Output("player-state", "data"),
        Output("step-slider", "value"),
        Output("play-toggle", "children"),
        Output("play-toggle", "aria-label"),
        Output("playback-status", "children"),
        Output("step-slider", "max"),
        Output("step-slider", "marks"),
        Input("play-toggle", "n_clicks"),
        Input("step-slider", "value"),
        Input("playback-tick", "n_intervals"),
        Input("episode-select", "value"),
        Input("dataset-key", "data"),
        State("player-state", "data"),
        prevent_initial_call=True,
    )
    def update_player(
        _clicks: int | None,
        requested_step: Any,
        _ticks: int,
        requested_ids: Any,
        dataset_key: str,
        state: dict[str, int | bool] | None,
    ) -> tuple:
        """Advance the player through exact steps, including slider synchronization."""
        current = state or _DEFAULT_PLAYER_STATE
        active_dataset = registry.get(dataset_key)
        selected, _ = _selected_episode_ids(requested_ids, active_dataset)
        count = active_dataset.sample_count(selected)
        if _normalize_step(current.get("step"), -1, count) == -1:
            current = dict(_DEFAULT_PLAYER_STATE)
        trigger = ctx.triggered_id
        if trigger in {"episode-select", "dataset-key"}:
            next_state = dict(_DEFAULT_PLAYER_STATE)
        elif trigger == "play-toggle":
            action = "pause" if current.get("playing") else "play"
            next_state = advance_player(current, trigger=action, sample_count=count)
        elif trigger == "step-slider":
            next_state = advance_player(
                current,
                trigger="seek",
                requested_step=_normalize_step(requested_step, int(current["step"]), count),
                sample_count=count,
            )
        elif trigger == "playback-tick":
            next_state = advance_player(current, trigger="tick", sample_count=count)
        else:
            next_state = {"step": int(current["step"]), "playing": bool(current["playing"])}
        button_label = "Pause" if next_state["playing"] else "Play"
        button_aria = (
            "Pause trajectory playback" if next_state["playing"] else "Start trajectory playback"
        )
        return (
            next_state,
            int(next_state["step"]),
            button_label,
            button_aria,
            _player_status(next_state, count) + _timeline_warning(active_dataset, selected),
            count - 1,
            _slider_marks(count),
        )

    @app.callback(
        Output("playback-tick", "interval"),
        Output("playback-tick", "disabled"),
        Input("playback-speed", "value"),
        Input("player-state", "data"),
    )
    def update_tick(speed: float, state: dict[str, int | bool]) -> tuple[int, bool]:
        """Use 5 Hz as the 1× stored-sample cadence while stopped playback stays idle."""
        normalized_speed = float(speed) if speed in _SPEEDS else 1.0
        interval_ms = int(round(OUTPUT_DT_S * 1000 / normalized_speed))
        assert interval_ms > 0
        return interval_ms, not bool(state.get("playing"))

    render_inputs = [
        Input("episode-select", "value"),
        Input("variant-select", "value"),
        Input("player-state", "data"),
        Input("dataset-key", "data"),
        Input("camera-interacting", "data"),
    ]
    if dataset.has_truth:
        render_inputs.append(Input("truth-toggle", "value"))

    @app.callback(
        Output("figure-xy", "figure"),
        Output("figure-xz", "figure"),
        Output("figure-yz", "figure"),
        Output("figure-3d", "figure"),
        Output("selection-warning", "children"),
        Output("render-context", "data"),
        *render_inputs,
        State("render-context", "data"),
    )
    def render_figures(requested_ids, variant_id, state, dataset_key, camera_interacting, *extra):
        """One writer owns initialization, selection changes and marker patches."""
        active = registry.get(dataset_key)
        selected, warning = _selected_episode_ids(requested_ids, active)
        variant = variant_id if variant_id in VARIANT_SIGMAS else "sigma_3m"
        show_truth = bool(dataset.has_truth and extra[0] and "show" in extra[0])
        previous = extra[-1]
        context = dict(
            dataset=dataset_key, episodes=list(selected), variant=variant, truth=show_truth
        )
        current = state or _DEFAULT_PLAYER_STATE
        step = _normalize_step(current.get("step"), 0, active.sample_count(selected))
        if ctx.triggered_id in {"player-state", "camera-interacting"} and previous == context:
            updates = build_current_marker_extensions(
                active,
                episode_ids=selected,
                variant_id=variant,
                step=step,
                show_truth=show_truth,
            )
            figures = []
            for view in _VIEW_IDS:
                values, indices, _ = updates[view]
                patch = Patch()
                for coordinate, sequences in values.items():
                    for index, sequence in zip(indices, sequences, strict=True):
                        patch["data"][index][coordinate] = sequence
                figures.append(patch)
            if camera_interacting:
                figures[_VIEW_IDS.index("3d")] = no_update
        else:
            figures = _build_figures(
                active, selected, variant, {"step": step, "playing": False}, show_truth
            )
        return (*figures, warning + _timeline_warning(active, selected), context)

    def export_figures(
        _clicks: int,
        requested_ids: Any,
        variant_id: str,
        state: dict[str, int | bool],
        show_truth: bool,
        dataset_key: str,
    ) -> str:
        """Write the current four panels to a new output run and disclose the exact path."""
        dataset = registry.get(dataset_key)
        selected, warning = _selected_episode_ids(requested_ids, dataset)
        normalized_variant = variant_id if variant_id in VARIANT_SIGMAS else "sigma_3m"
        normalized_state = dict(state or _DEFAULT_PLAYER_STATE)
        normalized_state["step"] = _normalize_step(
            normalized_state.get("step"), 0, dataset.sample_count(selected)
        )
        figures = dict(
            zip(
                _VIEW_IDS,
                _build_figures(dataset, selected, normalized_variant, normalized_state, show_truth),
                strict=True,
            )
        )
        try:
            result = export_png_set(
                figures,
                dataset_path=dataset.source_path,
                episode_ids=selected,
                variant_id=normalized_variant,
                step=int(normalized_state["step"]),
                output_root=exports_root,
            )
        except Exception as error:
            return f"PNG export failed: {error}"
        prefix = f"{warning} " if warning else ""
        return f"{prefix}Exported four PNGs: {result.path}"

    if dataset.has_truth:

        @app.callback(
            Output("export-status", "children"),
            Input("export-png", "n_clicks"),
            State("episode-select", "value"),
            State("variant-select", "value"),
            State("player-state", "data"),
            State("truth-toggle", "value"),
            State("dataset-key", "data"),
            prevent_initial_call=True,
        )
        def export_evaluation_figures(
            clicks: int,
            requested_ids: Any,
            variant_id: str,
            state: dict[str, int | bool],
            truth_toggle: list[str] | None,
            dataset_key: str,
        ) -> str:
            return export_figures(
                clicks,
                requested_ids,
                variant_id,
                state,
                show_truth=bool(truth_toggle and "show" in truth_toggle),
                dataset_key=dataset_key,
            )

    else:

        @app.callback(
            Output("export-status", "children"),
            Input("export-png", "n_clicks"),
            State("episode-select", "value"),
            State("variant-select", "value"),
            State("player-state", "data"),
            State("dataset-key", "data"),
            prevent_initial_call=True,
        )
        def export_public_figures(
            clicks: int,
            requested_ids: Any,
            variant_id: str,
            state: dict[str, int | bool],
            dataset_key: str,
        ) -> str:
            return export_figures(
                clicks, requested_ids, variant_id, state, show_truth=False, dataset_key=dataset_key
            )

    return app
