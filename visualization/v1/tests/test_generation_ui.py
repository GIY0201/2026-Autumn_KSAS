"""Tests for explicit dataset selection and the local generation control surface."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_generation.v1 import service
from data_generation.v1.dataset import GenerationRequest, write_dataset
from data_generation.v1.service import (
    GenerationJob,
    GenerationPreset,
    GenerationService,
    ObjectSupport,
)
from data_generation.v1.tests.test_service import FakeProcess, _finish
from visualization.v1.app import _PROJECT_ROOT, create_app
from visualization.v1.figures import load_viewer_dataset
from visualization.v1.generation_ui import DatasetRegistry, generation_panel
from visualization.v1.tests.test_app import _component_by_id, _dataset_path, _episode


def test_generation_panel_uses_service_presets_and_never_starts_on_page_load(tmp_path):
    jobs = GenerationService(output_root=tmp_path / "outputs", work_root=tmp_path / "jobs")
    app = create_app(_dataset_path(tmp_path), generation_service=jobs)
    assert jobs.snapshot() is None
    assert not jobs.work_root.exists()
    presets = _component_by_id(app.layout, "generation-preset")
    assert presets.options == [
        {"label": item.label, "value": item.preset_id} for item in jobs.presets
    ]
    assert _component_by_id(app.layout, "generation-seed").value == jobs.presets[0].default_seed
    assert _component_by_id(app.layout, "generation-start").disabled is False
    assert _component_by_id(app.layout, "generation-open").disabled is True
    assert _component_by_id(app.layout, "generation-cancel").disabled is True


def test_panel_derives_vtol_and_helicopter_availability_from_real_presets(tmp_path):
    jobs = GenerationService(output_root=tmp_path / "outputs", work_root=tmp_path / "jobs")
    app = create_app(_dataset_path(tmp_path), generation_service=jobs)
    support = _component_by_id(app.layout, "generation-object-support")
    rendered = str(support)
    assert "VTOL" in rendered
    assert "헬기" in rendered
    assert "생성 가능" in rendered
    assert {"diagnostic_vtol", "diagnostic_helicopter"} <= {
        preset.preset_id for preset in jobs.presets
    }
    assert jobs.snapshot() is None


def test_panel_retains_the_unavailable_path_for_controlled_service_metadata(tmp_path):
    initial = load_viewer_dataset(_dataset_path(tmp_path))
    registry = DatasetRegistry(initial, public_only=False)
    controlled_service = SimpleNamespace(
        presets=(
            GenerationPreset(
                "diagnostic",
                "테스트",
                "테스트 설명",
                1,
                17,
            ),
        ),
        object_support=(
            ObjectSupport(
                "vtol",
                "VTOL",
                "실행 가능한 VTOL profile/config가 아직 등록되지 않았습니다.",
                (),
            ),
        ),
    )

    rendered = str(generation_panel(controlled_service, registry))

    assert "VTOL" in rendered
    assert "생성 불가" in rendered
    assert "profile/config" in rendered


def test_export_root_does_not_redirect_generated_data(tmp_path):
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "image-exports")
    assert app.server.extensions["generation_service"].output_root == _PROJECT_ROOT / "outputs"


def test_data_callbacks_resolve_the_selected_dataset_not_a_global_current_value(tmp_path):
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "outputs")
    for fragment in ("figure-xy.figure", "figure-xy.extendData", "export-status.children"):
        callback = next(value for key, value in app.callback_map.items() if fragment in key)
        assert "dataset-key" in {item["id"] for item in callback["state"]}
    select = _component_by_id(app.layout, "dataset-select")
    assert select.value == _component_by_id(app.layout, "dataset-key").data


def test_registry_rejects_arbitrary_paths_and_keeps_each_dataset_independent(tmp_path):
    initial = load_viewer_dataset(_dataset_path(tmp_path))
    registry = DatasetRegistry(initial, public_only=False)
    second = write_dataset(
        [replace(_episode(), episode_id="x8-00000099")],
        request=GenerationRequest(99, "diagnostic", "NOT_RUN"),
        output_root=tmp_path / "outputs",
    )
    job = GenerationJob(
        "job-99",
        "diagnostic",
        "테스트 데이터",
        99,
        1,
        tmp_path,
        state="complete",
        result_path=second.path,
    )
    registry.add_results((job,))
    assert registry.get(initial.source_path.name) is initial
    assert registry.get(second.path.name).episode_ids == ("x8-00000099",)
    assert registry.get(initial.source_path.name).episode_ids == initial.episode_ids
    with pytest.raises(ValueError, match="dataset"):
        registry.get("../../data_sources")


def test_new_result_in_public_only_mode_never_loads_evaluation(tmp_path, monkeypatch):
    first_path = _dataset_path(tmp_path)
    initial = load_viewer_dataset(first_path, public_only=True)
    registry = DatasetRegistry(initial, public_only=True)
    second_path = _dataset_path(tmp_path)
    job = GenerationJob(
        "job",
        "diagnostic",
        "테스트 데이터",
        17,
        1,
        tmp_path,
        state="complete",
        result_path=second_path,
    )
    registry.add_results((job,))

    def forbid_truth(*args, **kwargs):
        raise AssertionError("public-only selection must not load truth")

    monkeypatch.setattr("visualization.v1.figures._load_truth", forbid_truth)
    selected = registry.get(second_path.name)
    assert not selected.has_truth


def test_failed_job_never_becomes_a_dataset_option(tmp_path):
    initial = load_viewer_dataset(_dataset_path(tmp_path))
    registry = DatasetRegistry(initial, public_only=False)
    registry.add_results(
        (
            GenerationJob(
                "bad",
                "diagnostic",
                "실패",
                17,
                1,
                tmp_path,
                state="failed",
                result_path=Path("not-a-dataset"),
            ),
        )
    )
    assert len(registry.options) == 1


def _post(app, fragment, inputs, states, changed):
    key, callback = next((key, value) for key, value in app.callback_map.items() if fragment in key)
    outputs = callback["output"]

    def output_spec(item):
        return {"id": item.component_id, "property": item.component_property}

    response = app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": key,
            "outputs": (
                [output_spec(item) for item in outputs]
                if isinstance(outputs, list)
                else output_spec(outputs)
            ),
            "inputs": [{**item, "value": inputs[item["id"]]} for item in callback["inputs"]],
            "state": [{**item, "value": states[item["id"]]} for item in callback["state"]],
            "changedPropIds": [changed],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["response"]


@pytest.mark.parametrize("public_only", [False, True])
def test_http_generate_poll_open_and_render_flow(tmp_path, monkeypatch, public_only):
    """Exercise actual Dash callback requests, not just the presence of buttons."""
    monkeypatch.setattr(service, "Popen", FakeProcess)
    jobs = GenerationService(output_root=tmp_path / "outputs", work_root=tmp_path / "jobs")
    app = create_app(_dataset_path(tmp_path), generation_service=jobs, public_only=public_only)
    initial_key = _component_by_id(app.layout, "dataset-select").value
    action_inputs = {"generation-start": 1, "generation-cancel": 0, "generation-open": 0}
    action_state = {
        "generation-preset": "diagnostic",
        "generation-seed": 42,
        "generation-job": None,
        "generation-refresh": 0,
    }
    started = _post(
        app,
        "generation-action-feedback.children",
        action_inputs,
        action_state,
        "generation-start.n_clicks",
    )
    assert "dataset-select" not in started
    assert jobs.snapshot().state == "running"
    finished = _finish(jobs, monkeypatch)
    assert finished.state == "complete"
    poll = _post(
        app,
        "generation-status.children",
        {"generation-poll": 1, "generation-refresh": 1},
        {"dataset-select": _component_by_id(app.layout, "dataset-select").options},
        "generation-poll.n_intervals",
    )
    assert poll["generation-open"]["disabled"] is False
    assert len(poll["dataset-select"]["options"]) == 2
    action_inputs["generation-open"] = 1
    action_state.update({"generation-job": poll["generation-job"]["data"], "generation-refresh": 1})
    opened = _post(
        app,
        "generation-action-feedback.children",
        action_inputs,
        action_state,
        "generation-open.n_clicks",
    )
    new_key = opened["dataset-select"]["value"]
    assert new_key != initial_key
    layout = _post(
        app, "viewer-container.children", {"dataset-select": new_key}, {}, "dataset-select.value"
    )
    assert layout["dataset-load-error"]["children"] == ""
    selected = load_viewer_dataset(finished.result_path, public_only=public_only)
    figure_inputs = {"episode-select": list(selected.episode_ids), "variant-select": "sigma_3m"}
    if not public_only:
        figure_inputs["truth-toggle"] = ["show"]
    figures = _post(
        app,
        "figure-xy.figure",
        figure_inputs,
        {"dataset-key": new_key, "player-state": {"step": 0, "playing": False}},
        "episode-select.value",
    )
    assert figures["figure-xy"]["figure"]["data"]
    markers = _post(
        app,
        "figure-xy.extendData",
        {"player-state": {"step": 1, "playing": True}},
        {**figure_inputs, "dataset-key": new_key},
        "player-state.data",
    )
    marker_data, marker_trace_indexes, max_points = markers["figure-xy"][
        "extendData"
    ]
    episode_id = selected.episode_ids[0]
    observed = selected.observed(episode_id, "sigma_3m")[1]
    assert max_points == 1
    if public_only:
        assert marker_trace_indexes == [1]
        assert marker_data["x"][0][0] == pytest.approx(observed[0])
        assert marker_data["y"][0][0] == pytest.approx(observed[1])
    else:
        truth = selected.truth(episode_id)[1]
        assert marker_trace_indexes == [2, 3]
        assert marker_data["x"][0][0] == pytest.approx(truth[0])
        assert marker_data["y"][0][0] == pytest.approx(truth[1])
        assert marker_data["x"][1][0] == pytest.approx(observed[0])
        assert marker_data["y"][1][0] == pytest.approx(observed[1])
    jobs.close()
