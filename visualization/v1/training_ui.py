"""Service-driven training console; model execution stays in the worker."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import PureWindowsPath

import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from plotly.subplots import make_subplots

from .training_diagnostics import (
    diagnostic_panel,
    register_diagnostic_callbacks,
)


def _flatten(config, prefix=""):
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _flatten(value, path)
        else:
            yield path, value


def merge_parameters(config, identifiers, values):
    """Copy a preset and apply explicit form values without mutating defaults."""
    result = deepcopy(config)
    for identifier, value in zip(identifiers, values, strict=True):
        parts = identifier["path"].split(".")
        branch = result
        for part in parts[:-1]:
            branch = branch[part]
        old = branch[parts[-1]]
        if value is None:
            raise ValueError(f"{identifier['path']}: 값이 필요합니다.")
        if isinstance(old, list):
            value = json.loads(value) if isinstance(value, str) else value
            if not isinstance(value, list):
                raise ValueError(f"{identifier['path']}: JSON 배열을 입력하세요.")
        elif isinstance(old, bool):
            value = value == "true"
        elif isinstance(old, int):
            if float(value) != int(value):
                raise ValueError(f"{identifier['path']}: 정수를 입력하세요.")
            value = int(value)
        elif isinstance(old, float):
            value = float(value)
        branch[parts[-1]] = value
    return result


def display_value(key, value):
    """Keep table values readable without changing recorded precision."""
    if value is None:
        return "—"
    if key == "dataset_path":
        return PureWindowsPath(str(value)).name
    if key in {"elapsed_s", "eta_s"} and isinstance(value, (int, float)):
        seconds = max(0, int(value))
        return f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}"
    if key == "created_at":
        try:
            timestamp = datetime.fromisoformat(str(value))
            return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        except ValueError:
            return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def metric_figure(rows, keys, title):
    """Plot independent scalar series; missing observations stay missing."""
    figure = go.Figure()
    normalized = [
        {
            **row,
            **{
                key.removeprefix("eval_"): value
                for key, value in row.items()
                if key.startswith("eval_")
            },
        }
        for row in rows
    ]
    for key in keys:
        for split, lineage in dict.fromkeys(
            (row.get("split", "train"), row.get("lineage", "")) for row in normalized
        ):
            points = [
                r
                for r in normalized
                if r.get("split", "train") == split
                and r.get("lineage", "") == lineage
                and isinstance(r.get(key), (int, float))
            ]
            if points:
                figure.add_scatter(
                    x=[r.get("global_step", r.get("step", r.get("epoch"))) for r in points],
                    y=[r[key] for r in points],
                    mode="lines+markers",
                    name=f"{lineage + ' / ' if lineage else ''}{split} / {key}",
                )
    markers = {
        row["resume_global_step"] for row in rows if row.get("resume_global_step") is not None
    }
    for marker in sorted(markers):
        figure.add_vline(x=marker, line_dash="dash", line_color="#777", annotation_text="Resume")
    figure.update_layout(
        template="plotly_white",
        title=title,
        xaxis_title="Global step",
        margin=dict(l=55, r=15, t=45, b=45),
        legend=dict(orientation="h"),
        uirevision=title,
    )
    return figure


def prediction_figure(result):
    """Display only trajectories supplied by the prediction service."""
    if "data" in result and "layout" in result:
        return go.Figure(result)
    figure = go.Figure()
    for key, label, dash in (
        ("history", "입력 관측", "solid"),
        ("observed", "미래 관측", "dash"),
        ("predicted", "GRU 예측", "solid"),
    ):
        points = result.get(key, [])
        if len(points):
            figure.add_scatter3d(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                z=[p[2] for p in points],
                mode="lines",
                name=label,
                line=dict(dash=dash),
            )
    figure.update_layout(
        template="plotly_white",
        scene=dict(
            aspectmode="data", xaxis_title="East (m)", yaxis_title="North (m)", zaxis_title="Up (m)"
        ),
        margin=dict(l=0, r=0, t=25, b=0),
    )
    return figure


def runtime_figure(rows):
    """Keep optimizer, memory and throughput units on independent axes."""
    series = (
        ("learning_rate", "Learning rate"),
        ("gpu_memory_mb", "GPU memory (MB)"),
        ("samples_per_second", "Samples / s"),
        ("grad_norm", "Gradient norm"),
    )
    figure = make_subplots(
        rows=len(series), cols=1, shared_xaxes=True, subplot_titles=[title for _, title in series]
    )
    for index, (key, title) in enumerate(series, 1):
        points = [r for r in rows if isinstance(r.get(key), (int, float))]
        figure.add_trace(
            go.Scatter(
                x=[r.get("global_step", r.get("step", r.get("epoch"))) for r in points],
                y=[r[key] for r in points],
                name=title,
                mode="lines",
            ),
            row=index,
            col=1,
        )
    figure.update_layout(
        template="plotly_white",
        height=580,
        showlegend=False,
        margin=dict(l=65, r=15, t=35, b=35),
        uirevision="runtime",
    )
    figure.update_xaxes(title_text="Global step", row=len(series), col=1)
    return figure


def _field(label, control):
    return html.Div(
        [
            html.Label(
                label,
                htmlFor=control.id
                if isinstance(control.id, str)
                else json.dumps(control.id, sort_keys=True, separators=(",", ":")),
            ),
            control,
        ],
        className="training-field",
    )


def training_panel(service):
    plugins = service.plugins()
    return html.Section(
        [
            dcc.Store(id="training-config"),
            dcc.Store(id="training-active"),
            dcc.Interval(id="training-tick", interval=3000),
            html.Div(
                [
                    html.H2("Training"),
                    html.Div(
                        "실행을 선택하거나 새 학습을 시작하세요.",
                        id="training-status",
                        role="status",
                    ),
                    html.Button("학습 중지", id="training-stop", disabled=True),
                ],
                className="training-statusbar",
            ),
            html.Div(id="training-message", role="alert"),
            html.Details(
                [
                    html.Summary("모델 · 학습 설정"),
                    html.Div(
                        [
                            _field(
                                "모델 이름",
                                dcc.Input(id="training-name", type="text", placeholder="실험 이름"),
                            ),
                            _field(
                                "모델",
                                dcc.Dropdown(
                                    id="training-plugin",
                                    options=[
                                        {"label": p.get("label", p["id"]), "value": p["id"]}
                                        for p in plugins
                                    ],
                                    value=plugins[0]["id"] if plugins else None,
                                    clearable=False,
                                ),
                            ),
                            _field(
                                "Dataset",
                                dcc.Dropdown(
                                    id="training-dataset",
                                    options=service.datasets(),
                                    clearable=False,
                                ),
                            ),
                            _field(
                                "저장된 parameter",
                                dcc.Dropdown(id="training-preset", placeholder="기본 설정"),
                            ),
                        ],
                        className="training-fields",
                    ),
                    html.Pre(id="training-contract", className="training-contract"),
                    html.Div(id="training-parameters", className="training-fields"),
                    html.Div(
                        [
                            dcc.Input(id="training-preset-name", placeholder="Parameter 저장 이름"),
                            html.Button("Parameter 저장", id="training-save"),
                            html.Button("새 학습 시작", id="training-start"),
                        ],
                        className="training-actions",
                    ),
                ],
                id="training-settings",
                open=True,
            ),
            dcc.Graph(
                id="training-loss", figure=metric_figure([], ["loss"], "Loss"), responsive=True
            ),
            html.Div(
                [
                    dcc.Graph(id="training-errors", responsive=True),
                    dcc.Graph(id="training-horizons", responsive=True),
                    dcc.Graph(id="training-axes", responsive=True),
                    dcc.Graph(id="training-runtime", responsive=True),
                ],
                className="training-graphs",
            ),
            html.Details([html.Summary("최근 log"), html.Pre(id="training-logs")]),
            html.H3("저장된 모델"),
            html.Div(id="training-runs", className="training-table-wrap"),
            html.Div(
                [
                    _field(
                        "실행",
                        dcc.Dropdown(id="training-run", placeholder="실행을 명시적으로 선택하세요"),
                    ),
                    _field(
                        "Checkpoint",
                        dcc.Dropdown(
                            id="training-checkpoint",
                            options=["best", "last"],
                            value="last",
                            clearable=False,
                        ),
                    ),
                    html.Button("설정 보기", id="training-inspect"),
                    html.Button("이어서 학습", id="training-resume"),
                    html.Button("예측 경로 확인", id="training-predict"),
                ],
                className="training-fields",
            ),
            html.Pre(id="training-inspection"),
            dcc.Graph(id="training-prediction", figure=prediction_figure({}), responsive=True),
            diagnostic_panel(),
        ],
        className="training-console",
    )


def register_training_callbacks(app, service):
    register_diagnostic_callbacks(app, service)

    @app.callback(
        Output("training-parameters", "children"),
        Output("training-config", "data"),
        Output("training-contract", "children"),
        Output("training-dataset", "value"),
        Input("training-plugin", "value"),
        Input("training-preset", "value"),
        Input("training-inspect", "n_clicks"),
        State("training-run", "value"),
    )
    def parameters(plugin_id, preset_id, _inspect=None, selected=None):
        plugin = next((p for p in service.plugins() if p["id"] == plugin_id), None)
        if plugin is None:
            return [], {}, "모델을 선택하세요.", no_update
        presets = service.presets(plugin_id)
        config = next((p["config"] for p in presets if p["id"] == preset_id), plugin["defaults"])
        if _inspect and selected and ctx.triggered_id == "training-inspect":
            config = service.status(selected)["config"]
            if config["plugin_id"] != plugin_id:
                return (
                    [],
                    {},
                    "저장된 실행과 같은 모델을 선택한 뒤 설정 보기를 누르세요.",
                    no_update,
                )
        fields = []
        for path, value in _flatten(config):
            if (
                value is None
                or path in {"data.dataset_path", "plugin_id"}
            ):
                continue
            identifier = {"type": "training-parameter", "path": path}
            spec = plugin.get("parameter_schema", {}).get(path, {})
            if "enum" in spec:
                control = dcc.Dropdown(
                    id=identifier,
                    options=spec["enum"],
                    value=value,
                    clearable=False,
                    disabled=len(spec["enum"]) == 1,
                )
            elif isinstance(value, bool):
                control = dcc.Dropdown(
                    id=identifier,
                    options=["true", "false"],
                    value=str(value).lower(),
                    clearable=False,
                )
            else:
                control = dcc.Input(
                    id=identifier,
                    value=json.dumps(value) if isinstance(value, list) else value,
                    type="number" if isinstance(value, (int, float)) else "text",
                    min=spec.get("minimum"),
                    max=spec.get("maximum"),
                    step=1 if isinstance(value, int) else "any",
                    debounce=True,
                )
            fields.append(_field(path, control))
        return (
            fields,
            config,
            json.dumps(
                plugin.get("prediction_contract", plugin.get("contract", {})),
                ensure_ascii=False,
                indent=2,
            ),
            config.get("data", {}).get("dataset_path", no_update),
        )

    @app.callback(
        Output("training-preset", "options"),
        Input("training-tick", "n_intervals"),
        Input("training-plugin", "value"),
    )
    def preset_options(_tick, plugin_id):
        return (
            [
                {"label": p.get("name", p["id"]), "value": p["id"]}
                for p in service.presets(plugin_id)
            ]
            if plugin_id
            else []
        )

    @app.callback(
        Output("training-message", "children"),
        Output("training-active", "data"),
        Output("training-settings", "open"),
        Output("training-run", "value"),
        Input("training-start", "n_clicks"),
        Input("training-stop", "n_clicks"),
        Input("training-save", "n_clicks"),
        Input("training-resume", "n_clicks"),
        State("training-config", "data"),
        State({"type": "training-parameter", "path": ALL}, "id"),
        State({"type": "training-parameter", "path": ALL}, "value"),
        State("training-plugin", "value"),
        State("training-dataset", "value"),
        State("training-name", "value"),
        State("training-preset-name", "value"),
        State("training-active", "data"),
        State("training-run", "value"),
        State("training-checkpoint", "value"),
        prevent_initial_call=True,
    )
    def action(
        _start,
        _stop,
        _save,
        _resume,
        config,
        identifiers,
        values,
        plugin,
        dataset,
        name,
        preset_name,
        active,
        selected,
        checkpoint,
    ):
        try:
            if ctx.triggered_id == "training-stop":
                stop_target = active or selected
                if not stop_target:
                    raise ValueError("중지할 실행을 선택하세요.")
                service.stop(stop_target)
                return (
                    "중지를 요청했습니다. Checkpoint 저장을 기다리세요.",
                    stop_target,
                    no_update,
                    no_update,
                )
            config = merge_parameters(config, identifiers, values)
            config["plugin_id"] = plugin
            config.setdefault("data", {})["dataset_path"] = dataset
            if ctx.triggered_id == "training-save":
                service.save_preset(preset_name, config)
                return "Parameter를 새 revision으로 저장했습니다.", no_update, no_update, no_update
            if not name or not dataset:
                raise ValueError("모델 이름과 Dataset을 입력하세요.")
            parent = None
            if ctx.triggered_id == "training-resume":
                if not selected:
                    raise ValueError("부모 실행과 Checkpoint를 선택하세요.")
                parent = f"{selected}/{checkpoint}"
            run_id = service.start(config, name, parent_checkpoint=parent)
            return f"학습 시작: {run_id}", run_id, False, run_id
        except Exception as error:
            return f"실행하지 못했습니다: {error}", no_update, no_update, no_update

    @app.callback(
        Output("training-runs", "children"),
        Output("training-run", "options"),
        Output("training-status", "children"),
        Output("training-stop", "disabled"),
        Output("training-loss", "figure"),
        Output("training-errors", "figure"),
        Output("training-horizons", "figure"),
        Output("training-axes", "figure"),
        Output("training-runtime", "figure"),
        Output("training-logs", "children"),
        Input("training-tick", "n_intervals"),
        Input("training-active", "data"),
        Input("training-run", "value"),
    )
    def refresh(_tick, active, selected):
        try:
            runs = service.runs()
            run_id = selected or active
            status = service.status(run_id) if run_id else {}
            active_status = service.status(active) if active and active != run_id else status
            rows = service.metrics(run_id) if run_id else []
            if status.get("parent_run"):
                resume_step = status.get("resume_global_step")
                parent_rows = service.metrics(status["parent_run"])
                if resume_step is not None:
                    parent_rows = [
                        row
                        for row in parent_rows
                        if row.get("global_step", row.get("step", 0)) <= resume_step
                    ]
                rows = [{**row, "lineage": "parent"} for row in parent_rows] + [
                    {**row, "lineage": "child", "resume_global_step": resume_step} for row in rows
                ]
            columns = [
                ("display_name", "이름"),
                ("plugin_id", "모델"),
                ("dataset_path", "Dataset"),
                ("status", "상태"),
                ("best_ade_m", "Best ADE (m)"),
                ("epoch", "Epoch"),
                ("created_at", "생성 시각"),
                ("parent_run", "부모 실행"),
            ]
            table = html.Table(
                [
                    html.Thead(html.Tr([html.Th(label) for _, label in columns])),
                    html.Tbody(
                        [
                            html.Tr(
                                [
                                    html.Td(
                                        display_value(key, run.get(key)),
                                        title=str(run.get(key, "")),
                                    )
                                    for key, _ in columns
                                ]
                            )
                            for run in runs
                        ]
                    ),
                ]
            )
            summary = " | ".join(
                f"{k}: {display_value(k, status[k])}"
                for k in ("display_name", "status", "epoch", "device", "elapsed_s", "eta_s")
                if k in status
            )
            message = summary or "저장된 실행을 선택하거나 새 학습을 시작하세요."
        except Exception as error:
            table, runs, rows, status, active_status = (
                html.Div(str(error), role="alert"),
                [],
                [],
                {},
                {},
            )
            message = f"학습 상태 조회 실패: {error}"
        return (
            table,
            [
                {
                    "label": f"{r.get('display_name', r['run_id'])} / {r['run_id']}",
                    "value": r["run_id"],
                }
                for r in runs
            ],
            message,
            not (active or selected) or active_status.get("status") not in {"running", "queued"},
            metric_figure(rows, ["loss"], "Train / validation loss"),
            metric_figure(rows, ["ade_m", "fde_m"], "ADE / FDE (m)"),
            metric_figure(
                rows, [f"error_{h}s_m" for h in (1, 3, 5, 10, 15)], "예측 시간별 오차 (m)"
            ),
            metric_figure(rows, [f"axis_rmse_{a}_m" for a in "xyz"], "축별 RMSE (m)"),
            runtime_figure(rows),
            str(status.get("log_tail", "")),
        )

    @app.callback(
        Output("training-inspection", "children"),
        Output("training-prediction", "figure"),
        Input("training-inspect", "n_clicks"),
        Input("training-predict", "n_clicks"),
        State("training-run", "value"),
        State("training-checkpoint", "value"),
        prevent_initial_call=True,
    )
    def inspect(_inspect, _predict, run_id, checkpoint):
        try:
            if not run_id:
                raise ValueError("실행을 먼저 선택하세요.")
            if ctx.triggered_id == "training-predict":
                return f"{run_id} / {checkpoint}", prediction_figure(
                    service.predict(run_id, checkpoint=checkpoint)
                )
            return json.dumps(service.status(run_id), ensure_ascii=False, indent=2), no_update
        except Exception as error:
            return f"조회 실패: {error}", no_update
