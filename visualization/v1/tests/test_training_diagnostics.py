"""Saved diagnostic explorer displays observed data and explicit selection only."""

from dash import Dash

from visualization.v1.tests.test_training_ui import MockService


def test_diagnostic_graphs_preserve_input_and_future_roles():
    from visualization.v1.training_diagnostics import diagnostic_figures

    detail = {"window": {"anchor_t_s": 3}, "points": [
        dict(sample_role="input", t_s=2.8, observed_x_m=0, observed_y_m=0,
             observed_z_m=10, predicted_x_m=None, predicted_y_m=None, predicted_z_m=None),
        dict(sample_role="future", t_s=3.2, observed_x_m=1, observed_y_m=2,
             observed_z_m=11, predicted_x_m=2, predicted_y_m=3, predicted_z_m=10.5),
    ]}
    spatial, altitude = diagnostic_figures(detail)
    assert len(spatial.data) == 3
    assert list(spatial.data[0].z) == [10]
    assert list(spatial.data[2].z) == [10.5]
    assert list(altitude.data[2].y) == [10.5]
    assert abs(altitude.data[1].x[0] - 0.2) < 1e-10


def test_diagnostic_filter_order_and_empty_state():
    from visualization.v1.training_diagnostics import filter_diagnostic_windows

    rows = [dict(sequence_id="a", observed_vertical="up", observed_xy_movement="moving",
                 axis_rmse_z_m=1, ade_m=8),
            dict(sequence_id="b", observed_vertical="down", observed_xy_movement="small",
                 axis_rmse_z_m=5, ade_m=2)]
    assert filter_diagnostic_windows(rows, None, None, "ade_m")[0]["sequence_id"] == "a"
    assert filter_diagnostic_windows(rows, "down", None, "axis_rmse_z_m")[0]["sequence_id"] == "b"
    assert filter_diagnostic_windows(rows, "up", "small", "ade_m") == []


def test_old_run_diagnostic_callback_reports_unavailable():
    from visualization.v1.training_ui import register_training_callbacks, training_panel

    service = MockService()
    service.diagnostic_windows = lambda *args: {"available": False, "windows": [],
                                               "vertical_options": [], "xy_options": []}
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    callback = next(value["callback"].__wrapped__ for key, value in app.callback_map.items()
                    if "training-diagnostic-catalog.data" in key)
    result = callback("old", "best", 0)
    assert "없습니다" in result[1]
    assert result[0]["available"] is False
    empty = callback(None, "best", 0)
    assert "선택" in empty[1]
