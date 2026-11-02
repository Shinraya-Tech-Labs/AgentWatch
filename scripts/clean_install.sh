#!/usr/bin/env sh
# Build the wheel and sdist, install each in a fresh virtualenv, smoke-test the installed
# package (from outside the repository) and audit its dependencies.
#   scripts/clean_install.sh [python3.12]
set -eu
PY="${1:-python3.12}"
REPO="$(pwd)"
OUT="$(mktemp -d)"
"$PY" -m venv "$OUT/build-venv"
BPY="$OUT/build-venv/bin/python"
[ -x "$BPY" ] || BPY="$OUT/build-venv/Scripts/python"
"$BPY" -m pip install -q build
"$BPY" -m build --outdir "$OUT/dist" "$REPO"
for kind in whl tar.gz; do
  "$PY" -m venv "$OUT/venv-$kind"
  VPY="$OUT/venv-$kind/bin/python"
  [ -x "$VPY" ] || VPY="$OUT/venv-$kind/Scripts/python"
  "$VPY" -m pip install -q --upgrade pip
  "$VPY" -m pip install -q "$OUT"/dist/*."$kind"
  (cd "$OUT" && "$VPY" -I "$REPO/scripts/smoke_installed.py")
done
"$VPY" -m pip install -q pip-audit
# fails on any known vulnerability; panoptis itself is reported as "not on PyPI" and skipped
(cd "$OUT" && "$VPY" -m pip_audit --progress-spinner off)
