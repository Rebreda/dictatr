# Packaging

rpm and deb packages install dictatr system-wide with the integration a
source checkout can't do without sudo:

- app tree under `/usr/lib/dictatr`, launchers symlinked into `/usr/bin`
  (the `dictate` shim detects the missing dev venv and runs
  `python3 -m dictatr` against the distro's `python3-websockets`)
- desktop entries for the hotkey launchers plus a visible "Set up
  dictatr" entry, and tray autostart for every session
  (`/etc/xdg/autostart`)
- optionally the pinned embeddable `lemond` under `/usr/lib/dictatr/lemond`
  (about 7 MB; CI fetches and checksums it per
  `packaging/lemond-version.env`), so the managed backend needs no
  download at all
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
