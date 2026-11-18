"""Complete the Windows environment that MCP clients hand to stdio servers.

MCP clients start stdio servers with a minimal environment: the MCP SDKs pass only a short whitelist
(APPDATA, LOCALAPPDATA, PATH, SYSTEMROOT, TEMP, USERPROFILE, ...). Without ``PATHEXT`` Windows cannot
resolve batch files by name, so Quarto fails to start its bundled Sass compiler (``sass.bat``) and every
HTML theme fails to compile with an empty "Theme file compilation failed" message. Found in a live
session in the Claude desktop app; the end-to-end tests had passed because they ran from a full shell
environment.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"


def complete_windows_env(env: MutableMapping[str, str] | None = None, *, windows: bool | None = None) -> list[str]:
    """Add PATHEXT, COMSPEC, WINDIR and TMP when they are missing. Existing values are never changed.

    ``env`` defaults to ``os.environ``. Returns the names that were added (empty off Windows)."""
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return []
    target = os.environ if env is None else env
    present = {name.upper(): name for name in target}

    def get(name: str) -> str | None:
        key = present.get(name)
        return target[key] if key is not None else None

    root = get("SYSTEMROOT") or r"C:\Windows"
    defaults = {"PATHEXT": DEFAULT_PATHEXT, "COMSPEC": root.rstrip("\\") + r"\System32\cmd.exe", "WINDIR": root}
    temp = get("TEMP")
    if temp:
        defaults["TMP"] = temp
    added = []
    for name, value in defaults.items():
        if name not in present:
            target[name] = value
            added.append(name)
    return added
