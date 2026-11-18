#!/usr/bin/env bash
# Build a static fledge-lite site into ./dist
#
#   ./build.sh                 clone/fetch everything into ./.cache and build
#   FLEDGE_SRC=... GUI_DIST=... ./build.sh   reuse an existing Fledge checkout / GUI build
#
# Needs: git, curl, node + yarn (only if GUI_DIST is not given), python3, tar with bzip2.
set -euo pipefail
cd "$(dirname "$0")"
HERE=$PWD
CACHE=${CACHE:-$HERE/.cache}
PYODIDE_VERSION=${PYODIDE_VERSION:-314.0.8}
# Fledge commits this was tested against (develop branch, 2026-11-18). The storage
# emulator and patches depend on Fledge internals, so bump these deliberately.
FLEDGE_REF=${FLEDGE_REF:-cf091f09fd456a767c155fc7474944dd9057c4c1}
FLEDGE_GUI_REF=${FLEDGE_GUI_REF:-ba3efe4a011ee32b24ba4f4b5785603dca4d03a1}
mkdir -p "$CACHE"

# ---------------------------------------------------------------- sources
fetch_repo() {  # fetch_repo <url> <ref (branch, tag or commit)> <dir>
  git init -q "$3"
  git -C "$3" fetch -q --depth 1 "$1" "$2"
  git -C "$3" checkout -q FETCH_HEAD
}
if [ -z "${FLEDGE_SRC:-}" ]; then
  FLEDGE_SRC=$CACHE/fledge
  [ -d "$FLEDGE_SRC" ] || fetch_repo https://github.com/fledge-iot/fledge.git "$FLEDGE_REF" "$FLEDGE_SRC"
fi
if [ -z "${GUI_DIST:-}" ]; then
  GUI_SRC=$CACHE/fledge-gui
  [ -d "$GUI_SRC" ] || fetch_repo https://github.com/fledge-iot/fledge-gui.git "$FLEDGE_GUI_REF" "$GUI_SRC"
  if [ ! -f "$GUI_SRC/dist/fledge.html" ]; then
    (cd "$GUI_SRC" && yarn install --frozen-lockfile --ignore-scripts && yarn build)
  fi
  GUI_DIST=$GUI_SRC/dist
fi
PYO=$CACHE/pyodide-$PYODIDE_VERSION
if [ ! -f "$PYO/full.tar.bz2" ]; then
  mkdir -p "$PYO"
  base=https://github.com/pyodide/pyodide/releases/download/$PYODIDE_VERSION
  curl -fsSL -o "$PYO/core.tar.bz2" "$base/pyodide-core-$PYODIDE_VERSION.tar.bz2"
  curl -fsSL -o "$PYO/full.tar.bz2" "$base/pyodide-$PYODIDE_VERSION.tar.bz2"
fi
# Pure-Python dependencies of the Fledge core that Pyodide does not ship
PURE_WHEELS="PyJWT aiohttp-cors"
for p in $PURE_WHEELS; do
  [ -f "$CACHE/$p.whl" ] || curl -fsSL -o "$CACHE/$p.whl" "$(curl -fsSL https://pypi.org/pypi/$p/json | python3 -c 'import sys,json; print([u["url"] for u in json.load(sys.stdin)["urls"] if u["filename"].endswith("none-any.whl")][0])')"
done

# ------------------------------------------------------------------ build
rm -rf dist && mkdir -p dist
cp web/* dist/
cp -r samples dist/samples

echo "GUI"
cp -r "$GUI_DIST" dist/gui
mv dist/gui/fledge.html dist/gui/index.html
sed -i 's#<base href="/">#<base href="./">#' dist/gui/index.html

echo "Pyodide $PYODIDE_VERSION (core + aiohttp and its dependencies)"
tar -xjf "$PYO/core.tar.bz2" -C dist
rm -f dist/pyodide/python dist/pyodide/python.bat dist/pyodide/python.exe dist/pyodide/python_cli_entry.mjs
WHEELS=$(python3 - "$HERE/dist/pyodide/pyodide-lock.json" <<'EOF'
import json, sys
lock = json.load(open(sys.argv[1]))["packages"]
todo, seen = ["aiohttp"], set()
while todo:
    n = todo.pop()
    if n in seen: continue
    seen.add(n); todo += lock[n]["depends"]
print(" ".join("pyodide/" + lock[n]["file_name"] for n in sorted(seen)))
EOF
)
tar -xjf "$PYO/full.tar.bz2" -C dist $WHEELS

echo "Fledge Python bundle"
STAGE=$CACHE/stage && rm -rf "$STAGE" && mkdir -p "$STAGE/python" "$STAGE/lite" "$STAGE/scripts/plugins/storage/sqlite"
cp -r "$FLEDGE_SRC/python/fledge" "$STAGE/python/"
cp "$FLEDGE_SRC/VERSION" "$STAGE/"
cp "$FLEDGE_SRC/scripts/plugins/storage/sqlite/init.sql" "$STAGE/scripts/plugins/storage/sqlite/"
cp -r py/fledge_lite "$STAGE/lite/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
for p in $PURE_WHEELS; do (cd "$STAGE/lite" && python3 -m zipfile -e "$CACHE/$p.whl" .); done
rm -rf "$STAGE"/lite/*.dist-info
(cd "$STAGE" && rm -f "$HERE/dist/fledge-bundle.zip" && python3 -m zipfile -c "$HERE/dist/fledge-bundle.zip" python lite scripts VERSION)

du -sh dist dist/pyodide dist/gui dist/fledge-bundle.zip
echo "Serve it with:  python3 -m http.server -d dist 8000   then open http://localhost:8000/"
