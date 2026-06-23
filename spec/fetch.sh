#!/usr/bin/env bash
# Fetch the WebAssembly core conformance testsuite as a .wast corpus.
# Downloads the WebAssembly/spec repo as a single archive from a direct link and
# copies test/core/*.wast plus the gc/ subdir into spec/wast/ (gitignored — we
# don't vendor the upstream testsuite). Then:
#   python3 -m miscast --mode replay --seeds spec/wast --sut <engine>
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/wast"; CACHE="$HERE/.cache"
mkdir -p "$DIR" "$CACHE"; rm -f "$DIR"/*.wast
TGZ="$CACHE/spec.tar.gz"
URL="https://github.com/WebAssembly/spec/archive/refs/heads/main.tar.gz"

echo "downloading $URL ..."
if   command -v curl >/dev/null && curl -fsSL "$URL" -o "$TGZ"; then :
elif command -v wget >/dev/null && wget -qO "$TGZ" "$URL"; then :
elif command -v gh   >/dev/null && gh api repos/WebAssembly/spec/tarball/main > "$TGZ"; then :
else echo "need curl, wget, or gh"; exit 1; fi

tar xzf "$TGZ" -C "$CACHE"
SRC="$(echo "$CACHE"/*/test/core)"          # archive extracts to a single top dir
cp "$SRC"/*.wast "$DIR/" 2>/dev/null
for f in "$SRC"/gc/*.wast; do [ -e "$f" ] && cp "$f" "$DIR/gc-$(basename "$f")"; done

rm -rf "$CACHE"
echo "done: $(ls "$DIR"/*.wast 2>/dev/null | wc -l | tr -d ' ') files in spec/wast/"
