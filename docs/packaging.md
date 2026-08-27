# Packaging

rpm and deb packages install dictatr system-wide with the integration a
source checkout can't do without sudo:

- app tree under `/usr/lib/dictatr`, launchers symlinked into `/usr/bin`
  (the `dictate` shim detects the missing dev venv and runs
  `python3 -m dictatr` against the distro's `python3-websockets`)
- desktop entries for the hotkey launchers, tray autostart for every
  session (`/etc/xdg/autostart`)
- systemd user units: `dictatr-listen`, `dictatr-gc.timer`, and
  `dictatr-ydotoold` (rootless typing daemon)
- a udev rule (`70-dictatr-uinput.rules`) tagging `/dev/uinput` with
  `uaccess`, so the logged-in user can run `ydotoold` as a user service;
  no root daemon, no socket mismatch, no manual sudo step

After installing a package:

```bash
dictate-hotkeys                                   # bind KDE shortcuts (once)
systemctl --user enable --now dictatr-ydotoold    # type-at-cursor daemon
```

Lemonade and its models stay per-user and are never packaged (they are
gigabytes, shared by other apps, and released on their own cadence);
install it per the README.

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
