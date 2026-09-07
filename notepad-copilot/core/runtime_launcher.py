"""Resolve the installed runtime without triggering an SDK runtime download."""
from __future__ import annotations

import os
import platform
import re
import shutil
from pathlib import Path

from core.copilot_runner import _resolve_launcher


def _version_key(name: str) -> tuple:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", name)
    if match is None:
        return ()
    release = tuple(int(match[index]) for index in (1, 2, 3))
    suffix = match[4]
    parts = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in (suffix or "").split(".")
    )
    return (*release, suffix is None, parts)


def _resolve_live_launcher() -> str:
    override = os.environ.get("NOTEPAD_COPILOT_RUNTIME")
    if override:
        path = Path(override).expanduser()
        if not path.is_file() or path.suffix.lower() not in (".js", ".exe"):
            raise FileNotFoundError(
                "NOTEPAD_COPILOT_RUNTIME must point to an installed .js or .exe."
            )
        return str(path.resolve())

    # The SDK disables auto-update. An old npm bootstrap would otherwise load
    # its bundled runtime instead of the newer CLI already installed in cache.
    roots = []
    for name in ("COPILOT_PKG_CACHE_HOME", "COPILOT_CACHE_HOME", "COPILOT_HOME"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]) / "pkg")
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "copilot" / "pkg")
    roots.append(Path.home() / ".copilot" / "pkg")
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    candidates = []
    if shutil.which("node"):
        for root in dict.fromkeys(roots):
            for flavor in (f"win32-{arch}", "universal"):
                directory = root / flavor
                if not directory.is_dir():
                    continue
                for version in directory.iterdir():
                    key = _version_key(version.name)
                    entry = version / "index.js"
                    if (key and entry.is_file()
                            and (version / "app.js").is_file()
                            and (version / ".extraction-complete").is_file()):
                        candidates.append((key, entry))
    if candidates:
        return str(max(candidates, key=lambda item: item[0])[1])

    program, args = _resolve_launcher()
    if program and args and Path(args[0]).suffix.lower() == ".js":
        return args[0]
    executable = shutil.which("copilot.exe")
    if executable:
        return executable
    raise FileNotFoundError(
        "No installed Copilot SDK runtime found. Install/update Copilot CLI, "
        "or set NOTEPAD_COPILOT_RUNTIME to its index.js or native .exe."
    )
