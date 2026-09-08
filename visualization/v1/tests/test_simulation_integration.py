"""Simulation is a separate page on the existing Dash server."""

from visualization.v1.app import create_app
from visualization.v1.tests.test_app import _dataset_path


def test_simulation_route_coexists_with_viewer(tmp_path):
    app = create_app(_dataset_path(tmp_path), output_root=tmp_path / "outputs")
    try:
        client = app.server.test_client()
        assert b"simulation-assets/simulation.js" in client.get("/simulation").data
        assert client.get("/_dash-layout").status_code == 200
        assert "workspace-tabs.value" in app.callback_map
    finally:
        for service in app.server.extensions.values():
            if hasattr(service, "close"):
                service.close()
