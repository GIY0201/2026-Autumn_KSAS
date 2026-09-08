"""Explicit project-local model plugin registry."""

from __future__ import annotations

import copy


class Registry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin: dict) -> None:
        identifier = plugin.get("id")
        if not identifier or identifier in self._plugins:
            raise ValueError(f"Missing or duplicate plugin ID: {identifier}")
        self._plugins[identifier] = plugin

    def get(self, identifier: str) -> dict:
        if identifier not in self._plugins:
            raise ValueError(f"Unknown model plugin: {identifier}")
        return self._plugins[identifier]


def _registry() -> Registry:
    from models.gru.v1.plugin import get_plugin as gru_plugin

    registry = Registry()
    registry.register(gru_plugin())
    return registry


def get_plugin(identifier: str) -> dict:
    return _registry().get(identifier)


def list_plugins() -> list[dict]:
    return [
        copy.deepcopy({k: v for k, v in item.items() if not callable(v)})
        for item in _registry()._plugins.values()
    ]
