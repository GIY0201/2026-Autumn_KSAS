"""Local-only HTTP bridge for the separate simulation page."""

from pathlib import Path

from flask import jsonify, request, send_from_directory

from data_generation.v1.simulation_service import SimulationService


def register_simulation_routes(server, project_root, service=None):
    service = service or SimulationService(project_root)
    assets = Path(__file__).with_name("simulation_web")

    @server.get("/simulation")
    def simulation_page():
        return send_from_directory(assets, "index.html")

    @server.get("/simulation-assets/<path:filename>")
    def simulation_assets(filename):
        return send_from_directory(assets, filename)

    @server.get("/api/simulation/catalog")
    def simulation_catalog():
        return jsonify(service.catalog())

    @server.get("/api/simulation/state")
    def simulation_state():
        return jsonify(service.state())

    @server.post("/api/simulation/<action>")
    def simulation_action(action):
        if request.headers.get("Origin") not in ("http://127.0.0.1:8088", "http://localhost:8088"):
            return jsonify(error="Only the local simulation page may change state."), 403
        if request.content_length and request.content_length > 16_000_000:
            return jsonify(error="Request exceeds size limit."), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="JSON object required."), 400
        try:
            with service.lock:
                service.authorize(action, payload)
                if action == "create":
                    result = service.create(payload)
                elif action == "export":
                    result = service.export_png(payload.get("png"))
                else:
                    result = service.command(action, payload)
            return jsonify(result)
        except PermissionError as exc:
            return jsonify(error=str(exc)), 409
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    return service
