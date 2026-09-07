"""Tests for the local trajectory viewer command-line entry point."""

from __future__ import annotations

import sys

import pytest

from visualization.v1 import __main__ as viewer_cli


def test_cli_defaults_to_the_fixed_local_port_8088(monkeypatch) -> None:
    """The local viewer must not silently select an arbitrary fallback port."""
    monkeypatch.setattr(sys, "argv", ["visualization.v1", "--dataset-path", "dataset"])

    arguments = viewer_cli._arguments()

    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8088


def test_cli_rejects_any_port_other_than_8088(monkeypatch) -> None:
    """The viewer must not create an arbitrary second local listener."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["visualization.v1", "--dataset-path", "dataset", "--port", "8050"],
    )

    with pytest.raises(SystemExit):
        viewer_cli._arguments()
