"""Local Dash application for synchronized v1 trajectory playback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

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

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VIEW_IDS = ("xy", "xz", "yz", "3d")
_SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
_MAX_COMPARED_EPISODES = 4
_DEFAULT_PLAYER_STATE = {"step": 0, "playing": False}
_GRAPH_CANVAS_HEIGHT = "clamp(22rem, 36vw, 34rem)"

assert SAMPLE_COUNT == 301
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


def _normalize_step(value: Any, fallback: int) -> int:
    """Accept only a stored integer sample index from Dash component state."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and 0 <= value < SAMPLE_COUNT:
        return value
    if isinstance(value, float) and value.is_integer() and 0 <= int(value) < SAMPLE_COUNT:
        return int(value)
    return fallback


def _player_status(state: dict[str, int | bool]) -> str:
    """Describe current stored time and playback state without implying interpolation."""
    step = int(state["step"])
    phase = "Playing" if state["playing"] else "Paused"
    return f"{phase}. Stored step {step} of {SAMPLE_COUNT - 1}; t = {step * OUTPUT_DT_S:.1f} s."


def _build_figures(
    dataset: ViewerDataset,
    selected: tuple[str, ...],
    variant_id: str,
    state: dict[str, int | bool],
    show_truth: bool,
) -> tuple:
    """Build all panels from one exact selection and one shared integer time step."""
    step = int(state["step"])
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


def _graph_section(figure_id: str, heading_id: str, title: str) -> html.Section:
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
    episode_options = [
        {
            "label": dataset.episode_option_label(episode.episode_id),
            "value": episode.episode_id,
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
            html.A("Skip to trajectory viewer", href="#viewer-main", className="skip-link"),
            html.Header(
                [
                    html.P("GEN-V1 · local ENU trajectory data", className="eyebrow"),
                    html.H2("Trajectory playback", className="page-title"),
                    html.P(
                        [
                            html.Span("Dataset ", className="muted-label"),
                            html.Code(dataset.source_path.name),
                            html.Span(" · 60 s · 5 Hz · stored samples only"),
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
                                                max=SAMPLE_COUNT - 1,
                                                step=1,
                                                value=0,
                                                marks={
                                                    0: "0 s",
                                                    75: "15 s",
                                                    150: "30 s",
                                                    225: "45 s",
                                                    300: "60 s",
                                                },
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
                                        _player_status(_DEFAULT_PLAYER_STATE),
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
                            html.Div(
                                [
                                    _graph_section(
                                        "figure-xy",
                                        "xy-view-heading",
                                        "XY 평면 · 수평 이동 (East–North)",
                                    ),
                                    _graph_section(
                                        "figure-xz",
                                        "xz-view-heading",
                                        "XZ 평면 · 고도 변화 (East–Up)",
                                    ),
                                    _graph_section(
                                        "figure-yz",
                                        "yz-view-heading",
                                        "YZ 평면 · 측면 이동 (North–Up)",
                                    ),
                                    _graph_section(
                                        "figure-3d", "three-d-view-heading", "3D ENU 공간"
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
        Input("play-toggle", "n_clicks"),
        Input("step-slider", "value"),
        Input("playback-tick", "n_intervals"),
        State("player-state", "data"),
        prevent_initial_call=True,
    )
    def update_player(
        _clicks: int | None,
        requested_step: Any,
        _ticks: int,
        state: dict[str, int | bool] | None,
    ) -> tuple[dict[str, int | bool], int, str, str, str]:
        """Advance the player through exact steps, including slider synchronization."""
        current = state or _DEFAULT_PLAYER_STATE
        trigger = ctx.triggered_id
        if trigger == "play-toggle":
            action = "pause" if current.get("playing") else "play"
            next_state = advance_player(current, trigger=action)
        elif trigger == "step-slider":
            next_state = advance_player(
                current,
                trigger="seek",
                requested_step=_normalize_step(requested_step, int(current["step"])),
            )
        elif trigger == "playback-tick":
            next_state = advance_player(current, trigger="tick")
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
            _player_status(next_state),
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

    def render_figures(
        requested_ids: Any,
        variant_id: str,
        state: dict[str, int | bool],
        show_truth: bool,
        dataset_key: str,
    ) -> tuple:
        """Render every panel from a shared, validated viewer state."""
        dataset = registry.get(dataset_key)
        selected, warning = _selected_episode_ids(requested_ids, dataset)
        normalized_variant = variant_id if variant_id in VARIANT_SIGMAS else "sigma_3m"
        normalized_state = state or _DEFAULT_PLAYER_STATE
        figures = _build_figures(
            dataset, selected, normalized_variant, normalized_state, show_truth
        )
        return (*figures, warning)

    if dataset.has_truth:

        @app.callback(
            Output("figure-xy", "figure"),
            Output("figure-xz", "figure"),
            Output("figure-yz", "figure"),
            Output("figure-3d", "figure"),
            Output("selection-warning", "children"),
            Input("episode-select", "value"),
            Input("variant-select", "value"),
            Input("truth-toggle", "value"),
            State("player-state", "data"),
            State("dataset-key", "data"),
        )
        def render_evaluation_figures(
            requested_ids: Any,
            variant_id: str,
            truth_toggle: list[str] | None,
            state: dict[str, int | bool],
            dataset_key: str,
        ) -> tuple:
            return render_figures(
                requested_ids,
                variant_id,
                state,
                show_truth=bool(truth_toggle and "show" in truth_toggle),
                dataset_key=dataset_key,
            )

    else:

        @app.callback(
            Output("figure-xy", "figure"),
            Output("figure-xz", "figure"),
            Output("figure-yz", "figure"),
            Output("figure-3d", "figure"),
            Output("selection-warning", "children"),
            Input("episode-select", "value"),
            Input("variant-select", "value"),
            State("player-state", "data"),
            State("dataset-key", "data"),
        )
        def render_public_figures(
            requested_ids: Any,
            variant_id: str,
            state: dict[str, int | bool],
            dataset_key: str,
        ) -> tuple:
            return render_figures(
                requested_ids, variant_id, state, show_truth=False, dataset_key=dataset_key
            )

    def stream_current_markers(
        state: dict[str, int | bool] | None,
        requested_ids: Any,
        variant_id: str,
        show_truth: bool,
        dataset_key: str,
    ) -> tuple:
        """Send only the current marker coordinates for a stored playback step."""
        dataset = registry.get(dataset_key)
        selected, _ = _selected_episode_ids(requested_ids, dataset)
        normalized_variant = variant_id if variant_id in VARIANT_SIGMAS else "sigma_3m"
        normalized_state = state or _DEFAULT_PLAYER_STATE
        extensions = build_current_marker_extensions(
            dataset,
            episode_ids=selected,
            variant_id=normalized_variant,
            step=_normalize_step(normalized_state.get("step"), 0),
            show_truth=show_truth,
        )
        return tuple(extensions[view] for view in _VIEW_IDS)

    if dataset.has_truth:

        @app.callback(
            Output("figure-xy", "extendData"),
            Output("figure-xz", "extendData"),
            Output("figure-yz", "extendData"),
            Output("figure-3d", "extendData"),
            Input("player-state", "data"),
            State("episode-select", "value"),
            State("variant-select", "value"),
            State("truth-toggle", "value"),
            State("dataset-key", "data"),
            prevent_initial_call=True,
        )
        def stream_evaluation_markers(
            state: dict[str, int | bool] | None,
            requested_ids: Any,
            variant_id: str,
            truth_toggle: list[str] | None,
            dataset_key: str,
        ) -> tuple:
            return stream_current_markers(
                state,
                requested_ids,
                variant_id,
                show_truth=bool(truth_toggle and "show" in truth_toggle),
                dataset_key=dataset_key,
            )

    else:

        @app.callback(
            Output("figure-xy", "extendData"),
            Output("figure-xz", "extendData"),
            Output("figure-yz", "extendData"),
            Output("figure-3d", "extendData"),
            Input("player-state", "data"),
            State("episode-select", "value"),
            State("variant-select", "value"),
            State("dataset-key", "data"),
            prevent_initial_call=True,
        )
        def stream_public_markers(
            state: dict[str, int | bool] | None,
            requested_ids: Any,
            variant_id: str,
            dataset_key: str,
        ) -> tuple:
            return stream_current_markers(
                state,
                requested_ids,
                variant_id,
                show_truth=False,
                dataset_key=dataset_key,
            )

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
        normalized_state = state or {"step": 0, "playing": False}
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
