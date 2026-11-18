"""The plugin launcher (stdlib-only script shared by the Claude Code and Codex plugins)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "plugins" / "shared" / "runtime" / "launcher.py"


@pytest.fixture
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("sf_launcher_under_test", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runtime_location_and_spec(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_DIR", str(tmp_path))
    assert launcher.runtime_root() == tmp_path / "runtime"
    monkeypatch.setenv("SCHOLARFORGE_PACKAGE", "/some/path")
    assert launcher.package_spec("1.2.3") == "/some/path"
    monkeypatch.delenv("SCHOLARFORGE_PACKAGE")
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_VERSION", "9.9.9")
    assert launcher.plugin_version() == "9.9.9"


def test_existing_runtime_is_reused_without_installing(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_VERSION", "0.0.1")
    monkeypatch.setenv("SCHOLARFORGE_PACKAGE", "pkg-spec")
    venv = tmp_path / "runtime" / "venv-0.0.1"
    venv.mkdir(parents=True)
    (venv / "scholarforge-installed.json").write_text(json.dumps({"spec": "pkg-spec", "python": sys.executable}))
    monkeypatch.setattr(launcher.subprocess, "check_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert launcher.ensure_runtime() == launcher.venv_python(venv)


def test_missing_venv_support_gives_actionable_message(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_VERSION", "0.0.2")
    monkeypatch.setattr(launcher, "venv_support", lambda: False)
    with pytest.raises(SystemExit) as exc:
        launcher.ensure_runtime()
    assert "python3-venv" in str(exc.value)
    assert not (tmp_path / "runtime" / ".install.lock").exists()  # lock released


def test_failed_pypi_install_explains_missing_wheel(launcher, monkeypatch, capsys):
    import subprocess

    def boom(force=False):
        raise subprocess.CalledProcessError(1, ["pip"])

    monkeypatch.setattr(launcher, "ensure_runtime", boom)
    monkeypatch.setattr(launcher, "package_spec", lambda version: "scholarforge==0.0.0")
    assert launcher.main(["--setup"]) == 1
    err = capsys.readouterr().err
    assert "installation failed" in err and "no bundled wheel" in err


def _dead_pid():
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_pid_alive_never_kills_and_detects_exit(launcher):
    import os

    assert launcher.pid_alive(os.getpid())
    assert launcher.pid_alive(os.getpid())  # still alive: the check must not signal the process
    assert not launcher.pid_alive(_dead_pid())
    assert not launcher.pid_alive(0)


def test_lock_of_dead_launcher_is_taken_over(launcher, tmp_path):
    import os
    import time

    lock = tmp_path / ".install.lock"
    lock.write_text(str(_dead_pid()))  # a launcher killed by the MCP client during installation
    launcher.acquire_lock(lock, time.time() + 5)
    assert launcher.lock_owners(lock) == [os.getpid()]


def test_lock_with_running_child_blocks(launcher, monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(launcher, "POLL_SECONDS", 0.05)
    lock = tmp_path / ".install.lock"
    lock.write_text(f"{_dead_pid()} {os.getpid()}")  # launcher gone, but its pip child still runs
    with pytest.raises(SystemExit) as exc:
        launcher.acquire_lock(lock, time.time() + 0.3)
    assert "still running" in str(exc.value)
    assert lock.exists()


def test_installation_steps_record_child_pid(launcher, tmp_path):
    import os

    lock = tmp_path / ".install.lock"
    lock.write_text(str(os.getpid()))
    seen = tmp_path / "seen.txt"
    code = f"import pathlib; pathlib.Path({str(seen)!r}).write_text(pathlib.Path({str(lock)!r}).read_text())"
    launcher.run_locked([sys.executable, "-c", code], lock)
    owners = [int(p) for p in seen.read_text().split()]
    assert owners[0] == os.getpid() and len(owners) == 2  # launcher + running child while it ran
    assert launcher.lock_owners(lock) == [os.getpid()]


def _fake_runtime(launcher, monkeypatch, tmp_path, wheel_bytes, marker=None):
    """A plugin runtime whose venv exists; pip calls are recorded instead of run."""
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCHOLARFORGE_RUNTIME_VERSION", "0.0.3")
    wheel = tmp_path / "scholarforge-0.0.3-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes)
    monkeypatch.setenv("SCHOLARFORGE_PACKAGE", str(wheel))
    venv = tmp_path / "data" / "runtime" / "venv-0.0.3"
    py = launcher.venv_python(venv)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    if marker is not None:
        (venv / "scholarforge-installed.json").write_text(json.dumps(dict(marker, spec=str(wheel), python=str(py))))
    calls = []
    monkeypatch.setattr(launcher, "run_locked", lambda cmd, lock: calls.append(cmd))
    return venv, wheel, calls


def test_rebuilt_wheel_with_same_version_is_reinstalled(launcher, monkeypatch, tmp_path):
    venv, wheel, calls = _fake_runtime(launcher, monkeypatch, tmp_path, b"new build",
                                       marker={"fingerprint": "sha-of-an-older-build"})
    launcher.ensure_runtime()
    assert any("--force-reinstall" in cmd for cmd in calls)
    info = json.loads((venv / "scholarforge-installed.json").read_text())
    assert info["fingerprint"] == launcher.wheel_fingerprint(str(wheel))
    calls.clear()
    launcher.ensure_runtime()  # now up to date: nothing to do
    assert calls == []


def test_first_install_does_not_force_reinstall(launcher, monkeypatch, tmp_path):
    venv, wheel, calls = _fake_runtime(launcher, monkeypatch, tmp_path, b"first build")
    launcher.ensure_runtime()
    assert calls and not any("--force-reinstall" in cmd for cmd in calls)


def test_old_python_is_refused(launcher, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "MIN_PYTHON", (99, 0))
    assert launcher.main([]) == 1
    assert "required" in capsys.readouterr().err
