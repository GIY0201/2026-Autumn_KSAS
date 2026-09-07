"""Tests for synchronized XY/XZ/YZ/3D trajectory figures and PNG export."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from data_generation.v1.dataset import GenerationRequest, write_dataset
from data_generation.v1.scenario import GeneratedEpisode
from visualization.v1.figures import (
    advance_player,
    build_current_marker_extensions,
    build_projection_figure,
    export_png,
    export_png_set,
    load_viewer_dataset,
)


def _episode() -> GeneratedEpisode:
    steps = np.arange(301, dtype=int)
    position = np.column_stack((steps.astype(float), 2.0 * steps, 100.0 + steps))
    return GeneratedEpisode(
        episode_id="x8-00000017",
        status="complete",
        failure_reason=None,
        seed=17,
        steps=steps,
        t_s=steps.astype(float) * 0.2,
        position_enu_m=position,
        velocity_enu_mps=np.column_stack((np.full(301, 5.0), np.zeros(301), np.zeros(301))),
        euler_rad=np.zeros((301, 3)),
        airspeed_mps=np.full(301, 18.0),
        alpha_rad=np.full(301, 0.1),
        beta_rad=np.zeros(301),
        raw_commands=np.column_stack((np.zeros(301), np.zeros(301), np.full(301, 0.5))),
        event_records=(),
    )


def _viewer_dataset(
    tmp_path: Path,
    *,
    episode_count: int = 1,
    public_only: bool = False,
):
    episode = _episode()
    result = write_dataset(
        [
            replace(
                episode,
                episode_id=f"x8-{17 + index:08d}",
                seed=17 + index,
            )
            for index in range(episode_count)
        ],
        request=GenerationRequest(
            master_seed=1, mode="diagnostic", source_motion_check_status="NOT_RUN"
        ),
        output_root=tmp_path / "outputs",
    )
    return load_viewer_dataset(result.path, public_only=public_only), result.path


def _current_trace(figure, prefix: str):
    return next(trace for trace in figure.data if trace.name.startswith(prefix))


def test_all_projections_show_exact_current_truth_and_observed_xyz_at_stored_step_one(
    tmp_path: Path,
) -> None:
    """Each view distinguishes exact simulation truth from the stored measurement."""
    dataset, _ = _viewer_dataset(tmp_path)
    episode_id = "x8-00000017"
    step = 1
    truth = dataset.truth(episode_id)[step]
    observed = dataset.observed(episode_id, "sigma_3m")[step]
    figures = {
        view: build_projection_figure(
            dataset,
            view=view,
            episode_ids=[episode_id],
            variant_id="sigma_3m",
            step=step,
            show_truth=True,
        )
        for view in ("xy", "xz", "yz", "3d")
    }

    truth_xy = _current_trace(figures["xy"], "Simulation truth current")
    observed_xy = _current_trace(figures["xy"], "Observation current")
    truth_path_xy = _current_trace(figures["xy"], "Simulation truth ·")
    observation_path_xy = _current_trace(
        figures["xy"], "Observation (measurement noise; not actual path) ·"
    )
    truth_xz = _current_trace(figures["xz"], "Simulation truth current")
    observed_xz = _current_trace(figures["xz"], "Observation current")
    truth_yz = _current_trace(figures["yz"], "Simulation truth current")
    observed_yz = _current_trace(figures["yz"], "Observation current")
    truth_three_d = _current_trace(figures["3d"], "Simulation truth current")
    observed_three_d = _current_trace(figures["3d"], "Observation current")

    assert (truth_xy.x[0], truth_xy.y[0]) == pytest_approx_tuple((truth[0], truth[1]))
    assert (observed_xy.x[0], observed_xy.y[0]) == pytest_approx_tuple((observed[0], observed[1]))
    assert (truth_xz.x[0], truth_xz.y[0]) == pytest_approx_tuple((truth[0], truth[2]))
    assert (observed_xz.x[0], observed_xz.y[0]) == pytest_approx_tuple((observed[0], observed[2]))
    assert (truth_yz.x[0], truth_yz.y[0]) == pytest_approx_tuple((truth[1], truth[2]))
    assert (observed_yz.x[0], observed_yz.y[0]) == pytest_approx_tuple((observed[1], observed[2]))
    assert (truth_three_d.x[0], truth_three_d.y[0], truth_three_d.z[0]) == pytest_approx_tuple(
        tuple(truth)
    )
    assert (
        observed_three_d.x[0], observed_three_d.y[0], observed_three_d.z[0]
    ) == pytest_approx_tuple(tuple(observed))
    for truth_marker, observed_marker in (
        (truth_xy, observed_xy),
        (truth_xz, observed_xz),
        (truth_yz, observed_yz),
        (truth_three_d, observed_three_d),
    ):
        assert truth_marker.marker.symbol == "circle"
        assert observed_marker.marker.symbol == "diamond"
        assert truth_marker.marker.color == observed_marker.marker.color
    assert truth_path_xy.line.dash is None
    assert observation_path_xy.line.dash == "dash"
    assert [trace.name.split(" · ", 1)[0] for trace in figures["xy"].data] == [
        "Simulation truth",
        "Observation (measurement noise; not actual path)",
        "Simulation truth current",
        "Observation current",
    ]
    assert figures["3d"].layout.scene.aspectmode == "data"


def test_figure_uses_a_human_readable_episode_name_not_only_an_opaque_id(
    tmp_path: Path,
) -> None:
    """Legend labels preserve a stable ID elsewhere but read naturally in the plot."""
    dataset, _ = _viewer_dataset(tmp_path)
    figure = build_projection_figure(
        dataset,
        view="xy",
        episode_ids=["x8-00000017"],
        variant_id="sigma_3m",
        step=0,
        show_truth=True,
    )

    assert dataset.episode_display_name("x8-00000017") == "X8 고정익 UAV · 진단 시뮬레이션 1"
    assert figure.data[0].name == "Simulation truth · X8 고정익 UAV · 진단 시뮬레이션 1"
    assert "x8-00000017" not in figure.data[0].name


def test_current_marker_extensions_only_replace_current_marker_points_for_one_to_four_episodes(
    tmp_path: Path,
) -> None:
    """Playback streams truth and observation markers in the shared trace order."""
    for episode_count in range(1, 5):
        dataset, _ = _viewer_dataset(
            tmp_path / f"episodes-{episode_count}", episode_count=episode_count
        )
        episode_ids = list(dataset.episode_ids)
        step = 1

        extensions = build_current_marker_extensions(
            dataset,
            episode_ids=episode_ids,
            variant_id="sigma_3m",
            step=step,
            show_truth=True,
        )

        current_positions = [
            position
            for episode_id in episode_ids
            for position in (
                dataset.truth(episode_id)[step],
                dataset.observed(episode_id, "sigma_3m")[step],
            )
        ]
        expected_trace_indexes = [
            trace_index
            for episode_index in range(len(episode_ids))
            for trace_index in (4 * episode_index + 2, 4 * episode_index + 3)
        ]
        for view, first_axis, second_axis in (
            ("xy", 0, 1),
            ("xz", 0, 2),
            ("yz", 1, 2),
        ):
            data, trace_indexes, max_points = extensions[view]
            assert data == {
                "x": [[float(position[first_axis])] for position in current_positions],
                "y": [[float(position[second_axis])] for position in current_positions],
            }
            assert trace_indexes == expected_trace_indexes
            assert max_points == 1

        three_d_data, three_d_trace_indexes, three_d_max_points = extensions["3d"]
        assert three_d_data == {
            "x": [[float(position[0])] for position in current_positions],
            "y": [[float(position[1])] for position in current_positions],
            "z": [[float(position[2])] for position in current_positions],
        }
        assert three_d_trace_indexes == expected_trace_indexes
        assert three_d_max_points == 1


def test_current_marker_extensions_preserve_observation_only_order_when_truth_is_hidden(
    tmp_path: Path,
) -> None:
    """A hidden truth toggle leaves only dashed-observation current markers to stream."""
    dataset, _ = _viewer_dataset(tmp_path, episode_count=2)
    episode_ids = list(dataset.episode_ids)
    step = 300

    extensions = build_current_marker_extensions(
        dataset,
        episode_ids=episode_ids,
        variant_id="sigma_3m",
        step=step,
        show_truth=False,
    )

    observed_positions = [
        dataset.observed(episode_id, "sigma_3m")[step] for episode_id in episode_ids
    ]
    xy_data, xy_trace_indexes, xy_max_points = extensions["xy"]
    assert xy_data == {
        "x": [[float(position[0])] for position in observed_positions],
        "y": [[float(position[1])] for position in observed_positions],
    }
    assert xy_trace_indexes == [1, 3]
    assert xy_max_points == 1


def test_truth_traces_and_markers_are_absent_when_hidden_or_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public-only figures cannot regain evaluation truth through the display toggle."""
    dataset, dataset_path = _viewer_dataset(tmp_path)
    hidden = build_projection_figure(
        dataset,
        view="xy",
        episode_ids=[dataset.episode_ids[0]],
        variant_id="sigma_3m",
        step=0,
        show_truth=False,
    )
    assert all(not trace.name.startswith("Simulation truth") for trace in hidden.data)

    def _reject_truth_read(*_args, **_kwargs):
        raise AssertionError("public-only viewer must not access evaluation truth")

    monkeypatch.setattr("visualization.v1.figures.read_evaluation_truth", _reject_truth_read)
    public_dataset = load_viewer_dataset(dataset_path, public_only=True)
    public_figure = build_projection_figure(
        public_dataset,
        view="xy",
        episode_ids=[public_dataset.episode_ids[0]],
        variant_id="sigma_3m",
        step=300,
        show_truth=True,
    )
    assert public_dataset.has_truth is False
    assert all(not trace.name.startswith("Simulation truth") for trace in public_figure.data)


def test_current_marker_wire_coordinates_are_json_lists_in_all_views_and_truth_modes(
    tmp_path: Path,
) -> None:
    """extendData needs ordinary JSON arrays for every current-marker coordinate."""
    dataset, _ = _viewer_dataset(tmp_path)
    episode_id = dataset.episode_ids[0]

    for show_truth, expected_marker_labels in (
        (False, ["Observation current"]),
        (True, ["Simulation truth current", "Observation current"]),
    ):
        for view in ("xy", "xz", "yz", "3d"):
            wire_figure = json.loads(
                build_projection_figure(
                    dataset,
                    view=view,
                    episode_ids=[episode_id],
                    variant_id="sigma_1m",
                    step=1,
                    show_truth=show_truth,
                ).to_json()
            )
            current_traces = [
                trace
                for trace in wire_figure["data"]
                if trace["name"].split(" · ", 1)[0] in expected_marker_labels
            ]
            assert [
                trace["name"].split(" · ", 1)[0] for trace in current_traces
            ] == expected_marker_labels
            coordinate_names = ("x", "y", "z") if view == "3d" else ("x", "y")
            for trace in current_traces:
                assert all(isinstance(trace[coordinate], list) for coordinate in coordinate_names)


def test_current_marker_extensions_keep_exact_truth_and_observation_at_endpoints_and_seek(
    tmp_path: Path,
) -> None:
    """First, last, and backward-seek states retain both exact current marker values."""
    dataset, _ = _viewer_dataset(tmp_path)
    episode_id = dataset.episode_ids[0]
    backward_state = advance_player(
        {"step": 300, "playing": False}, trigger="seek", requested_step=1
    )

    states = (
        {"step": 0, "playing": False},
        {"step": 300, "playing": False},
        backward_state,
    )
    for state in states:
        step = int(state["step"])
        extensions = build_current_marker_extensions(
            dataset,
            episode_ids=[episode_id],
            variant_id="sigma_1m",
            step=step,
            show_truth=True,
        )
        truth = dataset.truth(episode_id)[step]
        observed = dataset.observed(episode_id, "sigma_1m")[step]

        for view, first_axis, second_axis in (
            ("xy", 0, 1),
            ("xz", 0, 2),
            ("yz", 1, 2),
        ):
            data, trace_indexes, max_points = extensions[view]
            assert data == {
                "x": [[float(truth[first_axis])], [float(observed[first_axis])]],
                "y": [[float(truth[second_axis])], [float(observed[second_axis])]],
            }
            assert trace_indexes == [2, 3]
            assert max_points == 1

        three_d_data, three_d_trace_indexes, three_d_max_points = extensions["3d"]
        assert three_d_data == {
            "x": [[float(truth[0])], [float(observed[0])]],
            "y": [[float(truth[1])], [float(observed[1])]],
            "z": [[float(truth[2])], [float(observed[2])]],
        }
        assert three_d_trace_indexes == [2, 3]
        assert three_d_max_points == 1


def test_projection_title_does_not_show_a_stale_playback_step(tmp_path: Path) -> None:
    """A streamed marker must not leave an old time step in a static figure title."""
    dataset, _ = _viewer_dataset(tmp_path)
    first = build_projection_figure(
        dataset,
        view="xy",
        episode_ids=["x8-00000017"],
        variant_id="sigma_3m",
        step=0,
        show_truth=True,
    )
    later = build_projection_figure(
        dataset,
        view="xy",
        episode_ids=["x8-00000017"],
        variant_id="sigma_3m",
        step=137,
        show_truth=True,
    )

    assert first.layout.title.text == "Stored trajectory · current marker follows playback"
    assert later.layout.title.text == first.layout.title.text


def test_player_seek_pause_and_endpoint_do_not_resample_data() -> None:
    """The player uses integer dataset steps and stops at the final stored observation."""
    paused = advance_player({"step": 15, "playing": False}, trigger="seek", requested_step=80)
    playing = advance_player(paused, trigger="play")
    endpoint = advance_player({"step": 300, "playing": True}, trigger="tick")
    backward = advance_player({"step": 300, "playing": False}, trigger="seek", requested_step=1)

    assert paused == {"step": 80, "playing": False}
    assert playing == {"step": 80, "playing": True}
    assert endpoint == {"step": 300, "playing": False}
    assert backward == {"step": 1, "playing": False}


def test_png_export_writes_a_new_visualization_run_with_selected_state(tmp_path: Path) -> None:
    """PNG export must write under outputs/visualization, not next to viewer code or input data."""
    dataset, dataset_path = _viewer_dataset(tmp_path)
    figure = build_projection_figure(
        dataset,
        view="xy",
        episode_ids=["x8-00000017"],
        variant_id="sigma_1m",
        step=20,
        show_truth=True,
    )

    result = export_png(
        figure,
        dataset_path=dataset_path,
        view="xy",
        episode_ids=["x8-00000017"],
        variant_id="sigma_1m",
        step=20,
        output_root=tmp_path / "outputs",
    )

    assert result.png_path.is_file()
    assert result.png_path.parent == result.path
    assert (result.path / "manifest.csv").is_file()
    assert (result.path / "view_state.csv").is_file()
    assert not (dataset_path / "plot.png").exists()


def test_four_view_export_keeps_all_pngs_in_one_visualization_run(tmp_path: Path) -> None:
    """The browser's four-view export preserves all synchronized projections together."""
    dataset, dataset_path = _viewer_dataset(tmp_path)
    figures = {
        view: build_projection_figure(
            dataset,
            view=view,
            episode_ids=["x8-00000017"],
            variant_id="sigma_3m",
            step=27,
            show_truth=True,
        )
        for view in ("xy", "xz", "yz", "3d")
    }

    result = export_png_set(
        figures,
        dataset_path=dataset_path,
        episode_ids=["x8-00000017"],
        variant_id="sigma_3m",
        step=27,
        output_root=tmp_path / "outputs",
    )

    assert set(result.png_paths) == {"xy", "xz", "yz", "3d"}
    assert all(path.is_file() and path.parent == result.path for path in result.png_paths.values())
    assert (result.path / "manifest.csv").is_file()


def pytest_approx_tuple(values: tuple[float, ...]):
    """Return an independently evaluated tuple approximation for plotting coordinates."""
    import pytest

    return pytest.approx(values, abs=1e-12)
