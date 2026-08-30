# Packaging

rpm and deb packages install dictatr system-wide with the integration a
source checkout can't do without sudo:

- app tree under `/usr/lib/dictatr`, launchers symlinked into `/usr/bin`
  (the `dictate` shim detects the missing dev venv and runs
  `python3 -m dictatr` against the distro's `python3-websockets`)
- desktop entries for the hotkey launchers plus a visible "Set up
  dictatr" entry, and tray autostart for every session
  (`/etc/xdg/autostart`)
- man pages for every launcher (one page, the rest `.so` stubs)
- systemd user units: `dictatr-listen` and `dictatr-gc.timer`

Nothing here needs device access or a privileged helper: typing goes
through the desktop portal, which asks the user once and remembers.

Installing runs no user-visible post-install step and prints no shell
instructions. Per-user work cannot happen in a package script anyway (it
has no session, no bus and no idea who will log in), so the tray offers
the setup wizard the first time it starts and it does that work in the
session where it applies. See the
[guide](guide.md#setup).

Models stay per-user and are never packaged: they are gigabytes and
shared with other local-AI apps through the HuggingFace cache. The
wizard pulls the dictation model on first run.

The inference engine is not packaged either, for the same reason plus a
harder one: it is a prebuilt x86-64 binary, and bundling one is grounds
for rejection from both Debian and Fedora. It also made an
`Architecture: all` / `BuildArch: noarch` package ship an ELF, which
lintian and rpmlint each flag as an error, and which left the package
installable on aarch64 where it could never run. dictatr downloads the
pinned release (checksummed, `PINNED_SHA256` in
`src/dictatr/backend/lemond.py`) to `~/.local/share/dictatr/lemond` on
first use of the managed backend — 13 MB, next to a model download of a
gigabyte, so vendoring bought nothing.

A distro that packages lemond itself can install it at
`/usr/lib/dictatr/lemond/lemond`, which dictatr prefers over its own
download.

## Checking the packages

One script, run the same way on a laptop and in CI:

```bash
packaging/check.sh debian:stable    # deb: build, lintian, install, smoke
packaging/check.sh ubuntu:24.04     # the current LTS
packaging/check.sh fedora:latest    # rpm: build, rpmlint, install, smoke
```

It picks the format from the image (apt -> deb, dnf -> rpm), builds,
runs that distro's official linter as a gate (`lintian --fail-on error`;
rpmlint exits non-zero on its own when a package scores), installs the
result, and then checks two things that a build alone does not:

- `dictate backend status` runs, so a package missing part of its own
  source fails the build. 0.3.0 shipped without `src/dictatr/backend/`
  and nobody noticed, because CI only listed the files.
- the realtime `connect()` call is well formed against the websockets
  that distro ships. The argument was renamed in 14.0 and Ubuntu 24.04
  LTS still packages 10.4; passing the wrong name is a TypeError at
  connect time, which importing the module does not reach.

`.github/workflows/packages.yml` runs exactly this script across
`debian:stable`, `ubuntu:24.04`, `ubuntu:25.04` and `fedora:latest`.
Adding a distro is one line in the matrix. `CONTAINER=docker` selects
the engine; podman is the default.

`packaging/dictatr.rpmlintrc` turns off only the description spell
check: rpmlint counts an unknown word as an error, and ours are names.

## Building

Everything a package installs is staged by `packaging/stage.sh DESTDIR`,
the single source of truth used by both builders.

```bash
packaging/build-deb.sh              # -> dist/dictatr_<version>_all.deb
packaging/build-rpm.sh              # -> dist/noarch/dictatr-<version>-1.*.rpm
VERSION=1.2.3 packaging/build-deb.sh   # override the pyproject version
```

The deb is a plain `dpkg-deb` binary package (no debhelper source
ceremony); the rpm builds from a `git archive` of HEAD, so commit before
building.

## CI and releases

`.github/workflows/packages.yml` runs on pushes and PRs: pytest, then
both package builds as artifacts. Pushing a tag `vX.Y.Z` additionally
creates a GitHub Release with the rpm and deb attached, versioned from
the tag:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Distro repos (COPR / PPA) can layer on later; the spec and control file
are already in the shapes those services consume.
