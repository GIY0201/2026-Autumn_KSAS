const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('camera gestures suspend updates until all pointers release, including cancel and blur', () => {
    const handlers = {};
    const updates = [];
    const surface = { addEventListener: (name, fn) => { handlers[name] = fn; } };
    const context = {
        document: surface,
        window: { ...surface, dash_clientside: { set_props: (id, value) => updates.push([id, value.data]) } },
        setTimeout, clearTimeout,
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/camera_interaction.js'), 'utf8'), context);
    const target = { closest: () => ({}) };
    handlers.pointerdown({ target, pointerId: 1 });
    handlers.pointerdown({ target, pointerId: 2 });
    handlers.pointerup({ pointerId: 1 });
    assert.deepEqual(updates, [['camera-interacting', true]]);
    handlers.pointercancel({ pointerId: 2 });
    assert.equal(updates.at(-1)[1], false);
    handlers.pointerdown({ target, pointerId: 3 });
    handlers.blur();
    assert.equal(updates.at(-1)[1], false);
});
