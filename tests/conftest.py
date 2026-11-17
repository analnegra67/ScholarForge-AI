"""Shared fixtures.

Every test runs with ``SCHOLARFORGE_HOME`` pointing to a fresh temporary directory,
so tests never touch the user's real configuration, projects, keyring or caches.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--run-network", action="store_true", default=False, help="run tests against live APIs")


def pytest_collection_modifyitems(config, items):
    run_net = config.getoption("--run-network")
    has_quarto = _tool_available("quarto")
    has_latex = _tool_available("lualatex")
    for item in items:
        if "network" in item.keywords and not run_net:
            item.add_marker(pytest.mark.skip(reason="live network test (use --run-network)"))
        if "quarto" in item.keywords and not has_quarto:
            item.add_marker(pytest.mark.skip(reason="Quarto not installed (scholarforge tools install quarto)"))
        if "latex" in item.keywords and not has_latex:
            item.add_marker(pytest.mark.skip(reason="LuaLaTeX not installed (scholarforge tools install tinytex)"))


def _tool_available(name: str) -> bool:
    try:
        from scholarforge.tools.detect import detect_all

        info = detect_all().get(name, {})
        return bool(info.get("available"))
    except Exception:
        return shutil.which(name) is not None


@pytest.fixture(autouse=True)
def sf_home(tmp_path, monkeypatch):
    home = tmp_path / "sfhome"
    home.mkdir()
    monkeypatch.setenv("SCHOLARFORGE_HOME", str(home))
    monkeypatch.setenv("SCHOLARFORGE_DISABLE_KEYRING", "1")
    monkeypatch.delenv("SCHOLARFORGE_CONFIG", raising=False)
    monkeypatch.delenv("SCHOLARFORGE_PROJECTS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SCHOLARFORGE_OPENAI_API_KEY", raising=False)
    # Keep the user's installed tools visible to tests (they live outside SCHOLARFORGE_HOME).
    real_tools = os.environ.get("SCHOLARFORGE_TOOLS_DIR")
    if not real_tools:
        import platformdirs

        candidate = Path(platformdirs.user_data_dir("ScholarForge", appauthor=False)) / "tools"
        if candidate.exists():
            monkeypatch.setenv("SCHOLARFORGE_TOOLS_DIR", str(candidate))
    from scholarforge.logging_setup import reset_logging_for_tests

    reset_logging_for_tests()
    yield home
    reset_logging_for_tests()


@pytest.fixture
def settings(sf_home):
    from scholarforge.settings import load_settings

    s = load_settings()
    s.network.enabled = False  # unit tests are offline unless they opt in
    return s


@pytest.fixture
def service(settings):
    from scholarforge.core.service import ProjectService

    return ProjectService(settings)


@pytest.fixture
def project(service):
    from scholarforge.models.project import ProjectParams

    proj = service.create(ProjectParams(topic="Mathematical Foundations of Deep Learning"))
    yield proj
    proj.close()
