"""Tests for the local Dash trajectory playback surface."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from data_generation.v1.dataset import GenerationRequest, write_dataset
from data_generation.v1.scenario import GeneratedEpisode
from visualization.v1.app import create_app


def _episode() -> GeneratedEpisode:
    steps = np.arange(301, dtype=int)
    return GeneratedEpisode(
        episode_id="x8-00000019",
        status="complete",
        failure_reason=None,
        seed=19,
        steps=steps,
        t_s=steps.astype(float) * 0.2,
        position_enu_m=np.column_stack((steps, steps * 0.0, 100.0 + steps)),
        velocity_enu_mps=np.column_stack((np.full(301, 5.0), np.zeros(301), np.zeros(301))),
        euler_rad=np.zeros((301, 3)),
        airspeed_mps=np.full(301, 18.0),
        alpha_rad=np.full(301, 0.1),
        beta_rad=np.zeros(301),
        raw_commands=np.column_stack((np.zeros(301), np.zeros(301), np.full(301, 0.5))),
        event_records=(),
    )


def _dataset_path(tmp_path: Path) -> Path:
    return write_dataset(
        [_episode()],
        request=GenerationRequest(
            master_seed=19, mode="diagnostic", source_motion_check_status="NOT_RUN"
        ),
        output_root=tmp_path / "outputs",
    ).path


def _component_ids(component) -> set[str]:
    children = getattr(component, "children", None)
    current_id = getattr(component, "id", None)
    identifiers = {current_id} if isinstance(current_id, str) else set()
    if isinstance(children, (list, tuple)):
        for child in children:
            identifiers.update(_component_ids(child))
    elif children is not None:
        identifiers.update(_component_ids(children))
    return identifiers


def _component_by_id(component, target_id: str):
    """Find one Dash component by ID inside a composed layout."""
    if getattr(component, "id", None) == target_id:
        return component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            found = _component_by_id(child, target_id)
            if found is not None:
                return found
    elif children is not None:
        return _component_by_id(children, target_id)
    return None


def _callback_input_ids(app, output_fragment: str) -> set[str]:
    """Return input component IDs for the callback that owns one output property."""
    for callback_key, callback in app.callback_map.items():
        if output_fragment in callback_key:
            return {item["id"] for item in callback["inputs"]}
    raise AssertionError(f"missing callback output: {output_fragment}")


def test_viewer_exposes_synchronized_controls_and_hides_truth_in_public_mode(
    tmp_path: Path,
) -> None:
    """The browser surface must expose playback/export controls without truth leakage."""
    dataset_path = _dataset_path(tmp_path)
    evaluation_app = create_app(dataset_path, public_only=False, output_root=tmp_path / "exports")
    public_app = create_app(dataset_path, public_only=True, output_root=tmp_path / "exports")

    expected = {
        "episode-select",
        "variant-select",
        "player-state",
        "play-toggle",
        "step-slider",
        "playback-speed",
        "playback-tick",
        "figure-xy",
        "figure-xz",
        "figure-yz",
        "figure-3d",
        "export-png",
        "export-status",
    }
    accessibility_labels = {
        "episode-select-label",
        "variant-select-label",
        "time-step-label",
        "xy-view-heading",
        "xz-view-heading",
        "yz-view-heading",
        "three-d-view-heading",
    }
    evaluation_ids = _component_ids(evaluation_app.layout)
    public_ids = _component_ids(public_app.layout)
    assert expected <= evaluation_ids
    assert expected <= public_ids
    assert accessibility_labels <= evaluation_ids
    assert accessibility_labels <= public_ids
    assert "truth-toggle" in evaluation_ids
    assert "truth-toggle" not in public_ids
    assert evaluation_app.config.external_scripts == []
    assert public_app.config.external_scripts == []


def test_viewer_uses_human_readable_episode_and_visible_projection_names(
    tmp_path: Path,
) -> None:
    """People must be able to identify an episode and each projection at a glance."""
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "exports")

    episode_select = _component_by_id(app.layout, "episode-select")
    xy_heading = _component_by_id(app.layout, "xy-view-heading")
    xz_heading = _component_by_id(app.layout, "xz-view-heading")
    yz_heading = _component_by_id(app.layout, "yz-view-heading")

    assert episode_select.options[0]["label"] == (
        "X8 고정익 UAV · 진단 시뮬레이션 1 (ID: x8-00000019)"
    )
    assert xy_heading.children == "XY 평면 · 수평 이동 (East–North)"
    assert xz_heading.children == "XZ 평면 · 고도 변화 (East–Up)"
    assert yz_heading.children == "YZ 평면 · 측면 이동 (North–Up)"
    assert xy_heading.className == "figure-heading"


def test_each_projection_reserves_a_visible_plot_canvas(tmp_path: Path) -> None:
    """Every Plotly panel must have its own responsive, nonzero display height."""
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "exports")

    for figure_id in ("figure-xy", "figure-xz", "figure-yz", "figure-3d"):
        graph = _component_by_id(app.layout, figure_id)
        assert graph.style["height"] == "clamp(22rem, 36vw, 34rem)"


def test_single_episode_dataset_explains_why_episode_comparison_is_unavailable(
    tmp_path: Path,
) -> None:
    """A one-Episode dataset must not look like a broken comparison selector."""
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "exports")

    episode_select = _component_by_id(app.layout, "episode-select")
    availability = _component_by_id(app.layout, "episode-availability")

    assert episode_select.disabled is True
    assert availability.children == (
        "이 dataset에는 Episode가 1개뿐입니다. 여러 Episode를 비교하려면 "
        "Episode가 둘 이상인 dataset을 여세요."
    )


def test_multiple_episode_dataset_keeps_episode_comparison_selectable(tmp_path: Path) -> None:
    """A dataset with alternatives must keep its multi-Episode control enabled."""
    first = _episode()
    second = replace(first, episode_id="x8-00000020", seed=20)
    dataset_path = write_dataset(
        [first, second],
        request=GenerationRequest(
            master_seed=20, mode="diagnostic", source_motion_check_status="NOT_RUN"
        ),
        output_root=tmp_path / "outputs",
    ).path

    app = create_app(dataset_path, output_root=tmp_path / "exports")
    episode_select = _component_by_id(app.layout, "episode-select")

    assert episode_select.disabled is False
    assert len(episode_select.options) == 2


def test_playback_updates_marker_stream_without_rebuilding_all_figures(tmp_path: Path) -> None:
    """Each stored step must stream marker data instead of replacing four full figures."""
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "exports")

    static_figure_inputs = _callback_input_ids(app, "figure-xy.figure")
    marker_stream_inputs = _callback_input_ids(app, "figure-xy.extendData")

    assert app.config.update_title is None
    assert "player-state" not in static_figure_inputs
    assert "player-state" in marker_stream_inputs
