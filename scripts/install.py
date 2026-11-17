"""Install ScholarForge for the current user (standard library only).

    python scripts/install.py                 # runtime venv + `scholarforge` CLI shim
    python scripts/install.py --tools         # also install Quarto, TinyTeX, epubcheck (asks first)
    python scripts/install.py --claude        # also register the Claude Code marketplace + plugin
    python scripts/install.py --codex         # also register the Codex marketplace + plugin

Nothing outside the user profile is modified. Registration with Claude Code / Codex only runs when asked
for and uses their own CLIs (`claude plugin ...`, `codex plugin ...`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "shared" / "runtime"))
import launcher  # noqa: E402  (shared runtime logic: venv location, installation, locking)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--claude", action="store_true")
    ap.add_argument("--codex", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask before downloading external tools")
    args = ap.parse_args()
    if sys.version_info < (3, 12):
        print("Python 3.12+ is required", file=sys.stderr)
        return 1
    os.environ.setdefault("SCHOLARFORGE_PACKAGE", str(ROOT))
    version_file = (ROOT / "src" / "scholarforge" / "version.py").read_text(encoding="utf-8")
    os.environ.setdefault("SCHOLARFORGE_RUNTIME_VERSION", version_file.split('__version__ = "')[1].split('"')[0])
    py = launcher.ensure_runtime()
    print(f"runtime python: {py}")
    bindir = Path.home() / (".local/bin" if os.name != "nt" else "AppData/Local/ScholarForge/bin")
    bindir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shim = bindir / "scholarforge.cmd"
        shim.write_text(f'@"{py}" -m scholarforge %*\r\n', encoding="utf-8")
    else:
        shim = bindir / "scholarforge"
        shim.write_text(f'#!/bin/sh\nexec "{py}" -m scholarforge "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    print(f"CLI shim: {shim}  (add {bindir} to PATH to call `scholarforge` directly)")
    subprocess.call([str(py), "-m", "scholarforge", "doctor"])
    if args.tools:
        cmd = [str(py), "-m", "scholarforge", "tools", "install", "quarto,tinytex,epubcheck"]
        if args.yes:
            cmd.append("--yes")
        subprocess.call(cmd)
    if args.claude:
        claude = shutil.which("claude")
        if not claude:
            print("Claude Code CLI not found on PATH; run inside Claude Code: /plugin marketplace add " + str(ROOT))
        else:
            subprocess.call([claude, "plugin", "marketplace", "add", str(ROOT)])
            subprocess.call([claude, "plugin", "install", "scholarforge@scholarforge"])
    if args.codex:
        codex = shutil.which("codex")
        if not codex:
            print("Codex CLI not found on PATH")
        else:
            subprocess.call([codex, "plugin", "marketplace", "add", str(ROOT)])
            subprocess.call([codex, "plugin", "add", "scholarforge@scholarforge"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
