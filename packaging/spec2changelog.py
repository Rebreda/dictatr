#!/usr/bin/env python3
"""Render the rpm spec's %changelog into the other formats that need it.

Three places want the same history and none of them share a format: the
spec itself, /usr/share/doc/<pkg>/changelog.gz that Debian requires, and
the GitHub release page. Writing a release note three times means it
gets written well once and badly twice, so the spec is the source and
these are generated from it.

    spec2changelog.py SPEC "Name <mail>"          # Debian changelog
    spec2changelog.py SPEC --markdown             # release notes, latest
    spec2changelog.py SPEC --markdown --version 0.4.0
"""

import re
import sys
import textwrap
from pathlib import Path

# * Sat Aug 29 2026 Rebreda - 0.3.0-1
HEADER = re.compile(r"^\*\s+(\w{3})\s+(\w{3})\s+(\d{1,2})\s+(\d{4})\s+"
                    r"(.+?)\s+-\s+(\S+)\s*$")


def parse(spec: Path):
    """[{version, who, day, notes}], newest first."""
    text = spec.read_text()
    body = text.split("%changelog", 1)[1] if "%changelog" in text else ""

    entries, current = [], None
    for line in body.splitlines():
        m = HEADER.match(line.strip())
        if m:
            day, mon, dom, year, who, version = m.groups()
            current = {
                "version": version.split("-")[0],
                "who": who.strip(),
                "day": f"{day}, {int(dom):02d} {mon} {year}",
                "notes": [],
            }
            entries.append(current)
        elif current is not None and line.strip().startswith("-"):
            current["notes"].append(line.strip()[1:].strip())
        elif current is not None and line.strip() and current["notes"]:
            current["notes"][-1] += " " + line.strip()
    return entries


def markdown(spec: Path, version: str | None) -> int:
    """Release notes for one version: what changed, then how to install
    it. Someone landing on the release page wants both."""
    entries = parse(spec)
    if version:
        entries = [e for e in entries if e["version"] == version]
    if not entries:
        print(f"{spec}: no changelog entry for {version}", file=sys.stderr)
        return 1
    e = entries[0]
    v = e["version"]

    out = ["## What changed\n"]
    out += [f"- {n}" for n in e["notes"]]
    out.append(f"""
## Install

```bash
sudo dnf install ./dictatr-{v}-1.*.noarch.rpm   # Fedora
sudo apt install ./dictatr_{v}_all.deb          # Debian, Ubuntu
```

That is the whole install: the tray autostarts and offers a setup wizard
that picks an inference engine, asks the desktop for typing permission
and hotkeys, and ends with a test dictation.

Needs a Wayland session. Full on KDE Plasma and wlroots compositors; on
GNOME the floating surfaces fall back to ordinary windows and the tray
needs the AppIndicator extension. See the README for the support table.

Both packages are built, linted (`lintian`, `rpmlint`), installed and
smoke-tested on Debian stable, Ubuntu 24.04, Ubuntu 25.04 and Fedora
before release.""")
    print("\n".join(out))
    return 0


def main(spec: Path, maintainer: str) -> int:
    text = spec.read_text()
    name = re.search(r"^Name:\s*(\S+)", text, re.M).group(1)
    body = text.split("%changelog", 1)[1] if "%changelog" in text else ""

    entries, current = [], None
    for line in body.splitlines():
        m = HEADER.match(line.strip())
        if m:
            day, mon, dom, year, who, version = m.groups()
            current = {
                "version": version.split("-")[0],
                "who": who.strip(),
                "day": f"{day}, {int(dom):02d} {mon} {year}",
                "notes": [],
            }
            entries.append(current)
        elif current is not None and line.strip().startswith("-"):
            current["notes"].append(line.strip()[1:].strip())
        elif current is not None and line.strip() and current["notes"]:
            current["notes"][-1] += " " + line.strip()

    if not entries:
        print(f"{spec}: no %changelog entries", file=sys.stderr)
        return 1

    # An rpm changelog carries a date; a Debian one carries a timestamp
    # and insists each entry be newer than the last (lintian:
    # latest-changelog-entry-without-new-date). Two releases on one day
    # is normal, so order them within it: oldest at 00:00, newest last.
    for i, e in enumerate(reversed(entries)):
        e["date"] = f"{e['day']} {i // 60:02d}:{i % 60:02d}:00 +0000"

    out = []
    for e in entries:
        out.append(f"{name} ({e['version']}) unstable; urgency=medium\n\n")
        for note in e["notes"]:
            # Debian wants readable width; the spec's notes are already
            # wrapped, but joining them made single long lines
            # (lintian: debian-changelog-line-too-long).
            wrapped = textwrap.fill(note, width=76,
                                    initial_indent="  * ",
                                    subsequent_indent="    ")
            out.append(wrapped + "\n")
        # The trailer is parsed, not just printed: it needs a real
        # RFC-822 name-and-address or dpkg calls it malformed.
        out.append(f"\n -- {maintainer}  {e['date']}\n\n")
    sys.stdout.write("".join(out).rstrip("\n") + "\n")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    spec_path = Path(args[0])
    if "--markdown" in args:
        wanted = None
        if "--version" in args:
            wanted = args[args.index("--version") + 1]
        sys.exit(markdown(spec_path, wanted))
    who = next((a for a in args[1:] if not a.startswith("-")),
               "Rebreda <Rebreda@users.noreply.github.com>")
    sys.exit(main(spec_path, who))
