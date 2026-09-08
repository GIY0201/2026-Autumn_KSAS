"""Small playback-synchronized table of saved evaluation-only state."""

import numpy as np
from dash import html

from contracts.v1.validation import OUTPUT_DT_S

from .figures import ViewerDataset

MPS_TO_KMPH = 3.6


def telemetry_content(dataset: ViewerDataset, selected: tuple[str, ...], step: int):
    """Render only the selected stored sample; no trajectory recomputation."""
    if not dataset.has_truth:
        return html.P("Public-only: 검토용 실제 위치·속도·가속도는 읽지 않습니다.")
    rows = []
    for episode_id in selected:
        state = dataset.kinematics(episode_id, step)
        position = dataset.truth(episode_id)[step]
        speed = float(np.linalg.norm(state[:3]))
        acceleration = float(np.linalg.norm(state[3:]))
        assert np.isfinite(speed) and np.isfinite(acceleration)

        def vector(values):
            return " / ".join(f"{value:.3f}" for value in values)

        rows.append(
            html.Tr(
                [
                    html.Th(
                        dataset.episode_display_name(episode_id), scope="row", title=episode_id
                    ),
                    html.Td(f"{speed:.3f} m/s · {speed * MPS_TO_KMPH:.3f} km/h"),
                    html.Td(vector(position)),
                    html.Td(vector(state[:3])),
                    html.Td(vector(state[3:])),
                    html.Td(f"{acceleration:.3f}"),
                ]
            )
        )
    return html.Table(
        [
            html.Caption(f"현재 시각 {step * OUTPUT_DT_S:.1f} s · 저장된 시뮬레이션 검토값"),
            html.Thead(
                html.Tr(
                    [
                        html.Th(label, scope="col")
                        for label in (
                            "객체 / Episode",
                            "속력",
                            "위치 X / Y / Z (m)",
                            "속도 Vx / Vy / Vz (m/s)",
                            "가속도 Ax / Ay / Az (m/s²)",
                            "가속도 크기 (m/s²)",
                        )
                    ]
                )
            ),
            html.Tbody(rows),
        ],
        className="telemetry-table",
    )


def telemetry_panel(dataset: ViewerDataset):
    return html.Section(
        [
            html.H2("현재 위치 · 속도 · 가속도", id="telemetry-heading", className="section-title"),
            html.P(
                "ENU: X=East, Y=North, Z=Up. 속도는 저장값, 가속도는 저장 속도의 "
                "시간 미분값입니다. 관측오차·평활화가 없는 평가 전용 값이며 학습 입력이 아닙니다.",
                className="control-note",
            ),
            html.Div(
                telemetry_content(dataset, dataset.episode_ids[:1], 0),
                id="telemetry-content",
                className="telemetry-scroll",
                tabIndex=0,
                role="region",
                **{"aria-label": "현재 시점 운동 상태 표"},
            ),
        ],
        className="telemetry-panel",
        **{"aria-labelledby": "telemetry-heading"},
    )
