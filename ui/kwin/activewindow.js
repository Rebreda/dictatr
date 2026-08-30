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

// --- the panic shake --------------------------------------------------
//
// Shove the pointer up and down and the voice chat opens.
//
// Two rules were wrong before this one, and the debug log said why.
// Counting fixed-length sweeps never got past one, because a shake is
// not tidy equal strokes. Measuring total travel fired constantly:
// ordinary work racks up 1500-3000px of vertical movement and fifteen
// changes of direction inside a second and a half, and usually ends up
// near where it started, which is every condition that rule had.
//
// What separates them is not how much movement, but its shape: a shake
// is a few LONG strokes in quick alternation. Ordinary pointing makes
// many short ones. So only strokes past STROKE count at all, and BIG of
// them must land inside WINDOW.

var STROKE = 220;      // px: shorter strokes are movement, not a shake
var BIG = 4;           // long strokes needed, alternating by nature
var WINDOW = 800;      // ms they must all fall inside
var DEADZONE = 60;     // px before a direction change ends a stroke
var NET = 200;         // px: end near where you began
var COOLDOWN = 4000;   // ms of quiet after firing

var legs = [];         // completed strokes: {t, dist, dir}
var cur = null;
var lastY = -1;
var lastFired = 0;

function now() {
    return new Date().getTime();
}

function shake() {
    var y = workspace.cursorPos.y;
    if (lastY < 0) {
        lastY = y;
        return;
    }
    var dy = y - lastY;
    lastY = y;
    if (dy === 0) {
        return;
    }
    var dir = dy > 0 ? 1 : -1;
    var t = now();

    if (cur === null || dir === cur.dir) {
        if (cur === null) {
            cur = {t: t, t0: t, dist: 0, dir: dir};
        }
        cur.dist += Math.abs(dy);
        cur.t = t;
    } else if (cur.dist >= DEADZONE) {
        legs.push(cur);
        // Every completed stroke, so the thresholds can be chosen from
        // how a person actually moves rather than from guesswork.
        callDBus(TRAY, OBJ, IFACE, "Gesture",
                 "stroke dist=" + Math.round(cur.dist) +
                 " ms=" + (t - cur.t0));
        cur = {t: t, t0: t, dist: Math.abs(dy), dir: dir};
    } else {
        cur = {t: t, t0: t, dist: Math.abs(dy), dir: dir};   // jitter, restart
    }

    while (legs.length && t - legs[0].t > WINDOW) {
        legs.shift();
    }

    var big = 0;
    var travel = 0;
    var span = 0;
    for (var i = 0; i < legs.length; i++) {
        if (legs[i].dist >= STROKE) {
            big += 1;
        }
        travel += legs[i].dist;
        span += legs[i].dist * legs[i].dir;
    }
    if (cur.dist >= STROKE) {          // the stroke in progress counts too
        big += 1;
    }
    travel += cur.dist;
    span += cur.dist * cur.dir;

    if (big >= BIG - 1 && big > 1) {              // quiet until it is nearly there
        callDBus(TRAY, OBJ, IFACE, "Gesture",
                 "shaking long=" + big + "/" + BIG +
                 " travel=" + Math.round(travel) +
                 " net=" + Math.round(Math.abs(span)));
    }
    if (big >= BIG && Math.abs(span) <= NET && t - lastFired > COOLDOWN) {
        lastFired = t;
        legs = [];
        cur = null;
        callDBus(TRAY, OBJ, IFACE, "Gesture", "shake");
    }
}

workspace.cursorPosChanged.connect(shake);
