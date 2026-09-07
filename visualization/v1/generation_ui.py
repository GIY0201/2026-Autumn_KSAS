"""Local job controls; motion generation remains behind the generator service API."""

from __future__ import annotations

from collections import OrderedDict

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

from data_generation.v1.service import MAX_SEED, GenerationJob, GenerationService

from .figures import ViewerDataset, load_viewer_dataset

_DATASET_CACHE_SIZE = 2
_JOB_POLL_MS = 1000
_PHASE_LABELS = {
    "starting": "작업 시작 중…",
    "simulating": "운동 궤적 계산 중…",
    "source_check": "TRAIN 원본 운동 점검 중…",
    "writing": "CSV와 실행 기록 저장 중…",
    "validating": "저장된 데이터 형식 확인 중…",
    "finishing": "생성 worker 종료 확인 중…",
    "complete": "생성 완료 — 결과 열기를 누르면 새 데이터를 재생합니다.",
    "failed": "생성 실패 — 결과를 완료 데이터로 등록하지 않았습니다.",
    "cancelled": "생성 중단 — 기존 데이터는 그대로 보존됩니다.",
}


class DatasetRegistry:
    """Resolve only explicit input/results; selection stays in each browser's Store."""

    def __init__(self, initial: ViewerDataset, *, public_only: bool) -> None:
        key = initial.source_path.name
        self.initial_key = key
        self.public_only = public_only
        self._paths = {key: initial.source_path}
        self._labels = {key: f"처음 연 데이터 · {len(initial.episode_ids)}개 · {key}"}
        self._cache = OrderedDict({key: initial})

    def add_results(self, jobs: tuple[GenerationJob, ...]) -> None:
        """Register only service-validated completed datasets, without selecting one."""
        for job in jobs:
            if job.state != "complete" or job.result_path is None:
                continue
            key = job.result_path.name
            self._paths[key] = job.result_path
            self._labels[key] = f"{job.label} · {job.total}개 · seed {job.seed} · {key}"

    @property
    def options(self) -> list[dict[str, str]]:
        return [{"label": label, "value": key} for key, label in self._labels.items()]

    def get(self, key: str) -> ViewerDataset:
        """Validate loaded contents and bound memory without a shared current selection."""
        if not isinstance(key, str) or key not in self._paths:
            raise ValueError("unknown dataset selection")
        if key not in self._cache:
            self._cache[key] = load_viewer_dataset(self._paths[key], public_only=self.public_only)
            if len(self._cache) > _DATASET_CACHE_SIZE:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return self._cache[key]


def generation_panel(service: GenerationService, registry: DatasetRegistry) -> html.Section:
    """Provide a labelled, keyboard-accessible request form and actual job status."""
    preset = service.presets[0]
    return html.Section(
        [
            html.H2("데이터 생성", className="section-title", id="generation-heading"),
            html.P(
                "설정과 seed를 선택해 새 궤적을 만드세요. "
                "생성 중에도 아래 데이터를 재생할 수 있습니다.",
                className="generation-intro",
            ),
            html.Details(
                [
                    html.Summary("객체별 지원 상태 · v1"),
                    html.Ul(
                        [
                            html.Li(
                                f"{item.label}: "
                                + (
                                    "생성 가능"
                                    if item.available
                                    else f"생성 불가 — {item.unavailable_reason}"
                                )
                            )
                            for item in service.object_support
                        ]
                    ),
                ],
                id="generation-object-support",
                className="control-note",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label(
                                [
                                    html.Span("객체 · 생성 설정", className="dropdown-label-text"),
                                    dcc.Dropdown(
                                        id="generation-preset",
                                        clearable=False,
                                        options=[
                                            {"label": item.label, "value": item.preset_id}
                                            for item in service.presets
                                        ],
                                        value=preset.preset_id,
                                    ),
                                ],
                                className="dropdown-label",
                            ),
                            html.P(id="generation-description", className="control-note"),
                        ],
                        className="generation-preset-field",
                    ),
                    html.Div(
                        [
                            html.Label("Seed (새 움직임을 만드는 정수)", htmlFor="generation-seed"),
                            dcc.Input(
                                id="generation-seed",
                                name="generation-seed",
                                type="number",
                                min=0,
                                max=MAX_SEED,
                                step=1,
                                value=preset.default_seed,
                                autoComplete="off",
                                className="generation-seed-input",
                            ),
                            html.P(
                                "같은 seed·설정이면 같은 궤적, 저장 폴더는 매번 새 ID입니다.",
                                className="control-note",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Button(
                                "데이터 생성",
                                id="generation-start",
                                disabled=False,
                                className="button button-primary",
                            ),
                            html.Button(
                                "생성 중단",
                                id="generation-cancel",
                                disabled=True,
                                className="button button-secondary",
                            ),
                        ],
                        className="generation-actions",
                    ),
                ],
                className="generation-fields",
            ),
            html.Div(
                id="generation-action-feedback", className="generation-feedback", role="alert"
            ),
            html.Div(
                [
                    html.Div(
                        "대기 중 — 아직 생성 작업을 시작하지 않았습니다.",
                        id="generation-status",
                        **{"aria-live": "polite"},
                    ),
                    html.Progress(
                        id="generation-progress",
                        value=0,
                        max=preset.episode_count,
                        **{"aria-label": "계산한 에피소드 수"},
                    ),
                    html.Button(
                        "생성 결과 열기",
                        id="generation-open",
                        disabled=True,
                        className="button button-secondary",
                    ),
                ],
                className="generation-job-row",
            ),
            html.Div(
                [
                    html.Label(
                        [
                            html.Span(
                                "재생할 데이터 (처음 연 데이터 + 이번 서버에서 생성한 결과)",
                                className="dropdown-label-text",
                            ),
                            dcc.Dropdown(
                                id="dataset-select",
                                options=registry.options,
                                value=registry.initial_key,
                                clearable=False,
                            ),
                        ],
                        className="dropdown-label",
                    ),
                ],
                className="dataset-selection",
            ),
            dcc.Store(id="generation-job"),
            dcc.Store(id="generation-refresh", data=0),
            dcc.Interval(id="generation-poll", interval=_JOB_POLL_MS),
        ],
        className="generation-panel",
        **{"aria-labelledby": "generation-heading"},
    )


def register_generation_callbacks(
    app: Dash,
    service: GenerationService,
    registry: DatasetRegistry,
) -> None:
    """Keep background polling independent of playback ticks and selected figures."""

    @app.callback(
        Output("generation-description", "children"),
        Input("generation-preset", "value"),
    )
    def describe_preset(preset_id):
        preset = next((item for item in service.presets if item.preset_id == preset_id), None)
        return (
            f"{preset.description} 생성량: {preset.episode_count}개."
            if preset
            else "지원하는 생성 설정을 선택하세요."
        )

    @app.callback(
        Output("generation-action-feedback", "children"),
        Output("generation-refresh", "data"),
        Output("dataset-select", "value"),
        Input("generation-start", "n_clicks"),
        Input("generation-cancel", "n_clicks"),
        Input("generation-open", "n_clicks"),
        State("generation-preset", "value"),
        State("generation-seed", "value"),
        State("generation-job", "data"),
        State("generation-refresh", "data"),
        prevent_initial_call=True,
    )
    def act(_start, _cancel, _open, preset_id, seed, displayed_job, refresh):
        trigger = ctx.triggered_id
        try:
            if trigger == "generation-start":
                service.submit(preset_id, seed)
            elif trigger == "generation-cancel":
                current = service.snapshot()
                if current is None or current.job_id != (displayed_job or {}).get("job_id"):
                    raise ValueError("작업 상태가 바뀌었습니다. 현재 상태를 확인한 뒤 중단하세요.")
                service.cancel()
            elif trigger == "generation-open":
                completed = next(
                    (
                        job
                        for job in service.results
                        if job.job_id == (displayed_job or {}).get("job_id")
                    ),
                    None,
                )
                if completed is None or completed.result_path is None:
                    raise ValueError("완료된 생성 결과가 없습니다. 작업 상태를 확인하세요.")
                registry.add_results(service.results)
                return "", refresh + 1, completed.result_path.name
            return "", refresh + 1, no_update
        except (ValueError, RuntimeError, OSError) as error:
            return f"요청을 처리하지 못했습니다: {error}", refresh + 1, no_update

    @app.callback(
        Output("generation-status", "children"),
        Output("generation-progress", "value"),
        Output("generation-progress", "max"),
        Output("generation-start", "disabled"),
        Output("generation-cancel", "disabled"),
        Output("generation-open", "disabled"),
        Output("generation-job", "data"),
        Output("dataset-select", "options"),
        Input("generation-poll", "n_intervals"),
        Input("generation-refresh", "data"),
        State("dataset-select", "options"),
    )
    def poll(_ticks, _refresh, previous_options):
        job = service.snapshot()
        registry.add_results(service.results)
        options = registry.options if registry.options != previous_options else no_update
        if job is None:
            return (
                "대기 중 — 아직 생성 작업을 시작하지 않았습니다.",
                0,
                service.presets[0].episode_count,
                False,
                True,
                True,
                None,
                options,
            )
        status = [
            html.Strong(_PHASE_LABELS.get(job.phase, job.phase)),
            html.Span(
                f" {job.label} · seed {job.seed} · 계산한 에피소드 {job.completed} / {job.total}"
            ),
        ]
        if job.result_path is not None:
            status.append(html.Code(str(job.result_path), className="job-result-path"))
        if job.message or job.state == "failed":
            status.extend([html.P(job.message), html.Code(f"로그: {job.work_path / 'worker.log'}")])
        running = job.state == "running"
        return (
            status,
            job.completed,
            job.total,
            running,
            not running,
            job.state != "complete",
            {"job_id": job.job_id, "state": job.state},
            options,
        )
