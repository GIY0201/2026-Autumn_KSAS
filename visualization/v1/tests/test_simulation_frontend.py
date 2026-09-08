import hashlib
import json
from pathlib import Path

WEB = Path(__file__).parents[1] / "simulation_web"


def test_simulation_assets_are_local_and_have_two_accessible_tabs():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.count('role="tab"') == 2
    assert 'role="tabpanel"' in html
    assert 'https://' not in html
    assert 'simulation.js' in html


def test_client_keeps_physics_on_server_and_releases_controls():
    js = (WEB / "simulation.js").read_text(encoding="utf-8")
    markers = [
        'visibilitychange', 'blur', 'keyup', 'keydown', 'simulationDiagnostics',
        '/api/simulation/', 'requestAnimationFrame',
    ]
    for marker in markers:
        assert marker in js
    assert 'eval(' not in js


def test_vendor_files_match_pinned_manifest():
    manifest = json.loads((WEB / "vendor/provenance.json").read_text())
    assert manifest['version']
    for name, digest in manifest['sha256'].items():
        assert hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest() == digest


def test_mutations_are_owned_ordered_and_do_not_discard_runtime_errors():
    js = (WEB / "simulation.js").read_text(encoding="utf-8")
    assert "mutationChain.then(dispatch)" in js
    assert "client_id:clientId" in js
    assert "checkpoint_sha256:" in js
    assert "if (!response.ok)" in js
    assert "!response.ok || result.error" not in js
    assert "if(ownsRun()&&running())navigator.sendBeacon" in js
    assert "payload.object_id=body.object_id" in js
    assert "revision !== mutationRevision" in js


def test_short_key_edges_keep_snapshots_and_reject_new_run():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable")
    module = (WEB / "input_protocol.js").resolve().as_uri()
    program = f"""
import {{captureInput,isCurrentInput}} from '{module}';
import assert from 'node:assert/strict';
const state={{run_id:'run-a',selected_id:'object-a'}};
const keys=new Set(['KeyT']);
const down=captureInput(keys,state,1);
keys.clear();
const up=captureInput(keys,state,1);
assert.deepEqual(down.keys,['KeyT']);
assert.deepEqual(up.keys,[]);
assert.equal(isCurrentInput(down,state,1),true);
assert.equal(isCurrentInput(down,{{...state,run_id:'run-b'}},1),false);
assert.equal(isCurrentInput(down,{{...state,selected_id:'object-b'}},1),false);
assert.equal(isCurrentInput(down,state,2),false);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", program], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
