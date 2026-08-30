#!/bin/bash
# Build, lint, install and smoke-test a package in a throwaway container.
#
#   packaging/check.sh debian:stable      # deb: build, lintian, install, run
#   packaging/check.sh ubuntu:24.04       # the current LTS
#   packaging/check.sh fedora:latest      # rpm: build, rpmlint, install, run
#
# One script for CI and for a laptop, so "it passed for me" and "it
# passed in CI" mean the same thing. CONTAINER=docker also works.
#
# Format is decided by what the image has: apt -> deb, dnf -> rpm.
set -eu
image=${1:?usage: packaging/check.sh IMAGE}
repo=$(dirname "$(dirname "$(readlink -f "$0")")")
engine=${CONTAINER:-podman}

# A tag build sets VERSION; pass it through rather than letting the
# container fall back to the version in the tree.
exec "$engine" run --rm -i -e "VERSION=${VERSION:-}" \
    -v "$repo:/repo:z" "$image" bash -s <<'INNER'
set -eu
cd /repo
[ -n "${VERSION:-}" ] || unset VERSION

if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq lintian python3 xz-utils >/dev/null
    rm -f dist/*.deb
    bash packaging/build-deb.sh | tail -1
    echo "== lintian =="
    lintian --fail-on error --tag-display-limit 0 dist/*.deb && echo "  clean"
    apt-get install -y -qq ./dist/*.deb >/dev/null
else
    dnf install -y -q rpm-build rpmlint git-core python3 awk >/dev/null
    git config --global --add safe.directory /repo
    rm -rf dist/noarch
    bash packaging/build-rpm.sh >/dev/null
    echo "== rpmlint =="
    rpmlint -r packaging/dictatr.rpmlintrc dist/noarch/*.rpm
    dnf install -y -q dist/noarch/*.rpm >/dev/null
fi

echo "== installed and runnable =="
dictate backend status

# The check that matters: distros ship very different websockets, and
# the argument connect() takes was renamed in 14.0. Opening a socket to
# a dead port proves the call is well formed -- a TypeError here is the
# bug that made every dictation fail on Ubuntu 24.04, and it does not
# show up in `backend status`, which never opens one.
echo "== realtime connect() is well formed =="
PYTHONPATH=/usr/lib/dictatr/src python3 -c '
import asyncio, sys, websockets, dictatr.engine as e
print("  websockets", websockets.__version__, "->", e._HEADERS_KW)
try:
    asyncio.run(e._connect("ws://127.0.0.1:1/realtime"))
except TypeError as exc:
    sys.exit(f"  FAIL: connect() rejected our arguments: {exc}")
except Exception:
    pass  # refused is the expected end; the call itself was accepted
print("  ok")
'
INNER
