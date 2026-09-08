"""Saved kinematics must follow the playback clock without leaking into public mode."""

import json

import numpy as np
import pytest

from visualization.v1.app import create_app
from visualization.v1.figures import load_viewer_dataset
from visualization.v1.tests.test_generation_ui import _post
from visualization.v1.tests.test_variable_playback import variable_path


def test_saved_velocity_is_available_at_exact_last_step(tmp_path):
    dataset = load_viewer_dataset(variable_path(tmp_path, (601,)))
    assert hasattr(dataset, "kinematics"), "viewer discards stored velocity/acceleration"
    values = dataset.kinematics("variable-0", 600)
    np.testing.assert_allclose(values, [5, 10, 15, 0, 0, 0], atol=1e-10)


@pytest.mark.parametrize("step", [600, 12, 0])
def test_panel_http_matches_seek_and_speed_units(tmp_path, step):
    path = variable_path(tmp_path, (601,))
    app = create_app(path)
    assert "telemetry-content.children" in app.callback_map, "telemetry callback is missing"
    result = _post(
        app,
        "telemetry-content.children",
        {
            "player-state": {"step": step, "playing": False},
            "episode-select": ["variable-0"],
            "dataset-key": path.name,
        },
        {},
        "player-state.data",
    )
    content = json.dumps(result, ensure_ascii=False)
    assert "18.708" in content
    assert "67.350" in content
    assert f"{step * 0.2:.1f} s" in content
    assert "m/s²" in content


def test_public_only_panel_never_loads_truth(tmp_path):
    path = variable_path(tmp_path)
    (path / "evaluation" / "truth.csv").unlink()
    app = create_app(path, public_only=True)
    assert "telemetry-content.children" in app.callback_map
    result = _post(
        app,
        "telemetry-content.children",
        {
            "player-state": {"step": 0, "playing": False},
            "episode-select": ["variable-0"],
            "dataset-key": path.name,
        },
        {},
        "player-state.data",
    )
    assert "Public-only" in json.dumps(result, ensure_ascii=False)
