# Shared by the launchers. Source, do not run.
#
# PyGObject lives in the system python, and a dev checkout's venv comes
# first on PATH without it, so "python3" is the wrong interpreter
# exactly when someone is working on dictatr. gtk4-layer-shell has to be
# preloaded before GTK opens its wayland connection, which a program
# cannot do for itself.

repo=$(dirname "$(dirname "$(readlink -f "$0")")")

# The interpreter that can import gi, named outright rather than trusted
# from PATH.
py=python3
"$py" -c 'import gi' 2>/dev/null || py=/usr/bin/python3

# Call before exec'ing a surface that floats above other windows.
preload_layer_shell() {
    for lib in /usr/lib64/libgtk4-layer-shell.so.0 \
               /usr/lib/libgtk4-layer-shell.so.0; do
        [ -e "$lib" ] && export LD_PRELOAD="$lib${LD_PRELOAD:+:$LD_PRELOAD}"
    done
}
