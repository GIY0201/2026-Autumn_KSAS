"""Stored variable-duration playback, including mixed-length HTTP selections."""

import numpy as np
import pytest

from data_generation.v1.dataset import write_dataset
from data_generation.v1.records import MotionEpisode
from data_generation.v1.service import GenerationJob
from data_generation.v1.tests.test_multi_object_dataset import _generic_request
from visualization.v1.app import _viewer_layout, create_app
from visualization.v1.figures import (
    advance_player,
    build_current_marker_extensions,
    build_projection_figure,
    load_viewer_dataset,
)
from visualization.v1.tests.test_app import _component_by_id
from visualization.v1.tests.test_generation_ui import _post


def variable_path(tmp_path, counts=(601,)):
    episodes = []
    for index, count in enumerate(counts):
        steps = np.arange(count)
        episodes.append(
            MotionEpisode(
                episode_id=f"variable-{index}",
                status="complete",
                failure_reason=None,
                seed=index,
                steps=steps,
                t_s=steps * 0.2,
                position_enu_m=np.column_stack((steps, steps * 2, steps * 3)),
                velocity_enu_mps=np.tile([5.0, 10.0, 15.0], (count, 1)),
                diagnostic_arrays={},
                event_records=(),
            )
        )
    return write_dataset(
        episodes,
        request=_generic_request(),
        output_root=tmp_path / "outputs",
    ).path


@pytest.mark.parametrize("count", [2, 301, 601, 3001])
@pytest.mark.parametrize("public_only", [False, True])
def test_variable_last_step_is_exact_in_all_views(tmp_path, count, public_only):
    dataset = load_viewer_dataset(variable_path(tmp_path, (count,)), public_only=public_only)
    selected = dataset.episode_ids
    expected = dataset.observed(selected[0], "sigma_3m")[-1]
    updates = build_current_marker_extensions(
        dataset,
        episode_ids=selected,
        variant_id="sigma_3m",
        step=count - 1,
        show_truth=True,
    )
    for view, axes in {"xy": (0, 1), "xz": (0, 2), "yz": (1, 2), "3d": (0, 1, 2)}.items():
        figure = build_projection_figure(
            dataset,
            view=view,
            episode_ids=selected,
            variant_id="sigma_3m",
            step=count - 1,
            show_truth=True,
        )
        for coordinate, axis in zip(("x", "y", "z"), axes, strict=False):
            assert getattr(figure.data[-1], coordinate)[0] == expected[axis]
            assert updates[view][0][coordinate][-1][0] == expected[axis]
    layout = _viewer_layout(dataset)
    assert _component_by_id(layout, "step-slider").max == count - 1
    assert f"of {count - 1}" in _component_by_id(layout, "playback-status").children


def test_player_variable_endpoint_and_reverse_seek():
    state = advance_player({"step": 599, "playing": True}, trigger="tick", sample_count=601)
    assert state == {"step": 600, "playing": False}
    assert advance_player(state, trigger="seek", requested_step=3, sample_count=601)["step"] == 3


def test_mixed_selection_resets_and_uses_common_stored_end(tmp_path):
    path = variable_path(tmp_path, (601, 101))
    app = create_app(path)
    result = _post(
        app,
        "player-state.data",
        {
            "play-toggle": 0,
            "step-slider": 590,
            "playback-tick": 0,
            "episode-select": ["variable-0", "variable-1"],
            "dataset-key": path.name,
        },
        {"player-state": {"step": 590, "playing": True}},
        "episode-select.value",
    )
    assert result["player-state"]["data"] == {"step": 0, "playing": False}
    assert result["step-slider"]["max"] == 100
    assert "공통" in result["playback-status"]["children"]
    dataset = load_viewer_dataset(path)
    with pytest.raises(ValueError, match="step"):
        build_current_marker_extensions(
            dataset,
            episode_ids=dataset.episode_ids,
            variant_id="sigma_3m",
            step=101,
            show_truth=True,
        )


@pytest.mark.parametrize("public_only", [False, True])
def test_http_switch_refreshes_timeline_and_seek_payload(tmp_path, public_only):
    first = variable_path(tmp_path, (301,))
    second = variable_path(tmp_path, (3001,))
    app = create_app(first, public_only=public_only)
    jobs = app.server.extensions["generation_service"]
    # Publish a completed result through the same service snapshot used by the panel.
    jobs._job = GenerationJob(
        "fixture", "fixture", "fixture", 1, 1, tmp_path, state="complete", result_path=second
    )
    jobs._results.append(jobs._job)
    _post(
        app,
        "generation-status.children",
        {"generation-poll": 1, "generation-refresh": 0},
        {"dataset-select": _component_by_id(app.layout, "dataset-select").options},
        "generation-poll.n_intervals",
    )
    layout = _post(
        app,
        "viewer-container.children",
        {"dataset-select": second.name},
        {},
        "dataset-select.value",
    )
    assert layout["dataset-load-error"]["children"] == ""
    inputs = {
        "play-toggle": 0,
        "step-slider": 0,
        "playback-tick": 0,
        "episode-select": ["variable-0"],
        "dataset-key": second.name,
    }
    reset = _post(
        app,
        "player-state.data",
        inputs,
        {"player-state": {"step": 299, "playing": True}},
        "dataset-key.data",
    )
    assert reset["step-slider"]["max"] == 3000
    assert reset["step-slider"]["marks"]["3000"] == "600 s"
    assert reset["player-state"]["data"] == {"step": 0, "playing": False}
    inputs["step-slider"] = 3000
    sought = _post(
        app,
        "player-state.data",
        inputs,
        {"player-state": reset["player-state"]["data"]},
        "step-slider.value",
    )
    assert "600.0 s" in sought["playback-status"]["children"]
    states = {
        "episode-select": ["variable-0"],
        "variant-select": "sigma_3m",
        "dataset-key": second.name,
        "truth-toggle": ["show"],
    }
    context = {
        "dataset": second.name,
        "episodes": ["variable-0"],
        "variant": "sigma_3m",
        "truth": not public_only,
    }
    markers = _post(
        app,
        "figure-xy.figure",
        {**states, "player-state": sought["player-state"]["data"]},
        {"render-context": context},
        "player-state.data",
    )
    expected = load_viewer_dataset(second, public_only=public_only).observed(
        "variable-0", "sigma_3m"
    )[-1]
    assert any(
        op["location"] == ["data", 1 if public_only else 3, "x"]
        and op["params"]["value"] == [expected[0]]
        for op in markers["figure-xy"]["figure"]["operations"]
    )
    jobs.close()
