#!/usr/bin/env python3
"""ScholarForge MCP launcher (standard library only).

Creates a private virtual environment on first use, installs ScholarForge into it
(bundled wheel if present, otherwise the pinned release from PyPI, or the path in
SCHOLARFORGE_PACKAGE), then runs the MCP server over stdio.

    python launcher.py            run the MCP server (installs on first run)
    python launcher.py --setup    install/repair the runtime and print a status line
    python launcher.py --where    print the runtime location

stdout is reserved for the MCP protocol: all messages go to stderr.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MIN_PYTHON = (3, 12)  # the pinned NumPy/SciPy/contourpy in constraints.txt require 3.12
HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent


def log(msg):
    sys.stderr.write("[scholarforge-launcher] " + msg + "\n")
    sys.stderr.flush()


def plugin_version():
    if os.environ.get("SCHOLARFORGE_RUNTIME_VERSION"):
        return os.environ["SCHOLARFORGE_RUNTIME_VERSION"]
    for manifest in (PLUGIN_ROOT / ".claude-plugin" / "plugin.json", PLUGIN_ROOT / ".codex-plugin" / "plugin.json"):
        if manifest.exists():
            try:
                return json.loads(manifest.read_text(encoding="utf-8")).get("version", "0.0.0")
            except ValueError:
                pass
    return "0.0.0"


def runtime_root():
    explicit = os.environ.get("SCHOLARFORGE_RUNTIME_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit) / "runtime"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "ScholarForge" / "runtime"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ScholarForge" / "runtime"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "ScholarForge" / "runtime"


def venv_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def package_spec(version):
    override = os.environ.get("SCHOLARFORGE_PACKAGE")
    if override:
        return override
    wheels = sorted((HERE / "wheels").glob("scholarforge-*.whl"))
    if wheels:
        return str(wheels[-1])
    return "scholarforge==" + version


def venv_support():
    import importlib.util

    return importlib.util.find_spec("ensurepip") is not None and importlib.util.find_spec("venv") is not None


LOCK_STALE_SECONDS = 1800
LOCK_WAIT_SECONDS = 900
POLL_SECONDS = 2


def pid_alive(pid):
    """True if a process with this id exists. Never signals it (os.kill would terminate it on Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: the process exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_owners(lock):
    try:
        text = lock.read_text(encoding="ascii", errors="ignore")
    except OSError:
        return []
    return [int(part) for part in text.split() if part.isdigit()]


def write_lock(lock, pids):
    try:
        lock.write_text(" ".join(str(p) for p in pids), encoding="ascii")
    except OSError:
        pass


def acquire_lock(lock, deadline):
    """Exclusive installation lock. The file lists the launcher and its running child (venv/pip), so a
    launcher killed by an impatient MCP client does not block later starts, while a pip process it left
    running still does (two pip runs must never write into the same environment)."""
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            owners = lock_owners(lock)
            if age > LOCK_STALE_SECONDS or (owners and not any(pid_alive(p) for p in owners)):
                log("removing a stale installation lock left by process(es) " + (" ".join(map(str, owners)) or "?"))
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.time() > deadline:
                raise SystemExit("[scholarforge-launcher] another ScholarForge installation is still running "
                                 "(process " + " ".join(map(str, owners)) + "); try again in a few minutes")
            time.sleep(POLL_SECONDS)


def run_locked(cmd, lock):
    proc = subprocess.Popen(cmd, stdout=sys.stderr)
    write_lock(lock, [os.getpid(), proc.pid])
    code = proc.wait()
    write_lock(lock, [os.getpid()])
    if code:
        raise subprocess.CalledProcessError(code, cmd)


def wheel_fingerprint(spec):
    """sha256 of a bundled wheel, so that a rebuilt wheel with the same version is installed again."""
    path = Path(spec)
    if spec.endswith(".whl") and path.is_file():
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def ensure_runtime(force=False):
    version = plugin_version()
    root = runtime_root()
    venv = root / ("venv-" + version)
    marker = venv / "scholarforge-installed.json"
    spec = package_spec(version)
    fingerprint = wheel_fingerprint(spec)

    def ready():
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
            return (info.get("spec") == spec and info.get("fingerprint") == fingerprint
                    and Path(info.get("python", "")).exists())
        except (OSError, ValueError):
            return False

    if not force and ready():
        return venv_python(venv)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".install.lock"
    acquire_lock(lock, time.time() + LOCK_WAIT_SECONDS)
    try:
        if not force and ready():  # another launcher finished while we waited
            return venv_python(venv)
        had_install = marker.exists()
        log("preparing the ScholarForge runtime in " + str(venv) + " (one-time, a few minutes)")
        if not venv_python(venv).exists() and not venv_support():
            raise SystemExit("[scholarforge-launcher] this Python cannot create virtual environments (the "
                             "'ensurepip' module is missing). Debian/Ubuntu: sudo apt install python3-venv; "
                             "or point the plugin option python_command to another Python 3.12+.")
        if not venv_python(venv).exists():
            run_locked([sys.executable, "-m", "venv", str(venv)], lock)
        py = str(venv_python(venv))
        run_locked([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], lock)
        cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", spec]
        constraints = HERE / "constraints.txt"
        if constraints.exists() and not os.environ.get("SCHOLARFORGE_PACKAGE"):
            cmd += ["-c", str(constraints)]
        run_locked(cmd, lock)
        if force or (had_install and fingerprint):
            # pip skips a package whose version is already installed; a rebuilt wheel with the same
            # version (or an explicit --reinstall) must still replace the code
            run_locked([py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                        "--force-reinstall", "--no-deps", spec], lock)
        marker.write_text(json.dumps({"spec": spec, "fingerprint": fingerprint, "python": py,
                                      "installed_at": time.time(), "plugin_version": version}),
                          encoding="utf-8")
        log("runtime ready")
        return venv_python(venv)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def main(argv):
    if sys.version_info < MIN_PYTHON:
        log("Python %d.%d+ is required (found %s). Install it from https://www.python.org and set the plugin "
            "option python_command." % (MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0]))
        return 1
    if "--where" in argv:
        print(str(runtime_root()))
        return 0
    try:
        py = ensure_runtime(force="--reinstall" in argv)
    except subprocess.CalledProcessError as exc:
        hint = ""
        if package_spec(plugin_version()).startswith("scholarforge=="):
            hint = (" This copy of the plugin has no bundled wheel, so ScholarForge was requested from PyPI;"
                    " if that version is not published there, install a plugin build made with"
                    " 'scripts/build_plugins.py --wheel'.")
        log("installation failed (exit code %s). Check your internet connection and run "
            "'python launcher.py --setup' in a terminal to see the full output." % exc.returncode + hint)
        return 1
    if "--setup" in argv or "--reinstall" in argv:
        out = subprocess.run([str(py), "-m", "scholarforge", "--json", "doctor"], stdout=subprocess.PIPE,
                             stderr=sys.stderr)
        try:
            report = json.loads(out.stdout.decode("utf-8").strip().splitlines()[-1])
            missing = [c["name"] for c in report.get("checks", []) if not c["ok"]]
            log("ScholarForge runtime OK; missing or optional components: " + (", ".join(missing) or "none"))
        except (ValueError, IndexError, KeyError):
            log("runtime installed, but diagnostics did not run cleanly")
        return 0
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if os.name != "nt":
        os.execve(str(py), [str(py), "-m", "scholarforge.mcp.server"], env)
    proc = subprocess.run([str(py), "-m", "scholarforge.mcp.server"], env=env)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
