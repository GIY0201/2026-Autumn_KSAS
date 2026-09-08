import uuid

from flask import Flask

from data_generation.v1.simulation_service import SimulationService
from data_generation.v1.tests.test_simulation_service import Engine
from visualization.v1.simulation_routes import register_simulation_routes


def test_routes_reject_foreign_origin(tmp_path):
    app = Flask(__name__)
    service = SimulationService(tmp_path, engine_factory=Engine, background=False)
    register_simulation_routes(app, tmp_path, service)
    client = app.test_client()
    assert (
        client.post(
            "/api/simulation/create", json={}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/simulation/create",
            json={"client_id": str(uuid.uuid4())},
            headers={"Origin": "http://127.0.0.1:8088"},
        ).status_code
        == 200
    )
    assert client.get("/api/simulation/state").json["status"] == "ready"
    service.close()


def test_owner_and_run_guard(tmp_path):
    app = Flask(__name__)
    service = SimulationService(tmp_path, background=False)
    register_simulation_routes(app, tmp_path, service)
    client = app.test_client()
    headers = {"Origin": "http://127.0.0.1:8088"}
    owner = str(uuid.uuid4())
    state = client.post(
        "/api/simulation/create",
        json={"client_id": owner, "objects": [{"kind": "helicopter"}]},
        headers=headers,
    ).json
    identity = {"client_id": owner, "run_id": state["run_id"]}
    assert client.post("/api/simulation/start", json=identity, headers=headers).status_code == 200
    assert (
        client.post(
            "/api/simulation/pause",
            json=dict(identity, client_id=str(uuid.uuid4())),
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/simulation/keys", json=dict(identity, object_id="old", keys=[]), headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/simulation/pause", json=dict(identity, run_id="old"), headers=headers
        ).status_code
        == 409
    )
    assert service.engine.status == "running"
    assert client.post("/api/simulation/stop", json=identity, headers=headers).status_code == 200
    service.close()
