"""Training UI contracts without starting GPU jobs."""

from types import SimpleNamespace

from dash import Dash

from visualization.v1.training_ui import (
    merge_parameters,
    metric_figure,
    prediction_figure,
    register_training_callbacks,
    training_panel,
)


class MockService:
    def plugins(self):
        return [
            {
                "id": "fixture",
                "defaults": {
                    "model": {"hidden_size": 128},
                    "data": {"dataset_path": ""},
                    "training": {"epochs": 200},
                },
                "contract": {"input_samples": 16, "output_samples": 75},
            }
        ]

    def datasets(self):
        return [{"label": "Fixture dataset", "value": "fixture-path"}]

    def presets(self, plugin_id):
        return []

    def runs(self):
        return []


def test_all_training_graphs_resize_to_their_container():
    from visualization.v1.tests.test_app import _component_by_id

    panel = training_panel(MockService())
    for suffix in ("loss", "errors", "horizons", "axes", "runtime", "prediction"):
        graph = _component_by_id(panel, f"training-{suffix}")
        assert graph.responsive is True


def test_mock_dashboard_layout_and_callbacks_render():
    service = MockService()
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    client = app.server.test_client()
    response = client.get("/_dash-layout")
    assert response.status_code == 200
    assert b"training-start" in response.data
    assert b"training-resume" in response.data
    callback = next(
        v["callback"].__wrapped__
        for k, v in app.callback_map.items()
        if "training-parameters.children" in k
    )
    fields, config, contract, _dataset = callback("fixture", None, None)
    assert len(fields) == 2
    assert config["model"]["hidden_size"] == 128
    assert "75" in contract


def test_empty_run_registry_is_honest():
    service = MockService()
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    callback = next(
        v["callback"].__wrapped__
        for k, v in app.callback_map.items()
        if "training-runs.children" in k
    )
    result = callback(0, None, None)
    assert result[1] == []
    assert result[3] is True
    assert len(result[4].data) == 0


def test_start_and_resume_use_explicit_dataset_and_checkpoint(monkeypatch):
    import visualization.v1.training_ui as ui

    service = MockService()
    calls = []
    service.start = lambda config, name, parent_checkpoint: (
        calls.append((config, name, parent_checkpoint)) or "new-run"
    )
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    callback = next(
        v["callback"].__wrapped__
        for k, v in app.callback_map.items()
        if "training-message.children" in k
    )
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="training-resume"))
    result = callback(
        0,
        0,
        0,
        1,
        {"training": {"max_epochs": 200}},
        [{"path": "training.max_epochs"}],
        [300],
        "fixture",
        "explicit-path",
        "실험",
        None,
        None,
        "parent-run",
        "last",
    )
    assert result[1] == "new-run"
    assert result[2] is False
    assert result[3] == "new-run"
    assert calls[0][0]["data"]["dataset_path"] == "explicit-path"
    assert calls[0][2] == "parent-run/last"


def test_start_failure_is_visible_without_claiming_a_run(monkeypatch):
    import visualization.v1.training_ui as ui

    service = MockService()
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    callback = next(
        v["callback"].__wrapped__
        for k, v in app.callback_map.items()
        if "training-message.children" in k
    )
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="training-start"))
    result = callback(1, 0, 0, 0, {}, [], [], "fixture", None, "name", None, None, None, "last")
    assert "Dataset" in result[0]
    assert result[1] is ui.no_update


def test_display_values_are_compact_and_keep_units():
    from visualization.v1.training_ui import display_value

    assert display_value("dataset_path", r"C:\outputs\data\dataset-name") == "dataset-name"
    assert display_value("best_ade_m", 12.123456) == "12.123"
    assert display_value("elapsed_s", 3661.2) == "1:01:01"
    assert (
        display_value("created_at", "2026-09-08T09:25:14.12345+00:00") == "2026-09-08 09:25:14 UTC"
    )


def test_parameter_editor_preserves_nested_config():
    original = {"model": {"hidden_size": 128}, "seed": 17}
    result = merge_parameters(original, [{"path": "model.hidden_size"}], [256])
    assert result["model"]["hidden_size"] == 256
    assert original["model"]["hidden_size"] == 128


def test_metrics_do_not_join_train_and_validation():
    figure = metric_figure(
        [
            {"global_step": 1, "split": "train", "loss": 2.0},
            {"global_step": 2, "split": "validation", "loss": 1.0},
        ],
        ["loss"],
        "Loss",
    )
    assert len(figure.data) == 2
    assert list(figure.data[0].y) == [2.0]


def test_real_trainer_eval_metric_names_render():
    rows = [
        {
            "global_step": 44,
            "split": "validation",
            "eval_loss": 0.01,
            "eval_error_1s_m": 1.2,
            "eval_axis_rmse_x_m": 2.3,
        }
    ]
    figure = metric_figure(rows, ["error_1s_m", "axis_rmse_x_m"], "Errors")
    assert len(figure.data) == 2
    assert list(figure.data[0].y) == [1.2]
    assert list(figure.data[1].y) == [2.3]


def test_parent_child_series_have_explicit_resume_marker():
    rows = [
        {"global_step": 10, "split": "train", "loss": 1.0, "lineage": "parent"},
        {
            "global_step": 11,
            "split": "train",
            "loss": 0.9,
            "lineage": "child",
            "resume_global_step": 10,
        },
    ]
    figure = metric_figure(rows, ["loss"], "Loss")
    assert len(figure.data) == 2
    assert figure.layout.shapes[0].x0 == 10


def test_http_poll_renders_real_worker_metrics_and_resume():
    from visualization.v1.tests.test_generation_ui import _post

    service = MockService()
    service.runs = lambda: [{"run_id": "child", "display_name": "Child"}]
    service.status = lambda run: {
        "status": "running",
        "parent_run": "parent",
        "resume_global_step": 44,
    }
    service.metrics = lambda run: [
        {
            "global_step": 44 if run == "parent" else 88,
            "split": "validation",
            "eval_loss": 0.1,
            "eval_error_1s_m": 1.2,
            "eval_axis_rmse_x_m": 2.3,
        }
    ]
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    result = _post(
        app,
        "training-runs.children",
        {"training-tick": 1, "training-active": "child", "training-run": None},
        {},
        "training-tick.n_intervals",
    )
    assert len(result["training-horizons"]["figure"]["data"]) == 2
    assert result["training-horizons"]["figure"]["layout"]["shapes"][0]["x0"] == 44
    assert len(result["training-axes"]["figure"]["data"]) == 2


def test_prediction_uses_equal_physical_3d_axes():
    figure = prediction_figure({"predicted": [[0, 0, 0], [1, 2, 3]]})
    assert figure.layout.scene.aspectmode == "data"
    assert list(figure.data[0].z) == [0, 3]


def test_cudnn_boolean_inspect_render_save_and_resume(monkeypatch):
    """A saved explicit backend choice survives the generic form round trip."""
    import visualization.v1.training_ui as ui

    service = MockService()
    saved = {
        "plugin_id": "fixture",
        "data": {"dataset_path": "explicit-path"},
        "training": {"cudnn_enabled": False, "max_epochs": 200},
    }
    service.status = lambda selected: {"config": saved}
    calls = []
    service.save_preset = lambda name, config: calls.append(("save", name, config))
    service.start = lambda config, name, parent_checkpoint: (
        calls.append(("resume", parent_checkpoint, config)) or "new-child"
    )
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    parameters = next(v["callback"].__wrapped__ for k, v in app.callback_map.items()
                      if "training-parameters.children" in k)
    action = next(v["callback"].__wrapped__ for k, v in app.callback_map.items()
                  if "training-message.children" in k)
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="training-inspect"))
    fields, config, _, dataset = parameters("fixture", None, 1, "saved")
    control = next(field.children[1] for field in fields
                   if field.children[1].id["path"] == "training.cudnn_enabled")
    assert control.value == "false"
    assert control.options == ["true", "false"]
    assert config["training"]["cudnn_enabled"] is False
    assert dataset == "explicit-path"
    identifiers = [{"path": "training.cudnn_enabled"}, {"path": "training.max_epochs"}]
    for trigger in ("training-save", "training-resume"):
        monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id=trigger))
        action(0, 0, 1, 1, config, identifiers, ["false", 200], "fixture", dataset,
               "Child", "Backend preset", None, "saved", "last")
    assert calls[0][0] == "save"
    assert calls[1][:2] == ("resume", "saved/last")
    assert all(call[2]["training"]["cudnn_enabled"] is False for call in calls)
    assert merge_parameters(config, identifiers, ["true", 200])["training"]["cudnn_enabled"] is True
    assert saved["training"]["cudnn_enabled"] is False


def test_inspecting_old_run_does_not_inject_backend_setting(monkeypatch):
    import visualization.v1.training_ui as ui

    service = MockService()
    saved = {"plugin_id": "fixture", "training": {"max_epochs": 200},
             "data": {"dataset_path": "old-path"}}
    service.status = lambda selected: {"config": saved}
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    parameters = next(v["callback"].__wrapped__ for k, v in app.callback_map.items()
                      if "training-parameters.children" in k)
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="training-inspect"))
    _, config, _, _ = parameters("fixture", None, 1, "old")
    assert config == saved
    assert "cudnn_enabled" not in config["training"]


def test_new_gru_default_backend_is_visible_and_false(tmp_path):
    """Versioned new-run defaults are visible without rewriting old run configs."""
    from models.runtime.v1.service import TrainingService

    service = TrainingService(tmp_path)
    app = Dash(__name__)
    app.layout = training_panel(service)
    register_training_callbacks(app, service)
    parameters = next(v["callback"].__wrapped__ for k, v in app.callback_map.items()
                      if "training-parameters.children" in k)
    fields, config, _, _ = parameters("gru_direct_v1", None)
    assert config["training"]["cudnn_enabled"] is False
    control = next(field.children[1] for field in fields
                   if field.children[1].id["path"] == "training.cudnn_enabled")
    assert control.value == "false"
    assert control.options == ["true", "false"]
