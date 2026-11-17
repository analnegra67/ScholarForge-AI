"""Locate external programs: configured path > ScholarForge-managed install > TinyTeX default > PATH."""

from __future__ import annotations

import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from scholarforge import paths

EXE = ".exe" if os.name == "nt" else ""


def _managed_manifest() -> dict[str, Any]:
    f = paths.tools_dir() / "tools.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def tinytex_roots() -> list[Path]:
    home = Path.home()
    roots = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "TinyTeX")
        roots.append(home / "AppData" / "Roaming" / "TinyTeX")
    elif sys.platform == "darwin":
        roots.append(home / "Library" / "TinyTeX")
    else:
        roots.append(home / ".TinyTeX")
    roots.append(paths.tools_dir() / "TinyTeX")
    return roots


def _tinytex_bin(name: str) -> str | None:
    for root in tinytex_roots():
        bindir = root / "bin"
        if not bindir.is_dir():
            continue
        for sub in sorted(bindir.iterdir()):
            cand = sub / (name + (".exe" if os.name == "nt" else ""))
            if cand.exists():
                return str(cand)
            if os.name == "nt":
                for ext in (".bat", ".cmd"):
                    alt = sub / (name + ext)
                    if alt.exists():
                        return str(alt)
    return None


def _settings_path(name: str) -> str | None:
    try:
        from scholarforge.settings import load_settings

        tools = load_settings().tools
    except Exception:
        return None
    key = {"quarto": "quarto", "lualatex": "lualatex", "java": "java", "dot": "dot", "lean": "lean",
           "lake": "lake"}.get(name)
    value = getattr(tools, key, None) if key else None
    return value if value and Path(value).exists() else None


def find_tool(name: str) -> str | None:
    configured = _settings_path(name)
    if configured:
        return configured
    manifest = _managed_manifest()
    if name in manifest and Path(manifest[name]["path"]).exists():
        return manifest[name]["path"]
    if name in ("lualatex", "tlmgr", "kpsewhich", "pdflatex", "xelatex", "makeindex", "dvisvgm"):
        found = _tinytex_bin(name)
        if found:
            return found
    found = shutil.which(name)
    return found


@functools.lru_cache(maxsize=32)
def _version(cmd: tuple[str, ...]) -> str:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0][:200] if out else ""


def epubcheck_jar() -> str | None:
    try:
        from scholarforge.settings import load_settings

        jar = load_settings().tools.epubcheck_jar
        if jar and Path(jar).exists():
            return jar
    except Exception:
        pass
    manifest = _managed_manifest()
    if "epubcheck" in manifest and Path(manifest["epubcheck"]["path"]).exists():
        return manifest["epubcheck"]["path"]
    return None


def detect_all() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def add(name: str, path: str | None, version_cmd: list[str] | None, required: bool, fix: str, detail: str = ""):
        version = _version(tuple(version_cmd)) if (path and version_cmd) else ""
        out[name] = {"available": bool(path), "path": path, "version": version, "required": required,
                     "detail": detail or (version or (path or "not found")), "fix": None if path else fix}

    quarto = find_tool("quarto")
    add("quarto", quarto, [quarto, "--version"] if quarto else None, True,
        "scholarforge tools install quarto  (or install from https://quarto.org)")
    pandoc = None
    if quarto:
        qdir = Path(quarto).resolve().parent
        for cand in (qdir / "tools" / f"pandoc{EXE}", qdir / "tools" / "x86_64" / f"pandoc{EXE}",
                     qdir / "tools" / "aarch64" / f"pandoc{EXE}"):
            if cand.exists():
                pandoc = str(cand)
                break
    pandoc = pandoc or find_tool("pandoc")
    add("pandoc", pandoc, [pandoc, "--version"] if pandoc else None, True, "bundled with Quarto")
    lualatex = find_tool("lualatex")
    add("lualatex", lualatex, [lualatex, "--version"] if lualatex else None, True,
        "scholarforge tools install tinytex  (or install TeX Live / MiKTeX / MacTeX)")
    kpse = find_tool("kpsewhich")
    add("kpsewhich", kpse, None, False, "part of the TeX distribution")
    java = find_tool("java")
    add("java", java, [java, "-version"] if java else None, False, "install a Java 11+ runtime (for epubcheck)")
    jar = epubcheck_jar()
    add("epubcheck", jar, None, False, "scholarforge tools install epubcheck", detail=jar or "not installed")
    dot = find_tool("dot")
    add("graphviz", dot, [dot, "-V"] if dot else None, False, "install Graphviz (optional; for DOT diagrams)")
    lean = find_tool("lean")
    add("lean", lean, [lean, "--version"] if lean else None, False,
        "optional: install Lean 4 via elan for formal proofs")
    mmdc = shutil.which("mmdc")
    add("mermaid-cli", mmdc, None, False, "optional: npm install -g @mermaid-js/mermaid-cli")
    out["platform"] = {"available": True, "required": False, "detail": f"{platform.system()} {platform.machine()}",
                       "path": None, "version": platform.python_version(), "fix": None}
    return out


def tex_find_file(name: str) -> str | None:
    """Locate a file inside the TeX distribution (e.g. an ICC profile from the colorprofiles package)."""
    kpse = find_tool("kpsewhich")
    if not kpse:
        return None
    try:
        proc = subprocess.run([kpse, name], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = proc.stdout.strip()
    return path if path and Path(path).exists() else None


def parse_version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x) for x in m.groups() if x is not None) if m else ()
