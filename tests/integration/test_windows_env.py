"""Quarto must compile HTML themes when ScholarForge runs under an MCP client's minimal environment.

Regression test for a failure found in a live Claude desktop session: the MCP SDKs start stdio servers
with a short environment whitelist without PATHEXT, and Quarto then cannot launch its bundled Sass
compiler, so every HTML build failed with "Theme file compilation failed".
"""

import os
import subprocess

import pytest

from scholarforge.util.winenv import complete_windows_env

MCP_SDK_DEFAULT = ["APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PROCESSOR_ARCHITECTURE",
                   "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "USERNAME", "USERPROFILE", "PROGRAMFILES"]


@pytest.mark.quarto
@pytest.mark.skipif(os.name != "nt", reason="the minimal-environment problem is specific to Windows")
def test_html_theme_compiles_under_minimal_mcp_environment(tmp_path):
    from scholarforge.publishing.quarto import quarto_exe

    project = tmp_path / "doc"
    project.mkdir()
    (project / "_quarto.yml").write_text("project:\n  type: default\nformat:\n  html:\n    theme: cosmo\n",
                                         encoding="utf-8")
    (project / "index.qmd").write_text("---\ntitle: Minimal\n---\n\nHello $x^2$.\n", encoding="utf-8")
    full = {k.upper(): v for k, v in os.environ.items()}
    env = {k: full[k] for k in MCP_SDK_DEFAULT if k in full}
    cache = tmp_path / "cache"
    cache.mkdir()
    env["LOCALAPPDATA"] = str(cache)  # fresh Quarto cache, so that Sass really compiles
    assert "PATHEXT" in complete_windows_env(env, windows=True)
    proc = subprocess.run([str(quarto_exe()), "render", "index.qmd", "--to", "html"], cwd=project, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert proc.returncode == 0, (proc.stdout[-1500:] + proc.stderr[-1500:])
    assert (project / "index.html").exists()
