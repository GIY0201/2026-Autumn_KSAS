/* Camera gestures must not compete with the saved-sample playback renderer. */
(function () {
    'use strict';
    const pointers = new Set();
    let active = false;
    let wheelTimer;
    let wheeling = false;
    function publish() {
        const next = pointers.size > 0 || wheeling;
        if (next !== active && window.dash_clientside?.set_props) {
            active = next;
            window.dash_clientside.set_props('camera-interacting', { data: active });
        }
    }
    document.addEventListener('pointerdown', function (event) {
        if (event.target.closest?.('#figure-3d .js-plotly-plot')) {
            pointers.add(event.pointerId);
            publish();
        }
    }, true);
    function release(event) {
        pointers.delete(event.pointerId);
        publish();
    }
    window.addEventListener('pointerup', release, true);
    window.addEventListener('pointercancel', release, true);
    document.addEventListener('wheel', function (event) {
        if (!event.target.closest?.('#figure-3d .js-plotly-plot')) return;
        wheeling = true;
        clearTimeout(wheelTimer);
        publish();
        wheelTimer = setTimeout(function () { wheeling = false; publish(); }, 200);
    }, { passive: true, capture: true });
    window.addEventListener('blur', function () {
        pointers.clear();
        wheeling = false;
        clearTimeout(wheelTimer);
        publish();
    });
})();
