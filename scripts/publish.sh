#!/usr/bin/env bash
# Build + upload puenteo to PyPI.
# Requires: PYPI_TOKEN (API token from https://pypi.org/manage/account/token/)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TOKEN="${PYPI_TOKEN:-${TWINE_PASSWORD:-}}"
if [[ -z "$TOKEN" ]]; then
  echo "Set PYPI_TOKEN first:"
  echo "  export PYPI_TOKEN=pypi-…"
  echo "  # create at https://pypi.org/manage/account/token/"
  exit 1
fi

python3 -m pip install -U build twine
rm -rf dist build *.egg-info
python3 -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD="$TOKEN" python3 -m twine upload --skip-existing dist/*
echo "Published. Test: pip install -U puenteo && puenteo --version && asb --version"
