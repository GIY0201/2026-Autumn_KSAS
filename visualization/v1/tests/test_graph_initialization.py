"""A mounted/replaced viewer must never stream into uninitialized Plotly traces."""

from visualization.v1.app import create_app
from visualization.v1.tests.test_app import _component_by_id
from visualization.v1.tests.test_generation_ui import _post
from visualization.v1.tests.test_variable_playback import variable_path


def test_replaced_viewer_has_real_traces_before_any_tick(tmp_path):
    path = variable_path(tmp_path, (601,))
    app = create_app(path)
    for name in ("xy", "xz", "yz", "3d"):
        figure = _component_by_id(app.layout, f"figure-{name}").figure
        assert figure and len(figure.data) == 4


def test_selection_and_ticks_have_one_ordered_figure_writer(tmp_path):
    path = variable_path(tmp_path, (601, 501))
    app = create_app(path)
    assert not any("extendData" in key for key in app.callback_map)
    inputs = {
        "player-state": {"step": 4, "playing": True},
        "episode-select": ["variable-0", "variable-1"],
        "variant-select": "sigma_3m",
        "truth-toggle": ["show"],
        "dataset-key": path.name,
    }
    result = _post(
        app, "figure-xy.figure", inputs, {"render-context": None}, "episode-select.value"
    )
    assert len(result["figure-xy"]["figure"]["data"]) == 8
    result = _post(
        app,
        "figure-xy.figure",
        inputs,
        {"render-context": result["render-context"]["data"]},
        "player-state.data",
    )
    patch = result["figure-xy"]["figure"]
    assert "operations" in patch
    assert any(
        op["location"] == ["data", 6, "x"] and op["params"]["value"] == [4.0]
        for op in patch["operations"]
    )


def test_camera_drag_defers_3d_updates_and_release_catches_up(tmp_path):
    path = variable_path(tmp_path, (601,))
    app = create_app(path)
    inputs = {
        "player-state": {"step": 20, "playing": True},
        "episode-select": ["variable-0"],
        "variant-select": "sigma_3m",
        "truth-toggle": ["show"],
        "dataset-key": path.name,
        "camera-interacting": True,
    }
    context = dict(dataset=path.name, episodes=["variable-0"], variant="sigma_3m", truth=True)
    result = _post(
        app, "figure-xy.figure", inputs, {"render-context": context}, "player-state.data"
    )
    assert "figure-3d" not in result
    assert "figure-xy" in result
    inputs["camera-interacting"] = False
    result = _post(
        app, "figure-xy.figure", inputs, {"render-context": context}, "camera-interacting.data"
    )
    assert any(
        op["params"]["value"] == [20.0] for op in result["figure-3d"]["figure"]["operations"]
    )
