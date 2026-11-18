"""Build folders must avoid locations that a packaged (MSIX) host app redirects."""

from pathlib import Path

from scholarforge import paths
from scholarforge.publishing import quarto


def test_redirected_cache_folder_is_skipped(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    temp = tmp_path / "temp"
    monkeypatch.setattr(paths, "cache_dir", lambda: cache)
    monkeypatch.setattr(quarto.tempfile, "gettempdir", lambda: str(temp))
    redirected = cache / "builds"
    monkeypatch.setattr(paths, "package_redirect",
                        lambda p: Path("C:/Packages/App/LocalCache") if Path(p) == redirected else None)
    d = quarto.build_workdir("prj_TEST")
    assert d == temp / "scholarforge-builds" / "prj_TEST"
    assert d.is_dir()


def test_normal_cache_folder_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(paths, "package_redirect", lambda p: None)
    assert quarto.build_workdir("prj_TEST") == tmp_path / "cache" / "builds" / "prj_TEST"


def test_package_redirect_ignores_ordinary_folders(tmp_path):
    assert paths.package_redirect(tmp_path) is None
