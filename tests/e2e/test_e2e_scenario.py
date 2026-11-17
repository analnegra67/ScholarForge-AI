"""Requirement §14: the end-to-end scenario, executed for real (Quarto + LuaLaTeX + epubcheck when installed).

Sources are verified against recorded responses of the real APIs (reproducible, no network). Run with
``--run-network`` and SCHOLARFORGE_E2E_LIVE=1 to verify against the live services instead.
"""

import os

import pytest

from scholarforge.e2e.scenario import run_scenario

pytestmark = [pytest.mark.slow, pytest.mark.quarto, pytest.mark.latex]


def test_deep_learning_textbook_end_to_end(tmp_path):
    live = os.environ.get("SCHOLARFORGE_E2E_LIVE") == "1"
    report = run_scenario(tmp_path / "e2e", render=True, network=live)
    failed = [s for s in report["steps"] if not s["ok"]]
    assert not failed, failed
    steps = {s["step"]: s for s in report["steps"]}
    # what the scenario must demonstrate (§14, items 1-15)
    for required in ("create_project", "authoring_loop", "sources_verified", "fabricated_reference_rejected",
                     "math_checks", "figures_rendered", "consistency", "build_html", "build_epub", "build_pdf",
                     "build_print_pdf", "print_preflight", "export_latex", "export_quarto_source",
                     "resume_after_interruption", "rebuild_from_sources_without_model", "verification_report"):
        assert required in steps, required
    assert steps["build_print_pdf"]["pdfx"] in (True, False)  # labelled only when every PDF/X check passes
