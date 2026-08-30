#!/usr/bin/env python3
"""Propose the spec's next %changelog entry from the commits since the
last tag.

The spec stays the source of truth -- it is what the rpm carries, what
the Debian changelog is generated from, and what the release page
shows. What changes is how its entries get written: a Conventional
Commit subject is already a one-line summary of one change, so the
entry assembles itself instead of being recalled and re-prosed days
later, which is how a changelog turns into paragraphs nobody reads.

    packaging/release-entry.py 0.5.0        # since the latest tag
    packaging/release-entry.py 0.5.0 v0.3.0 # since a given one

Paste the block above the previous entry in packaging/dictatr.spec.
Only user-visible types are listed: feat, fix, perf and anything marked
breaking. Refactors, tests and CI work are real work and belong in the
history, not in a changelog a user reads.
"""

import re
import subprocess
import sys
import textwrap
from datetime import date

SHOWN = ("feat", "fix", "perf")
HEADING = {"feat": "", "fix": "", "perf": ""}
SUBJECT = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?: (?P<text>.+)$")


def git(*args) -> str:
    return subprocess.run(("git", *args), capture_output=True, text=True,
                          check=True).stdout.strip()


def main(version: str, since: str | None) -> int:
    if since is None:
        try:
            since = git("describe", "--tags", "--abbrev=0")
        except subprocess.CalledProcessError:
            since = ""
    span = f"{since}..HEAD" if since else "HEAD"
    subjects = git("log", "--format=%s", "--no-merges", span).splitlines()

    breaking, shown, skipped = [], [], 0
    for s in subjects:
        m = SUBJECT.match(s)
        if not m:
            skipped += 1
            continue
        text, scope = m["text"], m["scope"]
        line = f"{scope}: {text}" if scope else text
        if m["bang"]:
            breaking.append(line)
        elif m["type"] in SHOWN:
            shown.append(line)

    if not (breaking or shown):
        why = (f"; {skipped} had no Conventional Commit subject"
               if skipped else "")
        print(f"no user-visible commits in {span}{why}", file=sys.stderr)
        return 1

    who = git("config", "user.name") or "Rebreda"
    stamp = date.today().strftime("%a %b %d %Y")
    out = [f"* {stamp} {who} - {version}-1"]
    for line in breaking:
        out.append(_wrap(f"BREAKING: {line}"))
    for line in shown:
        out.append(_wrap(line))
    print("\n".join(out))

    if skipped:
        print(f"\n({skipped} commit(s) in {span} had no Conventional Commit "
              f"subject and were not considered)", file=sys.stderr)
    return 0


def _wrap(text: str) -> str:
    return textwrap.fill(text, width=70, initial_indent="- ",
                         subsequent_indent="  ")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
