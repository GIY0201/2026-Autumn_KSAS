"""Regression tests for Windows reader/writer sharing conflicts."""
import json

import pytest

from models.runtime.v1 import io


def test_atomic_json_retries_transient_reader_lock(tmp_path, monkeypatch):
    target = tmp_path / 'status.json'
    target.write_text('{"status":"running"}')
    replace = io.os.replace
    calls = []

    def locked_once(source, destination):
        calls.append((source, destination))
        if len(calls) == 1:
            raise PermissionError(13, 'simulated Windows sharing violation')
        replace(source, destination)

    monkeypatch.setattr(io.os, 'replace', locked_once)
    io.atomic_json(target, {'status': 'completed'})
    assert len(calls) == 2
    assert json.loads(target.read_text()) == {'status': 'completed'}
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_json_permanent_lock_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / 'status.json'
    original = b'{"status":"running"}'
    target.write_bytes(original)
    calls = []

    def locked(source, destination):
        calls.append(destination)
        raise PermissionError(13, 'persistent lock')

    monkeypatch.setattr(io.os, 'replace', locked)
    with pytest.raises(PermissionError, match='persistent lock'):
        io.atomic_json(target, {'status': 'completed'})
    assert len(calls) > 1
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]
