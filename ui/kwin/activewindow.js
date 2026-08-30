// Report the focused window's application to the dictatr tray.
//
// Wayland gives an app no way to ask what else is focused, and KWin's
// only DBus answer (queryWindowInfo) makes the user click a window. A
// compositor script is the way in: KWin already knows, and it can call
// out over DBus. The tray loads this at startup and unloads it on exit.
//
// The class (org.mozilla.firefox, code, konsole) is what gets sent. The
// caption is deliberately left behind: window titles carry document
// names and URLs, and knowing which app you are in is enough to steer
// recall.

// Transient system surfaces (portal permission dialogs, the shortcut
// prompt) steal focus for a moment and are not where the user is.
var IGNORE = /^org\.freedesktop\.impl\.portal|^xdg-desktop-portal/;

function report(window) {
    if (!window || window.specialWindow) {
        return;
    }
    if (IGNORE.test(String(window.resourceClass || ""))) {
        return;
    }
    callDBus("io.github.rebreda.dictatr.tray", "/Shortcuts",
             "io.github.rebreda.dictatr.Shortcuts", "ActiveApp",
             String(window.resourceClass || ""));
}

workspace.windowActivated.connect(report);
report(workspace.activeWindow);
