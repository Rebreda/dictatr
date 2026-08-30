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
// Shove the pointer up and down a few times and the voice chat opens.
// It has to be a gesture nobody performs by accident, so a swing only
// counts when it is fast and long: SWING is the distance a single
// up-or-down sweep must cover, and the whole sequence must finish
// inside WINDOW milliseconds. Ordinary pointing reverses direction
// constantly but in small steps, and dragging a scrollbar is long but
// slow; requiring both filters each of them out.

var SWING = 90;        // px a sweep must cover to count as one
var NEEDED = 4;        // sweeps (up, down, up, down) to fire
var WINDOW = 1100;     // ms the whole sequence must fit inside
var COOLDOWN = 4000;   // ms of quiet after firing

var dir = 0;           // +1 down, -1 up, 0 unknown
var pivot = -1;        // y where the current sweep began
var sweeps = [];       // timestamps of completed sweeps
var lastFired = 0;

function now() {
    return new Date().getTime();
}

function shake() {
    var y = workspace.cursorPos.y;
    if (pivot < 0) {
        pivot = y;
        return;
    }
    var travel = y - pivot;
    var heading = travel > 0 ? 1 : -1;
    if (heading !== dir) {          // direction changed: a sweep ended
        if (Math.abs(travel) >= SWING) {
            var t = now();
            sweeps.push(t);
            while (sweeps.length && t - sweeps[0] > WINDOW) {
                sweeps.shift();
            }
            // Every sweep is reported, not just the fourth: tuning
            // this by feel needs to show near misses, and the tray
            // stays quiet about them unless asked (gesture_debug).
            callDBus(TRAY, OBJ, IFACE, "Gesture",
                     "sweep " + sweeps.length + "/" + NEEDED +
                     " travel=" + Math.round(Math.abs(travel)));
            if (sweeps.length >= NEEDED && t - lastFired > COOLDOWN) {
                lastFired = t;
                sweeps = [];
                callDBus(TRAY, OBJ, IFACE, "Gesture", "shake");
            }
        }
        dir = heading;
        pivot = y;
    } else if (Math.abs(travel) > SWING * 2) {
        pivot = y - heading * SWING;   // a long glide is not a sweep
    }
}

workspace.cursorPosChanged.connect(shake);
