"""Running Quarto: ASCII-safe working directories, timeouts, log capture."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scholarforge import paths
from scholarforge.errors import ToolMissing
from scholarforge.logging_setup import get_logger
from scholarforge.tools.detect import find_tool

log = get_logger("quarto")


@dataclass
class RenderRun:
    fmt: str
    ok: bool
    returncode: int
    seconds: float
    log_path: Path
    log_tail: str


def _is_ascii(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _short_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)  # type: ignore[attr-defined]
    return Path(buf.value) if n and buf.value else path


def build_workdir(project_id: str) -> Path:
    """A working directory without non-ASCII characters (TeX tools are fragile with them) and outside
    folders that a packaged (MSIX) host app redirects (Dart Sass cannot open files there)."""
    candidates = [paths.cache_dir() / "builds", Path(tempfile.gettempdir()) / "scholarforge-builds"]
    for base in candidates:
        base.mkdir(parents=True, exist_ok=True)
        real = paths.package_redirect(base)
        if real is not None:
            log.info("build folder %s is redirected by the host app to %s; trying another location", base, real)
            continue
        if _is_ascii(base):
            d = base / project_id
            d.mkdir(parents=True, exist_ok=True)
            return d
        short = _short_path(base)
        if _is_ascii(short):
            d = short / project_id
            d.mkdir(parents=True, exist_ok=True)
            return d
    if os.name == "nt":
        root = Path(os.environ.get("SystemDrive", "C:") + "\\") / "sfbuild"
        try:
            root.mkdir(exist_ok=True)
            d = root / project_id
            d.mkdir(exist_ok=True)
            return d
        except OSError:
            pass
    d = candidates[0] / project_id
    d.mkdir(parents=True, exist_ok=True)
    log.warning("no ASCII-only build directory available; LaTeX may fail on %s", d)
    return d


def quarto_exe() -> str:
    exe = find_tool("quarto")
    if not exe:
        raise ToolMissing("Quarto is not installed", hint="scholarforge tools install quarto")
    return exe


def render(project_dir: Path, fmt: str, logs_dir: Path, *, timeout_s: int = 1800,
           profile: str | None = None, log_name: str | None = None) -> RenderRun:
    import time

    exe = quarto_exe()
    args = [exe, "render", str(project_dir), "--to", fmt]
    if profile:
        args += ["--profile", profile]
    env = dict(os.environ)
    env.setdefault("QUARTO_LOG_LEVEL", "INFO")
    # make the managed TinyTeX visible to Quarto even when it is not on PATH
    lualatex = find_tool("lualatex")
    if lualatex:
        env["PATH"] = str(Path(lualatex).parent) + os.pathsep + env.get("PATH", "")
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / (log_name or f"render-{fmt}{'-' + profile if profile else ''}.log")
    start = time.monotonic()
    try:
        proc = subprocess.run(args, cwd=str(project_dir), env=env, capture_output=True, timeout=timeout_s)
        out = proc.stdout.decode("utf-8", "replace") + "\n" + proc.stderr.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = f"timed out after {timeout_s}s\n" + ((exc.stdout or b"").decode("utf-8", "replace"))
        code = -1
    seconds = time.monotonic() - start
    log_path.write_text(out, encoding="utf-8")
    return RenderRun(fmt, code == 0, code, seconds, log_path, _tail(out))


def _tail(text: str, n: int = 40) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    important = [ln for ln in lines if re.search(r"error|warning|!|missing|undefined|failed", ln, re.I)]
    picked = important[-15:] or lines[-n:]
    return "\n".join(picked)[-4000:]


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
