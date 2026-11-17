# ScholarForge — progress

Last update: 2026-11-17 (session 1). Read this file first when continuing the work.

## Status by phase

| Phase | State | Evidence |
|---|---|---|
| 1 Core foundation | done | package, config, typed models, SQLite + migrations, atomic storage, versions, checkpoints, leased task queue, CLI, logging with secret redaction; unit tests |
| 2 Scientific engine | done | research → sources → architecture → drafting → verification → review → revision workflow; source verification (5 services), math checks (SymPy/numeric/dimensions, Lean adapter), consistency engine, claim statuses, reports; integration tests on recorded real API data |
| 3 Publishing engine | done | Quarto book generation, HTML/EPUB/PDF, print edition (bleed, boxes, colour conversion, output intent, PDF/X-4 label after preflight), cover + spine, LaTeX/Quarto exports, preflight (PDF/EPUB+epubcheck/HTML); a real test book is built |
| 4 Agent integration | done | MCP server (27 tools, SDK 2.3), skills, Claude Code plugin + marketplace (`claude plugin validate` passes, CLI 2.1.325), Codex plugin + marketplace (installed and enabled with Codex CLI 0.146; server started from the plugin cache answered MCP calls), launcher (runtime from the bundled wheel on Windows; actionable errors for missing venv support or wheel), installers, recovery, diagnostics |
| 5 Commercial infrastructure | done (not deployed) | licensing client (Ed25519 tokens, offline grace, device hash), gate (adapters only), license server (Starlette/SQLite), test payment provider, Stripe Billing integration (locally signed webhooks only), packaging (wheel, constraints, plugin bundles), catalog notes |
| 6 Full QA | done on Windows and Linux (WSL); macOS not run | 135 unit/integration tests (Windows: 135 passed; Linux: 134 passed, 1 skipped) + end-to-end test with real builds (Windows) |
| 7 UI/UX | minimal | human-readable terminal output for status/doctor/list/next/build; no GUI (by design: the AI client is the UI) |

## Test results (Windows 10, Python 3.14.6, this machine)

- `pytest tests/unit tests/integration`: **135 passed** in 209 s on the final code (includes a real MCP stdio subprocess with the classic
  initialize handshake, the licensing lifecycle, PDF/X finalisation with a real CMYK profile, sandbox
  escape attempts, external image generation through a mock transport).
- `pytest tests/e2e` (requirement §14, real Quarto 1.11.9 + LuaLaTeX/TinyTeX + epubcheck 5.4.0):
  **1 passed** (~5 min). The scenario: project from the exact §14 prompt; research plan; 13 candidate
  sources → 12 verified, 1 fabricated DOI rejected (not found in Crossref, OpenAlex, Semantic Scholar),
  1 mismatched DOI detected and corrected; outline (5 chapters, 6 blocks); interruption with a leased
  task and resume in a new session; 6 blocks with proofs, exercises, 3 sandboxed figures, 20 math checks
  (17 symbolic proofs, 3 numeric); chapter review → one bounded revision; claims 7 verified / 13 supported
  / 1 contested / 4 unverified; HTML (0 broken links), EPUB (epubcheck: 0 errors, 0 warnings), PDF,
  print interior + cover passing ScholarForge's PDF/X-4 checks, LaTeX and Quarto exports, rebuild from
  sources without any host call, verification report.
- Linux (WSL Ubuntu, Python 3.14.4): unit + integration **134 passed, 1 skipped** — see "Linux run" below.
- Tools: `claude plugin validate` (marketplace + plugin) pass; `codex plugin marketplace add` + `codex plugin
  add` + `codex mcp list` succeed in an isolated `CODEX_HOME`.
- Claude Code plugin installed for the owner (user scope, local marketplace = this checkout; the plugin runs
  directly from `plugins/claude-code/scholarforge`, runtime in `~/.claude/plugins/data/scholarforge-scholarforge`).
  The desktop app started the server right after installation; that first start was killed by the client's
  start-up timeout while pip was still installing — which exposed a launcher bug (a lock left by the killed
  launcher blocked restarts for 30 minutes), fixed with 5 new tests. After the fix, setup finished in 15 s,
  `claude mcp list` → `plugin:scholarforge:scholarforge … Connected`, and a stdio client calling the installed
  server got 27 tools, `diagnostics` ok (Quarto 1.11.9, LuaLaTeX, Pandoc 3.10, epubcheck, Java 17) and a
  symbolic `check_math` proof. The running Claude Code desktop session then loaded the plugin's 27 tools and
  4 skills, and `diagnostics` called from inside the app returned ok (projects root: the user's
  `OneDrive\Documents\ScholarForge`, flagged as cloud-synced). No model-driven authoring session has been
  run with it yet.

Example output of the test book: `examples/deep-learning-foundations/` (reading PDF, print interior and
cover, print specs, EPUB, HTML, LaTeX and Quarto sources, reports). Its README states what the example is
and is not (scripted host content, recorded API responses, 5-page target). Local absolute paths in its
reports were replaced with `<project>` / `<workdir>`.

## Environment notes (this development machine)

- The Claude desktop app is an MSIX package: processes started from it see `%LOCALAPPDATA%` and
  `%APPDATA%` redirected into `...\AppData\Local\Packages\Claude_<id>\LocalCache\`. The development venv,
  Quarto, TinyTeX, epubcheck and the example build made in session 1 physically live there and are **not
  visible from a normal terminal**. `scholarforge doctor` now reports this (`appdata_virtualized`). To use
  ScholarForge outside the app, install again from a normal terminal: `python scripts/install.py --tools`.
- WSL Ubuntu's Python has no `ensurepip`, so `python -m venv` fails there; the plugin launcher now detects
  this and prints the fix (`sudo apt install python3-venv`) instead of a bare pip error (checked in WSL).

## What is real vs. not verified

- The E2E scenario uses a **scripted host** (authored fixture content) instead of a live model, and
  **recorded real API responses** (live mode: `SCHOLARFORGE_E2E_LIVE=1 pytest tests/e2e --run-network`).
  The live-API path was exercised in this session (that run produced the recordings).
- Not executed here: a model-driven session inside Claude Code or Codex using the plugin (requires a
  session with the plugin loaded; the server, skills, manifests and installation were tested separately).
- Lean 4: adapter + "not installed"/`sorry` paths tested; no Lean toolchain on this machine.
- Graphviz/Mermaid renderers: implemented, not exercised (tools not installed).
- Stripe: signature verification and request shapes tested offline; no Stripe test-mode round trip.
- License server: not deployed; no production signing key; enforcement is `off` in source builds.
- PDF/X-4: ScholarForge's own checks only — not a certified ISO 15930-7 validation.
- macOS: not tested (CI workflow `.github/workflows/ci.yml` covers Linux/macOS/Windows once pushed).
- Books in non-Latin scripts (e.g. Russian): not built yet. The default LaTeX font (Latin Modern) has no
  Cyrillic glyphs, so until next task 7 is done a Russian PDF needs `build.mainfont` set to an installed
  font with Cyrillic; HTML and EPUB use the reader's fonts.

## Linux run (WSL Ubuntu, Python 3.14.4)

- `pytest tests/unit tests/integration` on the final code: **134 passed, 1 skipped** (114 s). The skipped
  test needs a CMYK printer ICC profile, which comes with TeX Live; TeX is not installed in WSL.
- Method: the repository is copied to `/tmp`; the distribution Python has no `ensurepip`, so a venv is
  created with `--without-pip` and pip is bootstrapped; dependencies are installed with `-c constraints.txt`.
  Quarto/LuaLaTeX builds and the E2E scenario were run on Windows only.
- Plugin launcher on that Python: stops with the actionable `python3-venv` message and leaves no partial
  runtime behind.

## Next tasks (in order)

0. Release packaging decision (blocks installing the plugins from a Git marketplace): ScholarForge is not
   on PyPI and `.gitignore` excludes `plugins/**/runtime/wheels/*.whl`. Either commit the bundled wheel per
   release (`scripts/build_plugins.py --wheel`) or publish to PyPI. Until then, install from a local
   checkout after building the wheel.
1. Run a real authoring session in Claude Code with `claude --plugin-dir plugins/claude-code/scholarforge`
   (and in Codex) on a small book; tune skill wording and context-pack budgets from what the model does.
2. Quarto float placement: figures deferred to chapter ends produce half-empty pages; evaluate
   `fig-pos` and KOMA float parameters.
3. Abstract retrieval for publishers that withhold abstracts (more claims could reach "supported"):
   consider Europe PMC (biomedicine) and arXiv versions of journal papers.
4. Graphviz/Mermaid: optional installers or pure-Python fallbacks; test them.
5. Deploy the license server (HTTPS, backups), generate the production key, Stripe test-mode E2E,
   merchant-of-record decision, public website with privacy policy / terms (catalog prerequisite).
6. Multi-volume builds (one Quarto project per volume) — the tree supports volumes, the assembler renders
   a single book with parts today.
7. Non-Latin books: default fonts for Cyrillic/Greek/CJK in LuaLaTeX (fontspec), tested on a Russian book.
8. Interactive figures for the electronic editions (e.g. Plotly in HTML with a static fallback for
   PDF/EPUB) and the optional Manim renderer — not implemented yet.
