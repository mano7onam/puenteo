"""Optional Rust accelerator (``pip install puenteo[fast]`` → puenteo-core).

Everything works without it; set ``PUENTEO_NATIVE=0`` to force pure Python.
"""

from __future__ import annotations

import os

core = None
if os.environ.get("PUENTEO_NATIVE", "1").strip() not in ("0", "false", "no"):
    try:
        import puenteo_core as core  # type: ignore[no-redef]
    except Exception:  # not installed / wrong platform
        core = None


def available() -> bool:
    return core is not None


def version() -> str:
    return getattr(core, "__version__", "") if core else ""
