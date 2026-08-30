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
#
# The `if` is load-bearing. Written as `[ -e "$lib" ] && export ...` the
# last iteration decides the function's status, and only one of these
# paths exists on any given machine -- so under `set -e` the launcher
# exited, silently and before doing anything, on every 64-bit distro
# that keeps the library in /usr/lib64. That is what "the hotkey does
# nothing" looked like from the outside.
preload_layer_shell() {
    for lib in /usr/lib64/libgtk4-layer-shell.so.0 \
               /usr/lib/libgtk4-layer-shell.so.0; do
        if [ -e "$lib" ]; then
            export LD_PRELOAD="$lib${LD_PRELOAD:+:$LD_PRELOAD}"
        fi
    done
    return 0
}

# Run a module from the dictatr package. The package is stdlib-only, so
# unlike the surfaces it does not need the gi interpreter -- it needs
# whichever python can import it: the checkout's venv, or the system one
# with src/ on the path.
dictatr_py() {
    if [ -x "$repo/.venv/bin/python" ]; then
        "$repo/.venv/bin/python" "$@"
    else
        PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}" python3 "$@"
    fi
}
