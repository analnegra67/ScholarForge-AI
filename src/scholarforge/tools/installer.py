"""User-level installation of external tools from their official sources.

* Quarto: official GitHub release archive, verified against the release's published
  SHA-256 checksum file; extracted under ``<tools_dir>/quarto-<version>``.
* TinyTeX (LuaLaTeX): installed by Quarto itself (``quarto install tinytex``) into the
  user profile; no administrator rights, PATH untouched.
* epubcheck: official W3C GitHub release, verified against the SHA-256 digest
  published by GitHub for the asset.

Nothing is installed without confirmation (interactive prompt, ``--yes`` or the
``confirm`` flag of the MCP tool).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import httpx

from scholarforge import paths
from scholarforge.errors import ConfirmationRequired, ExternalServiceError, ToolMissing
from scholarforge.logging_setup import get_logger
from scholarforge.tools.detect import find_tool
from scholarforge.util.atomic import atomic_write_json

log = get_logger("installer")

QUARTO_VERSION = "1.11.9"
EPUBCHECK_VERSION = "5.4.0"
GITHUB = "https://github.com"
SIZES = {"quarto": "~140 MB download", "tinytex": "~150 MB download, 300-600 MB installed",
         "epubcheck": "~5 MB download (requires Java 11+)"}


def _platform_asset(version: str) -> tuple[str, str]:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        return f"quarto-{version}-win.zip", "zip"
    if system == "Darwin":
        return f"quarto-{version}-macos.tar.gz", "tar"
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"quarto-{version}-linux-{arch}.tar.gz", "tar"


def _manifest_path() -> Path:
    return paths.tools_dir() / "tools.json"


def _update_manifest(name: str, info: dict[str, Any]) -> None:
    mf = _manifest_path()
    data = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    data[name] = info
    atomic_write_json(mf, data)


def _download(url: str, dest: Path, client: httpx.Client) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url, follow_redirects=True, timeout=600) as resp:
        if resp.status_code != 200:
            raise ExternalServiceError(f"download failed ({resp.status_code}): {url}")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    os.replace(tmp, dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise ExternalServiceError(f"unsafe path in archive: {member.filename}")
        zf.extractall(target)


def _safe_extract_tar(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tf:
        if sys.version_info >= (3, 12):
            tf.extractall(target, filter="data")
        else:  # pragma: no cover
            for member in tf.getmembers():
                dest = (target / member.name).resolve()
                if not str(dest).startswith(str(target.resolve())) or member.issym() or member.islnk():
                    raise ExternalServiceError(f"unsafe member in archive: {member.name}")
            tf.extractall(target)


def install_quarto(version: str = QUARTO_VERSION, client: httpx.Client | None = None) -> dict[str, Any]:
    client = client or httpx.Client(headers={"User-Agent": "ScholarForge-installer"})
    asset, kind = _platform_asset(version)
    base = f"{GITHUB}/quarto-dev/quarto-cli/releases/download/v{version}"
    downloads = paths.tools_dir() / "downloads"
    sums = _download(f"{base}/quarto-{version}-checksums.txt", downloads / f"quarto-{version}-checksums.txt", client)
    expected = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            expected = parts[0].lower()
    if not expected:
        raise ExternalServiceError(f"no checksum published for {asset}")
    archive = _download(f"{base}/{asset}", downloads / asset, client)
    actual = _sha256(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(f"checksum mismatch for {asset}: expected {expected}, got {actual}")
    target = paths.tools_dir() / f"quarto-{version}"
    if not target.exists():
        tmp = target.with_name(target.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        (_safe_extract_zip if kind == "zip" else _safe_extract_tar)(archive, tmp)
        os.replace(tmp, target)
    exe = next((p for p in target.rglob("quarto.exe" if os.name == "nt" else "quarto")
                if p.is_file() and p.parent.name == "bin"), None)
    if exe is None:
        raise ToolMissing(f"quarto executable not found after extracting {asset}")
    if os.name != "nt":
        exe.chmod(0o755)
    proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120)
    info = {"path": str(exe), "version": proc.stdout.strip(), "sha256": actual, "source": f"{base}/{asset}"}
    _update_manifest("quarto", info)
    return info


def install_tinytex() -> dict[str, Any]:
    quarto = find_tool("quarto")
    if not quarto:
        raise ToolMissing("Quarto is required to install TinyTeX", hint="scholarforge tools install quarto")
    proc = subprocess.run([quarto, "install", "tinytex", "--no-prompt"], capture_output=True, text=True,
                          timeout=3600)
    lualatex = find_tool("lualatex")
    if not lualatex:
        raise ToolMissing("TinyTeX installation did not produce lualatex",
                          details={"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]})
    info = {"path": lualatex, "installed_by": "quarto install tinytex", "log_tail": (proc.stdout + proc.stderr)[-800:]}
    _update_manifest("lualatex", {"path": lualatex})
    return info


def install_epubcheck(version: str = EPUBCHECK_VERSION, client: httpx.Client | None = None) -> dict[str, Any]:
    client = client or httpx.Client(headers={"User-Agent": "ScholarForge-installer"})
    api = f"https://api.github.com/repos/w3c/epubcheck/releases/tags/v{version}"
    resp = client.get(api, timeout=60)
    if resp.status_code != 200:
        raise ExternalServiceError(f"cannot read release metadata for epubcheck {version}")
    asset = next((a for a in resp.json().get("assets", []) if a["name"] == f"epubcheck-{version}.zip"), None)
    if asset is None:
        raise ExternalServiceError(f"epubcheck-{version}.zip not found in the release")
    expected = (asset.get("digest") or "").removeprefix("sha256:").lower()
    downloads = paths.tools_dir() / "downloads"
    archive = _download(asset["browser_download_url"], downloads / asset["name"], client)
    actual = _sha256(archive)
    if expected and actual != expected:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(f"checksum mismatch for {asset['name']}")
    target = paths.tools_dir() / f"epubcheck-{version}"
    if not target.exists():
        _safe_extract_zip(archive, target)
    jar = next(target.rglob("epubcheck.jar"), None)
    if jar is None:
        raise ToolMissing("epubcheck.jar not found after extraction")
    info = {"path": str(jar), "version": version, "sha256": actual, "verified_digest": bool(expected),
            "source": asset["browser_download_url"]}
    _update_manifest("epubcheck", info)
    return info


INSTALLERS = {"quarto": install_quarto, "tinytex": install_tinytex, "epubcheck": install_epubcheck}


def install(names: list[str], *, assume_yes: bool = False) -> dict[str, Any]:
    unknown = [n for n in names if n not in INSTALLERS]
    if unknown:
        raise ToolMissing(f"unknown tool(s): {unknown}; available: {sorted(INSTALLERS)}")
    if not assume_yes:
        summary = ", ".join(f"{n} ({SIZES[n]})" for n in names)
        if not sys.stdin.isatty():
            raise ConfirmationRequired(f"installing {summary} needs confirmation", hint="re-run with --yes")
        answer = input(f"Install {summary} into {paths.tools_dir()} / your user profile? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return {"installed": {}, "cancelled": True}
    results: dict[str, Any] = {}
    for name in names:
        try:
            results[name] = {"ok": True, **INSTALLERS[name]()}
        except Exception as exc:  # report each tool separately
            log.exception("installing %s failed", name)
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"installed": results}
