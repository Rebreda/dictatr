// What the compositor knows and a Wayland client cannot ask: which
// application has focus, and where the pointer is going.
//
// The tray loads this at startup and unloads it on exit. Everything it
// learns leaves over DBus; nothing is stored here.
//
// KWin's script engine has no timers (no setInterval, no QML objects),
// so anything periodic has to hang off a signal. That is why the shake
// detector below is written as a state machine over motion events
// rather than as a sampler.

var TRAY = "io.github.rebreda.dictatr.tray";
var OBJ = "/Shortcuts";
var IFACE = "io.github.rebreda.dictatr.Shortcuts";

// --- which application has focus -------------------------------------

// Transient system surfaces (portal permission dialogs, the shortcut
// prompt) steal focus for a moment and are not where the user is.
var IGNORE = /^org\.freedesktop\.impl\.portal|^xdg-desktop-portal/;

function report(window) {
    if (!window || window.specialWindow) {
        return;
    }
    // A permission prompt or a save dialog is not where you are working;
    // it borrows focus for a moment and its parent is the real answer.
    if (window.dialog || window.transient || window.popupWindow) {
        return;
    }
    if (IGNORE.test(String(window.resourceClass || ""))) {
        return;
    }
    // The pid travels too: dictatr's own surfaces are layer-shell and
    // carry no app id, so they all report as the interpreter. The tray
    // recognises its own and keeps the app you were actually in.
    callDBus(TRAY, OBJ, IFACE, "ActiveApp",
             String(window.resourceClass || ""), String(window.pid || 0));
}

workspace.windowActivated.connect(report);
report(workspace.activeWindow);

// --- pointer gestures --------------------------------------------------
//
// This end stays deliberately stupid. The script engine has no timers,
// no numbers library and nothing here can be tested, so it does not
// decide anything: it keeps the last stretch of pointer movement and,
// when enough has happened to be worth a look, hands the trace to the
// tray. Every judgement about what was drawn is made in
// src/dictatr/gestures.py, where it can be tested against traces.
//
// The gate is in screen heights rather than pixels, so it means the
// same thing on a laptop panel and a 4K monitor.

var SPAN_MS = 1600;        // how much movement to remember
var GATE = 0.45;           // screen heights of movement worth judging
var RETURN_MAX = 0.4;      // end displacement, as a share of what was drawn
var STEP = 0.008;          // screen heights: nearer than this is the same place
var MAX_POINTS = 256;      // backstop on how long a trail may grow
var QUIET_MS = 900;        // wait after handing one over

var trail = [];            // {t, x, y}
var drawn = 0;             // distance in the trail, in pixels
var handedAt = 0;

function now() {
    return new Date().getTime();
}

function screenHeight() {
    var size = workspace.virtualScreenSize;
    return size && size.height ? size.height : 1080;
}

function watch() {
    var p = workspace.cursorPos;
    var t = now();
    var h = screenHeight();
    var last = trail.length ? trail[trail.length - 1] : null;
    if (last) {
        var dx = p.x - last.x;
        var dy = p.y - last.y;
        var step = Math.sqrt(dx * dx + dy * dy);
        // A pointer reports far finer than a gesture is drawn: a step
        // is a pixel or two, which is quantisation noise and not shape.
        // Thinning here makes what a trace costs follow the movement
        // instead of the hardware -- a 1000Hz mouse and a 125Hz
        // touchpad drawing the same circle send about the same trace,
        // where before one sent eight times the other. No verdict in
        // gestures.py moves; there is a test for that. Dropping the
        // point rather than only omitting it from the message also
        // keeps drawn measured over exactly what the tray will see.
        if (step < STEP * h) {
            return;
        }
        drawn += step;
    }
    trail.push({t: t, x: p.x, y: p.y});

    while (trail.length > 1 &&
           (t - trail[0].t > SPAN_MS || trail.length > MAX_POINTS)) {
        var a = trail.shift();
        var b = trail[0];
        drawn -= Math.sqrt((b.x - a.x) * (b.x - a.x) +
                           (b.y - a.y) * (b.y - a.y));
    }
    if (drawn < 0) {
        drawn = 0;
    }

    if (drawn < GATE * h || t - handedAt < QUIET_MS || trail.length < 8) {
        return;
    }
    // The one judgement worth making here, because it is the cheapest
    // and it rejects the commonest movement there is: a gesture ends
    // near where it began, and a sweep across the screen does not. The
    // tray would reach the same verdict after parsing a few kilobytes.
    // Deliberately looser than MAX_RETURN in gestures.py, so nothing
    // the tray would have accepted can be dropped here; the tray still
    // decides. Checked on every event, so a shake that is lopsided
    // halfway through is simply handed over once it comes back.
    var ex = trail[trail.length - 1].x - trail[0].x;
    var ey = trail[trail.length - 1].y - trail[0].y;
    if (Math.sqrt(ex * ex + ey * ey) > RETURN_MAX * drawn) {
        return;
    }
    handedAt = t;

    var parts = [workspace.virtualScreenSize.width + "x" + h];
    for (var i = 0; i < trail.length; i++) {
        parts.push((trail[i].t - trail[0].t) + "," +
                   Math.round(trail[i].x) + "," + Math.round(trail[i].y));
    }
    callDBus(TRAY, OBJ, IFACE, "Trace", parts.join(" "));
}

workspace.cursorPosChanged.connect(watch);
