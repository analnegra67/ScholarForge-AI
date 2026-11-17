"""Run untrusted plotting code in a separate, restricted process.

Layers: (1) AST policy (:mod:`policy`), (2) PEP 578 audit hook in the child,
(3) process isolation: ``python -I``, scrubbed environment (no API keys), private
temporary working directory, wall-clock timeout, memory limit (POSIX rlimit;
Windows job object via pywin32 when available), no stdin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from scholarforge.illustrations.policy import ALLOWED_MODULES, check_python
from scholarforge.logging_setup import get_logger
from scholarforge.science.mathcheck.runner import scrubbed_env

log = get_logger("sandbox")


@dataclass
class SandboxResult:
    ok: bool
    files: dict[str, Path] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    error: str = ""
    policy_violations: list[str] = field(default_factory=list)


def _read_roots() -> list[str]:
    roots = {sys.prefix, sys.base_prefix, sysconfig.get_paths().get("purelib", ""),
             sysconfig.get_paths().get("platlib", ""), sysconfig.get_paths().get("stdlib", "")}
    try:
        import matplotlib

        roots.add(matplotlib.get_data_path())
        roots.add(matplotlib.get_cachedir())
        roots.add(matplotlib.get_configdir())
    except Exception:
        pass
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        roots.add(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.add(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    else:
        roots.update({"/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                      os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts", "/System/Library/Fonts",
                      os.path.expanduser("~/Library/Fonts"), "/etc/fonts", "/usr/share/fontconfig"})
    return sorted(r for r in roots if r)


def _limit_memory_posix(mb: int):
    def apply() -> None:  # pragma: no cover - runs in the child on POSIX only
        import resource

        limit = mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply


def _windows_job(memory_mb: int):
    try:
        import win32api  # type: ignore[import-not-found]
        import win32con  # type: ignore[import-not-found]
        import win32job  # type: ignore[import-not-found]
    except ImportError:
        return None
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["ProcessMemoryLimit"] = memory_mb * 1024 * 1024
    info["BasicLimitInformation"]["LimitFlags"] = (win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
                                                   | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job, win32api, win32con, win32job


def run_python_figure(code: str, *, timeout_s: int = 90, memory_mb: int = 2048, width_in: float = 6.0,
                      dpi: int = 400, policy: str = "strict") -> SandboxResult:
    violations = check_python(code)
    if violations and policy == "strict":
        return SandboxResult(False, error="code rejected by the sandbox policy:\n" + "\n".join(
            f"line {v.line}: {v.message}" for v in violations), policy_violations=[v.message for v in violations])
    workdir = Path(tempfile.mkdtemp(prefix="sf-fig-"))
    outdir = workdir / "out"
    outdir.mkdir()
    request = {"code": code, "outdir": str(outdir), "width_in": width_in, "dpi": dpi,
               "allowed_modules": sorted(ALLOWED_MODULES), "read_roots": _read_roots()}
    env = scrubbed_env()
    env["MPLBACKEND"] = "Agg"
    # single-threaded BLAS: predictable memory under the job/rlimit cap and no CPU storms
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"
    try:
        import matplotlib

        env["MPLCONFIGDIR"] = matplotlib.get_configdir()
    except Exception:
        pass
    child = [sys.executable, "-I", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "from scholarforge.illustrations.sandbox_child import main; raise SystemExit(main())",
             env["PYTHONPATH"]]
    kwargs: dict = {"cwd": str(workdir), "env": env, "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE}
    job = None
    if os.name == "posix":
        kwargs["preexec_fn"] = _limit_memory_posix(memory_mb)  # noqa: PLW1509
    elif os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        job = _windows_job(memory_mb)
    try:
        proc = subprocess.Popen(child, **kwargs)
    except OSError as exc:
        return SandboxResult(False, error=f"cannot start sandbox: {exc}")
    if job is not None:
        handle_job, win32api, win32con, win32job = job
        try:
            ph = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
            win32job.AssignProcessToJobObject(handle_job, ph)
        except Exception as exc:  # pragma: no cover - permission problems on locked-down systems
            log.warning("could not apply Windows job memory limit: %s", exc)
    try:
        out, err = proc.communicate(json.dumps(request).encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return SandboxResult(False, error=f"timed out after {timeout_s}s (simplify the computation)")
    finally:
        if job is not None:
            try:
                job[1].CloseHandle(job[0])
            except Exception:
                pass
    stderr = err.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        return SandboxResult(False, error=stderr[-4000:] or f"exit code {proc.returncode}")
    files = {ext: outdir / f"figure.{ext}" for ext in ("pdf", "svg", "png") if (outdir / f"figure.{ext}").exists()}
    meta = {}
    if (outdir / "meta.json").exists():
        meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    if "pdf" not in files or "png" not in files:
        return SandboxResult(False, error="the sandbox did not produce the expected files: " + stderr[-2000:])
    return SandboxResult(True, files=files, meta=meta)
