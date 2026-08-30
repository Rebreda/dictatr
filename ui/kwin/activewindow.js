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
// The first rule counted discrete sweeps, each of which had to cover a
// fixed distance on its own, and in practice the count kept resetting:
// a real shake is not four tidy equal strokes, and cursorPosChanged
// samples a fast movement coarsely enough to swallow a reversal whole.
// This measures the two things a shake actually is, over a window: a
// lot of vertical travel, and several changes of direction, ending up
// roughly where it started. A drag has the travel but no reversals and
// a large net displacement; ordinary pointing has reversals but little
// travel.

var TRAVEL = 380;      // px of vertical movement inside the window
var LEGS = 3;          // up/down strokes (so at least two reversals)
var DEADZONE = 25;     // px: shorter strokes are jitter, not direction
var WINDOW = 1500;     // ms the whole thing must fit inside
var NET = 300;         // px: end near where you began, or it was a move
var COOLDOWN = 4000;   // ms of quiet after firing

var legs = [];         // {t, dist, dir} for recent strokes
var cur = null;        // the stroke in progress
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
            cur = {t: t, dist: 0, dir: dir};
        }
        cur.dist += Math.abs(dy);
        cur.t = t;
    } else if (cur.dist >= DEADZONE) {
        legs.push(cur);                    // a real change of direction
        cur = {t: t, dist: Math.abs(dy), dir: dir};
    } else {
        cur = {t: t, dist: Math.abs(dy), dir: dir};   // jitter, restart
    }

    while (legs.length && t - legs[0].t > WINDOW) {
        legs.shift();
    }

    var travel = cur.dist;
    var span = 0;
    for (var i = 0; i < legs.length; i++) {
        travel += legs[i].dist;
        span += legs[i].dist * legs[i].dir;
    }
    span += cur.dist * cur.dir;

    if (legs.length + 1 >= LEGS) {
        callDBus(TRAY, OBJ, IFACE, "Gesture",
                 "shaking legs=" + (legs.length + 1) + "/" + LEGS +
                 " travel=" + Math.round(travel) + "/" + TRAVEL +
                 " net=" + Math.round(Math.abs(span)));
    }
    if (legs.length + 1 >= LEGS && travel >= TRAVEL &&
            Math.abs(span) <= NET && t - lastFired > COOLDOWN) {
        lastFired = t;
        legs = [];
        cur = null;
        callDBus(TRAY, OBJ, IFACE, "Gesture", "shake");
    }
}

workspace.cursorPosChanged.connect(shake);
