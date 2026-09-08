"""Saved validation-window explorer; never performs training or inference."""

from __future__ import annotations

import json

import plotly.graph_objects as go
from dash import Input, Output, dcc, html


def filter_diagnostic_windows(rows, vertical, xy_movement, sort_by):
    """Filter observation-derived labels and keep descending error order deterministic."""
    if sort_by not in {"axis_rmse_z_m", "ade_m"}:
        raise ValueError("Unsupported diagnostic sort")
    selected = [row for row in rows
                if (not vertical or row["observed_vertical"] == vertical)
                and (not xy_movement or row["observed_xy_movement"] == xy_movement)]
    return sorted(selected, key=lambda row: (-row[sort_by], row["sequence_id"]))


def diagnostic_figures(detail):
    """Use the saved point timestamps and positions without interpolation."""
    spatial, altitude = go.Figure(), go.Figure()
    anchor = detail.get("window", {}).get("anchor_t_s", 0)
    for role, prefix, label, color, dash in (
        ("input", "observed", "입력 관측", "#1769aa", "solid"),
        ("future", "observed", "미래 관측", "#202020", "dash"),
        ("future", "predicted", "GRU 예측", "#b64e28", "solid"),
    ):
        points = [point for point in detail.get("points", [])
                  if point["sample_role"] == role]
        if not points:
            continue
        spatial.add_scatter3d(
            x=[p[f"{prefix}_x_m"] for p in points],
            y=[p[f"{prefix}_y_m"] for p in points],
            z=[p[f"{prefix}_z_m"] for p in points],
            name=label, mode="lines+markers", marker={"size": 2},
            line={"color": color, "dash": dash},
        )
        altitude.add_scatter(
            x=[p["t_s"] - anchor for p in points],
            y=[p[f"{prefix}_z_m"] for p in points],
            name=label, mode="lines+markers", marker={"size": 3},
            line={"color": color, "dash": dash},
        )
    spatial.update_layout(
        template="plotly_white", title="입력 · 미래 관측 · 예측 / ENU",
        scene={"aspectmode": "data", "xaxis_title": "East (m)",
               "yaxis_title": "North (m)", "zaxis_title": "Up (m)"},
        margin={"l": 0, "r": 0, "t": 45, "b": 0},
    )
    altitude.update_layout(
        template="plotly_white", title="고도 변화",
        xaxis_title="마지막 입력 관측 기준 시간 (s)", yaxis_title="Up (m)",
        margin={"l": 55, "r": 15, "t": 45, "b": 45},
    )
    return spatial, altitude


def _table(rows, columns):
    def value(row, key):
        item = row.get(key)
        if isinstance(item, float):
            return f"{item:.3f}"
        if isinstance(item, list):
            return ", ".join(str(v) for v in item) or "—"
        return str(item) if item is not None else "—"

    return html.Table([
        html.Thead(html.Tr([html.Th(label) for _, label in columns])),
        html.Tbody([html.Tr([html.Td(value(row, key)) for key, _ in columns]) for row in rows]),
    ])


def diagnostic_panel():
    def field(label, identifier, **kwargs):
        return html.Div([html.Label(label, htmlFor=identifier),
                         dcc.Dropdown(id=identifier, **kwargs)], className="training-field")

    return html.Section([
        html.H3("Validation window 상세 진단"),
        html.P("위에서 선택한 실행·Checkpoint의 저장된 진단을 조회합니다. "
               "관측 기반 움직임과 generator annotation은 별도 정보입니다."),
        dcc.Store(id="training-diagnostic-catalog"),
        html.Button("상세 진단 새로고침", id="training-diagnostic-reload"),
        html.Div(id="training-diagnostic-status", role="status"),
        html.Div([
            field("관측 수직 움직임", "training-diagnostic-vertical", placeholder="전체"),
            field("관측 XY 움직임", "training-diagnostic-xy", placeholder="전체"),
            field("오차 내림차순", "training-diagnostic-sort",
                  options=[{"label": "Z RMSE", "value": "axis_rmse_z_m"},
                           {"label": "ADE", "value": "ade_m"}],
                  value="axis_rmse_z_m", clearable=False),
        ], className="training-fields"),
        html.Div(id="training-diagnostic-list", className="training-table-wrap",
                 style={"maxHeight": "22rem", "overflowY": "auto"}),
        field("Window", "training-diagnostic-window", placeholder="Window를 명시적으로 선택하세요"),
        html.Div(id="training-diagnostic-detail", role="status"),
        html.Div([
            dcc.Graph(id="training-diagnostic-trajectory", figure=diagnostic_figures({})[0],
                      responsive=True),
            dcc.Graph(id="training-diagnostic-altitude", figure=diagnostic_figures({})[1],
                      responsive=True),
        ], className="training-graphs"),
        html.Details([html.Summary("지점별 시각 · 고도 · generator annotation"),
                      html.Div(id="training-diagnostic-points", className="training-table-wrap",
                               style={"maxHeight": "25rem", "overflowY": "auto"})]),
        html.Details([html.Summary("진단 출처"), html.Pre(id="training-diagnostic-provenance")]),
    ])


def register_diagnostic_callbacks(app, service):
    @app.callback(
        Output("training-diagnostic-catalog", "data"),
        Output("training-diagnostic-status", "children"),
        Output("training-diagnostic-vertical", "options"),
        Output("training-diagnostic-vertical", "value"),
        Output("training-diagnostic-xy", "options"),
        Output("training-diagnostic-xy", "value"),
        Input("training-run", "value"), Input("training-checkpoint", "value"),
        Input("training-diagnostic-reload", "n_clicks"),
    )
    def catalog(run_id, checkpoint, _reload):
        if not run_id:
            return {}, "실행을 명시적으로 선택하세요.", [], None, [], None
        try:
            result = service.diagnostic_windows(run_id, checkpoint)
            source = result.get("provenance", {}).get("commands_status", "unavailable")
            message = (f"{run_id} / {checkpoint} / {len(result['windows'])} windows / "
                       f"움직임 출처: 관측 위치 / generator annotation: {source}"
                       if result["available"] else "이 실행에는 저장된 상세 진단이 없습니다.")
            return (result, message, result["vertical_options"], None,
                    result["xy_options"], None)
        except (ValueError, OSError, KeyError) as error:
            return {}, f"상세 진단 조회 실패: {error}", [], None, [], None

    @app.callback(
        Output("training-diagnostic-list", "children"),
        Output("training-diagnostic-window", "options"),
        Output("training-diagnostic-window", "value"),
        Input("training-diagnostic-catalog", "data"),
        Input("training-diagnostic-vertical", "value"),
        Input("training-diagnostic-xy", "value"),
        Input("training-diagnostic-sort", "value"),
    )
    def windows(catalog, vertical, xy, sort_by):
        rows = filter_diagnostic_windows((catalog or {}).get("windows", []), vertical, xy, sort_by)
        columns = [("episode_id", "Episode"), ("anchor_t_s", "입력 끝 (s)"),
                   ("observed_vertical", "관측 수직"), ("observed_xy_movement", "관측 XY"),
                   ("axis_rmse_z_m", "Z RMSE (m)"), ("ade_m", "ADE (m)")]
        options = [{"label": f"{r['episode_id']} / {r['anchor_t_s']:.1f}s / "
                             f"Z RMSE {r['axis_rmse_z_m']:.2f}m / {r['sequence_id'][:12]}",
                    "value": r["sequence_id"]} for r in rows]
        return _table(rows, columns) if rows else "해당 Window가 없습니다.", options, None

    @app.callback(
        Output("training-diagnostic-detail", "children"),
        Output("training-diagnostic-trajectory", "figure"),
        Output("training-diagnostic-altitude", "figure"),
        Output("training-diagnostic-points", "children"),
        Output("training-diagnostic-provenance", "children"),
        Input("training-run", "value"), Input("training-checkpoint", "value"),
        Input("training-diagnostic-window", "value"),
    )
    def detail(run_id, checkpoint, sequence_id):
        if not run_id or not sequence_id:
            return "Window를 선택하세요.", *diagnostic_figures({}), [], ""
        try:
            result = service.diagnostic_window(run_id, checkpoint, sequence_id)
            window = result["window"]
            transitions = window.get("generator_transitions", [])
            transition_text = " / ".join(
                f"{event['t_s']:.1f}s {event['event_id']} {event['transition']}"
                for event in transitions
            ) or "기록된 전환 없음 (annotation 가용성은 지점별 상태 참조)"
            message = (f"{window['episode_id']} / {sequence_id} / "
                       f"관측: {window['observed_vertical']} · {window['observed_xy_movement']} / "
                       f"Generator: {transition_text}")
            columns = [("sample_role", "구간"), ("t_s", "시간 (s)"),
                       ("observed_z_m", "관측 Z (m)"), ("predicted_z_m", "예측 Z (m)"),
                       ("error_3d_m", "3D 오차 (m)"),
                       ("generator_event_ids", "Generator event"),
                       ("generator_annotation_status", "Annotation 상태")]
            return (message, *diagnostic_figures(result), _table(result["points"], columns),
                    json.dumps(result["provenance"], ensure_ascii=False, indent=2))
        except (ValueError, OSError, KeyError) as error:
            return f"Window 조회 실패: {error}", *diagnostic_figures({}), [], ""
