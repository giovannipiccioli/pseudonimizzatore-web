#!/bin/sh
# Rebuild vendor/pseudonimizzatore_legale-*.whl from the engine repo next to this one.
#
#   ./build_engine_wheel.sh          # from ../anonymizer's working tree (development)
#   ./build_engine_wheel.sh vX.Y.Z   # from a tag or commit of ../anonymizer (release)
#
# The engine is built from a copy, so nothing is written into ../anonymizer. Needs a
# Python with pip and setuptools>=68 (PYTHON, default python3).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
engine="$here/../anonymizer"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [ $# -gt 0 ]; then
    git -C "$engine" archive "$1" pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md \
        pseudonimizzatore_legale | tar -x -C "$tmp"
else
    cp "$engine"/pyproject.toml "$engine"/README.md "$engine"/LICENSE \
        "$engine"/THIRD_PARTY_NOTICES.md "$tmp"/
    cp -R "$engine"/pseudonimizzatore_legale "$tmp"/
    find "$tmp" -name __pycache__ -prune -exec rm -rf {} +
fi

rm -f "$here"/vendor/pseudonimizzatore_legale-*.whl
"${PYTHON:-python3}" -m pip wheel --quiet --no-deps --no-build-isolation -w "$here/vendor" "$tmp"
ls "$here"/vendor/pseudonimizzatore_legale-*.whl
