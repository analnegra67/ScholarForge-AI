# ScholarForge

Local, AI-native scientific publishing engine for Claude Code and OpenAI Codex.

**The host AI is the reasoning engine. ScholarForge is the orchestration, verification and publishing
engine.** ScholarForge never calls a language model and never sends book content to any server it
operates. It plans the work as tasks for the AI client you already use, validates and stores every
result locally, verifies what can be verified (sources, mathematics, units, cross-references,
terminology) and typesets the book with Quarto + Pandoc + LuaLaTeX into HTML, EPUB 3, PDF and a
print-ready PDF (PDF/X-4 when it passes the checks) with cover.

- What works today and what is missing: [`PROGRESS.md`](PROGRESS.md)
- Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) · reasoning: [`DECISIONS.md`](DECISIONS.md)
- Plugins: [`plugins/README.md`](plugins/README.md) · catalogs: [`docs/CATALOG_SUBMISSION.md`](docs/CATALOG_SUBMISSION.md)
- License server: [`license_server/README.md`](license_server/README.md)
- Example output (test book): [`examples/deep-learning-foundations/`](examples/deep-learning-foundations/)

## Install

Claude Code (inside a session, Claude Code 2.1.275+):

```
/plugin install scholarforge --marketplace <owner>/<repo>
```

or from a shell: `claude plugin marketplace add <path-or-owner/repo>` then
`claude plugin install scholarforge@scholarforge`.

Codex: `codex plugin marketplace add <path-or-owner/repo>` then `codex plugin add scholarforge@scholarforge`.

> Installing from `<owner>/<repo>` needs a release whose plugin folders contain the bundled wheel (or a
> PyPI release of `scholarforge`) — see [`plugins/README.md`](plugins/README.md). From a local checkout,
> run `python scripts/build_plugins.py --wheel` first and add the marketplace by its local path.

Command line only: `python scripts/install.py` (add `--tools` to install Quarto, TinyTeX and epubcheck
into your user profile).

| Component | Needed for | Notes |
|---|---|---|
| Python 3.12+ | everything | the plugin launcher creates a private environment on first start (Debian/Ubuntu: also `python3-venv`) |
| Quarto ≥ 1.6 | HTML / EPUB / PDF builds | `scholarforge tools install quarto` (~140 MB, user profile) |
| TeX with LuaLaTeX | PDF and print PDF | `scholarforge tools install tinytex` (several hundred MB) |
| Java 11+ and epubcheck | EPUB validation (optional) | `scholarforge tools install epubcheck` |
| Lean 4 + Mathlib | formal proofs (optional) | used only when installed |
| OpenAI API key | external image generation (optional, paid by you) | stored in the OS credential store |

## Use

In Claude Code or Codex just ask, e.g. *"Create a rigorous scientific textbook about the Mathematical
Foundations of Deep Learning, with proofs, exercises, figures, references and a print-ready edition."*
The bundled skill drives the loop; you can stop at any time and continue later ("resume my book").

From a terminal:

```bash
scholarforge doctor
scholarforge new --request "Create a rigorous textbook about the Mathematical Foundations of Deep Learning"
scholarforge status <project-id>
scholarforge build <project-id>
scholarforge selftest            # end-to-end scenario on the bundled test book
```

## Honesty about verification

ScholarForge does **not** guarantee that every statement in a generated book is true. It runs layered
checks and labels each claim *verified*, *supported*, *contested*, *unverified* or *requires expert
review*, keeps the provenance of every check, and never treats a second opinion of the same language
model as independent evidence. The verification report lists exactly what was and was not checked.

## Pricing

ScholarForge Pro: $20/month, all features, no page or project limits. Third-party services you choose to
use (for example OpenAI image generation) are billed to you by their providers.
