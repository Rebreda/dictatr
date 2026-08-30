#!/usr/bin/env bash
# Build the rpm into dist/ from the current checkout.
#   VERSION=1.2.3 packaging/build-rpm.sh   (defaults to the spec version)
set -eu
here=$(dirname "$(readlink -f "$0")")
repo=$(dirname "$here")

spec=$repo/dist/dictatr.spec
mkdir -p "$repo/dist"
cp "$here/dictatr.spec" "$spec"
if [[ -n ${VERSION:-} ]]; then
    sed -i "s/^Version:.*/Version:        $VERSION/" "$spec"
fi
version=$(awk '/^Version:/{print $2}' "$spec")

top=$repo/dist/rpmbuild
rm -rf "$top"
mkdir -p "$top"/{SOURCES,BUILD,RPMS,SRPMS,SPECS}
# Package the working tree, not HEAD. Archiving HEAD silently drops
# local edits and every new file that is not committed yet, so a package
# built to check a change did not contain the change -- which is exactly
# how a broken 0.3.0 got tagged. Writing a throwaway index gives us a
# tree that includes untracked files and still honours .gitignore,
# without touching the real index. On a clean tree this is identical to
# archiving HEAD, so releases are unaffected.
tmpindex=$(mktemp)
trap 'rm -f "$tmpindex"' EXIT
GIT_INDEX_FILE=$tmpindex git -C "$repo" read-tree HEAD
GIT_INDEX_FILE=$tmpindex git -C "$repo" add -A
tree=$(GIT_INDEX_FILE=$tmpindex git -C "$repo" write-tree)
git -C "$repo" archive --prefix="dictatr-$version/" -o \
    "$top/SOURCES/dictatr-$version.tar.gz" "$tree"

rpmbuild -bb "$spec" \
    --define "_topdir $top" \
    --define "_rpmdir $repo/dist"
rm -rf "$top" "$spec"
ls "$repo"/dist/noarch/*.rpm
