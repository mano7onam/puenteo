"""Run `puenteo mesh up` in the background at login: launchd (macOS), systemd --user (Linux),
or a Scheduled Task (Windows). `puenteo mesh service install|uninstall|status`."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

LABEL = "io.github.mano7onam.puenteo.mesh"
UNIT = "puenteo-mesh.service"


def _cmd() -> List[str]:
    exe = shutil.which("puenteo")
    return [exe, "mesh", "up"] if exe else [sys.executable, "-m", "puenteo", "mesh", "up"]


def _log_path() -> Path:
    from ..paths import state_dir

    return state_dir() / "mesh.log"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT


def _plist(args: List[str]) -> str:
    from xml.sax.saxutils import escape

    items = "\n".join(f"    <string>{escape(a)}</string>" for a in args)
    log = escape(str(_log_path()))
    env = "".join(
        f"    <key>{k}</key><string>{escape(v)}</string>\n"
        for k, v in (("PATH", os.environ.get("PATH", "/usr/bin:/bin")), ("PUENTEO_HOME", os.environ.get("PUENTEO_HOME", "")))
        if v
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{items}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{env}  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""


def _unit(args: List[str]) -> str:
    import shlex

    env = ""
    if os.environ.get("PUENTEO_HOME"):
        env = f"Environment=PUENTEO_HOME={os.environ['PUENTEO_HOME']}\n"
    return f"""[Unit]
Description=puenteo mesh bridge (agent sessions across machines)
After=network-online.target

[Service]
ExecStart={' '.join(shlex.quote(a) for a in args)}
{env}Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def _run(cmd: List[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def install(extra_args: List[str] = ()) -> str:
    args = _cmd() + list(extra_args)
    if sys.platform == "darwin":
        p = _plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)])
        p.write_text(_plist(args), encoding="utf-8")
        rc, out = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)])
        if rc != 0 and "already" not in out.lower():
            return f"wrote {p} but launchctl failed: {out}"
        return f"installed launchd agent {LABEL} (starts at login, restarts on crash); log: {_log_path()}"
    if sys.platform.startswith("linux"):
        p = _unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_unit(args), encoding="utf-8")
        if not shutil.which("systemctl"):
            return f"wrote {p}; no systemctl found, start `{' '.join(args)}` yourself"
        _run(["systemctl", "--user", "daemon-reload"])
        rc, out = _run(["systemctl", "--user", "enable", "--now", UNIT])
        if rc != 0:
            return f"wrote {p} but systemctl failed: {out}"
        return f"installed systemd user service {UNIT}; logs: journalctl --user -u {UNIT}"
    if sys.platform == "win32":
        tr = subprocess.list2cmdline(args)
        rc, out = _run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", "puenteo-mesh", "/TR", tr])
        if rc != 0:
            return f"schtasks failed: {out}"
        _run(["schtasks", "/Run", "/TN", "puenteo-mesh"])
        return "installed Scheduled Task puenteo-mesh (runs at logon)"
    return f"unsupported platform {sys.platform}; run `{' '.join(args)}` yourself"


def uninstall() -> str:
    if sys.platform == "darwin":
        p = _plist_path()
        if not p.exists():
            return "not installed"
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)])
        p.unlink()
        return f"removed {LABEL}"
    if sys.platform.startswith("linux"):
        p = _unit_path()
        if shutil.which("systemctl"):
            _run(["systemctl", "--user", "disable", "--now", UNIT])
        if p.exists():
            p.unlink()
            _run(["systemctl", "--user", "daemon-reload"])
            return f"removed {UNIT}"
        return "not installed"
    if sys.platform == "win32":
        rc, out = _run(["schtasks", "/Delete", "/F", "/TN", "puenteo-mesh"])
        return "removed Scheduled Task puenteo-mesh" if rc == 0 else f"not removed: {out}"
    return "unsupported platform"


def running() -> bool:
    """Is a `puenteo mesh up` bridge alive for this bus? (heartbeat written by the node)."""
    import time

    from ..bus import Bus
    from .node import config_get

    try:
        with Bus() as b:
            hb = float(config_get(b, "heartbeat", "0") or 0)
        return time.time() - hb < 90
    except Exception:
        return False


def status() -> str:
    installed = (sys.platform == "darwin" and _plist_path().exists()) or \
                (sys.platform.startswith("linux") and _unit_path().exists())
    if sys.platform == "win32":
        installed = _run(["schtasks", "/Query", "/TN", "puenteo-mesh"])[0] == 0
    return f"service: {'installed' if installed else 'not installed'} · bridge: {'running' if running() else 'not running'}"
