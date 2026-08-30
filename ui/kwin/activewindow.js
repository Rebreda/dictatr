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
var GATE = 0.4;            // screen heights of movement worth judging
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
    var last = trail.length ? trail[trail.length - 1] : null;
    if (last) {
        var dx = p.x - last.x;
        var dy = p.y - last.y;
        if (dx === 0 && dy === 0) {
            return;
        }
        drawn += Math.sqrt(dx * dx + dy * dy);
    }
    trail.push({t: t, x: p.x, y: p.y});

    while (trail.length > 1 && t - trail[0].t > SPAN_MS) {
        var a = trail.shift();
        var b = trail[0];
        drawn -= Math.sqrt((b.x - a.x) * (b.x - a.x) +
                           (b.y - a.y) * (b.y - a.y));
    }
    if (drawn < 0) {
        drawn = 0;
    }

    var h = screenHeight();
    if (drawn < GATE * h || t - handedAt < QUIET_MS || trail.length < 8) {
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
