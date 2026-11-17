# Changelog

## 0.1.0 — 2026-11-17 (first development release)

- Core: projects, SQLite storage with migrations, atomic writes, versions, checkpoints, leased task queue,
  derived phases, resume after interruption, external-edit detection.
- Host-AI protocol with 15 task kinds, context packs, schema-validated results, bounded revisions.
- Scientific engine: source verification (Crossref, OpenAlex, arXiv, Semantic Scholar, Open Library) with
  fabricated/mismatched reference detection; SymPy/numeric/dimensional checks and optional Lean 4;
  terminology/notation registry; cross-reference and dependency-cycle checks; claim statuses; reports.
- Illustrations: sandboxed Matplotlib, Graphviz, TikZ, SVG, Mermaid; QC; OpenAI Images (opt-in, paid by the
  user, estimate + confirmation).
- Publishing: Quarto book (HTML, EPUB 3, PDF via LuaLaTeX), print edition with bleed, boxes, colour
  conversion, output intent and PDF/X-4 identification after independent preflight; cover with spine;
  LaTeX and Quarto source exports; EPUB repair for epubcheck.
- MCP server (27 tools) on the MCP Python SDK 2.x; Claude Code and Codex plugins; installers.
- Licensing module, license server, test provider and Stripe Billing integration.
- Terminal UX for `status`, `doctor`, `list`, `next`, `build`.
- Plugin launcher: clear messages when the Python cannot create virtual environments (missing
  `ensurepip`, e.g. Debian/Ubuntu without `python3-venv`) and when a plugin copy without a bundled wheel
  cannot install from PyPI; `doctor` reports Windows app-data virtualization by packaged (MSIX) host apps.
- Plugin launcher: the installation lock records the launcher and its running pip/venv process; a lock whose
  processes are gone is taken over at once (before, a launcher killed by the MCP client's start-up timeout
  blocked every restart for up to 30 minutes), and an orphaned pip still running keeps others waiting.
- Setup skill: a manual `--setup` for the Claude Code plugin now targets the plugin's data folder.
- Minimum Python raised from 3.11 to 3.12: the pinned NumPy 2.5.5, SciPy 1.18.1 and contourpy 1.4.0 in
  `constraints.txt` require it, so installation on 3.11 could not succeed (package metadata, launcher,
  installers, `doctor`, CI matrix and docs updated).
- Example output of the end-to-end test book in `examples/deep-learning-foundations/`.
