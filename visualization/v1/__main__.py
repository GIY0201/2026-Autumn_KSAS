"""Command-line entry point for the local X8-GEN-V1 trajectory viewer."""

from __future__ import annotations

import argparse
from pathlib import Path

from .app import create_app

_LOCAL_VIEWER_HOST = "127.0.0.1"
_LOCAL_VIEWER_PORT = 8088


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local X8-GEN-V1 trajectory viewer.")
    parser.add_argument(
        "--dataset-path", type=Path, required=True, help="Explicit generated dataset directory."
    )
    parser.add_argument(
        "--public-only", action="store_true", help="Do not load evaluation-only truth."
    )
    parser.add_argument(
        "--output-root", type=Path, default=None, help="Root containing visualization outputs."
    )
    parser.add_argument("--host", default=_LOCAL_VIEWER_HOST, choices=("127.0.0.1", "localhost"))
    parser.add_argument(
        "--port",
        type=int,
        default=_LOCAL_VIEWER_PORT,
        choices=(_LOCAL_VIEWER_PORT,),
        help=f"Fixed local viewer port: {_LOCAL_VIEWER_PORT}.",
    )
    return parser.parse_args()


def main() -> None:
    """Create and run the viewer without exposing it on a non-local interface."""
    arguments = _arguments()
    if not 1 <= arguments.port <= 65535:
        raise ValueError("port must be from 1 through 65535")
    app = create_app(
        arguments.dataset_path,
        public_only=arguments.public_only,
        output_root=arguments.output_root,
    )
    # A single request worker preserves ordering between a Pause click and a pending tick.
    try:
        app.run(host=arguments.host, port=arguments.port, debug=False, threaded=False)
    finally:
        app.server.extensions["generation_service"].close()


if __name__ == "__main__":
    main()
