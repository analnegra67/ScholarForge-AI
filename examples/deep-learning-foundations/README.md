# Example output: *Mathematical Foundations of Deep Learning* (test book)

These files were produced on 2026-11-17 by the end-to-end scenario required by the specification (§14),
with ScholarForge 0.1.0, Quarto 1.11.9, LuaLaTeX (TeX Live 2026 via TinyTeX) and epubcheck 5.4.0 on
Windows 10. They are copied here unchanged, except that local absolute paths in the JSON/Markdown reports
were replaced with `<project>` and `<workdir>`.

**Read this first — what the example is and is not**

- It is a deliberately small book (target: 5 pages of text, 5 chapters, 6 blocks, 3 figures) so that the
  full pipeline runs in about five minutes inside the test suite. It is not a finished textbook.
- The text was supplied by a **scripted host**: content authored for the test and stored in
  [`src/scholarforge/e2e/fixtures/deep_learning/`](../../src/scholarforge/e2e/fixtures/deep_learning/).
  No language model wrote it during this run. Everything after that — source verification, math checks,
  consistency checks, claim classification, figure rendering, assembly, typesetting, preflight — was done
  by ScholarForge itself.
- Bibliographic metadata comes from **recorded real responses** of Crossref, OpenAlex, Semantic Scholar,
  arXiv and Open Library (34 responses, recorded in a live run in the same session), so the run is
  reproducible offline.
- One fabricated reference (DOI `10.9999/sf-fabricated.2019.00123`) and one reference with a wrong DOI were
  planted on purpose: the first was rejected, the second was detected and corrected to `10.1038/nature16961`.

## Files

| Path | What it is | Checked by |
|:--|:--|:--|
| [`pdf/book.pdf`](pdf/book.pdf) | Reading PDF, A4, 17 pages, hyperlinked | ScholarForge PDF preflight: pass (1 warning: document actions present) |
| [`print/interior.pdf`](print/interior.pdf) | Print interior, 25 pages, 7 × 10 in trim + 3.175 mm bleed, grayscale, FOGRA39 output intent, PDF/X-4 identification | ScholarForge PDF/X-4 checks: pass (not a certified ISO 15930-7 validation) |
| [`print/cover.pdf`](print/cover.pdf) | Full cover (back, spine 1.487 mm, front) with bleed | ScholarForge PDF/X-4 checks: pass (same caveat) |
| [`print/print-specs.md`](print/print-specs.md) | Trim, bleed, spine width, paper, colour, preflight results and what was *not* checked | — |
| [`epub/book.epub`](epub/book.epub) | EPUB 3 with MathML | epubcheck 5.4.0: 0 errors, 0 warnings ([`book.epubcheck.json`](epub/book.epubcheck.json)) |
| [`html/index.html`](html/index.html) | HTML book (open locally in a browser) | 10 pages, 0 broken internal links |
| [`latex/`](latex/) | Stand-alone LaTeX source (`lualatex book.tex`, see `README.txt`) | — |
| [`quarto-source/`](quarto-source/) | Quarto book project (`quarto render`) | — |
| [`reports/verification-report.md`](reports/verification-report.md) | What is verified, how, and what is not | — |
| [`reports/e2e-report.md`](reports/e2e-report.md) | Result of each scenario step (21 steps) | — |
| `reports/review-*.md` | Chapter reviews written by the (scripted) host | — |
| [`manifest.json`](manifest.json), `reports/build-report.md` | Manifest of the last build: the HTML rebuild from stored sources without any host call | — |
| [`scholarforge.yaml`](scholarforge.yaml) | Project configuration (book, print and build parameters) | — |

## Verification summary

| | Result |
|:--|:--|
| Sources | 13 candidates → 12 verified, 1 rejected (the planted fabricated DOI); 1 wrong DOI corrected |
| Math checks | 20 passed: 17 symbolic (SymPy), 3 numeric (evidence, not proof) |
| Claims | 7 verified, 13 supported, 1 contested (an open research question, labelled as such), 4 unverified, 0 requiring expert review |
| Figures | 3 rendered in the sandbox from inspectable Matplotlib code, approved after review |
| Consistency | 1 informational finding (one symbol used with two meanings across the book); the verification report lists only warnings and errors, so it appears in `reports/e2e-report.md` |

The four unverified claims are listed with the reason in the verification report — for example, a proof
written in prose is not machine-checked, and three claims cite sources whose abstracts could not be
retrieved, so the support could not be confirmed independently.

## Known layout limitations visible in this example

- Figures float to the end of their chapter in the PDFs, which leaves partly empty pages.
- The cover is the default generated design (no cover image).

## Reproduce

From a checkout with the tools installed (`scholarforge tools install quarto,tinytex,epubcheck`):

```bash
scholarforge selftest --workdir ./sf-example
```

or as a test: `pytest tests/e2e`. Add `--network` (CLI) to verify the sources against the live APIs
instead of the recordings.
