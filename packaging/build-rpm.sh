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
git -C "$repo" archive --prefix="dictatr-$version/" -o \
    "$top/SOURCES/dictatr-$version.tar.gz" HEAD

rpmbuild -bb "$spec" \
    --define "_topdir $top" \
    --define "_rpmdir $repo/dist"
rm -rf "$top" "$spec"
ls "$repo"/dist/noarch/*.rpm
