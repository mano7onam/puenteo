#!/usr/bin/env bash
# Copy the canonical skills (puenteo/data/skills) into the plugin and Gemini-extension trees,
# and stamp the package version into the manifests. Run after editing a SKILL.md or bumping the version.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/puenteo/data/skills"
for dest in "$ROOT/plugin/skills" "$ROOT/skills"; do
  rm -rf "$dest"
  cp -R "$SRC" "$dest"
done
VER=$(python3 -c "import re;print(re.search(r'__version__ = \"(.+?)\"', open('$ROOT/puenteo/version.py').read()).group(1))")
python3 - "$VER" "$ROOT" <<'PY'
import json, sys, pathlib
ver, root = sys.argv[1], pathlib.Path(sys.argv[2])
for rel in ("plugin/.claude-plugin/plugin.json", "gemini-extension.json", "server.json"):
    p = root / rel
    d = json.loads(p.read_text())
    d["version"] = ver
    for pkg in d.get("packages", []):
        pkg["version"] = ver
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
PY
# The Rust core is versioned on its own: bump it only when native/ changes (PyPI rejects re-uploads,
# and the publish step uses skip-existing). Show the pair so a stale bump is visible.
NATIVE=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/native/Cargo.toml" | head -1)
echo "skills synced, manifests at $VER (native core $NATIVE)"
