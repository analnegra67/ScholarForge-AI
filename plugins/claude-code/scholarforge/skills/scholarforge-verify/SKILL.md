---
name: scholarforge-verify
description: Verify the scientific content of a ScholarForge book — sources, citations, mathematical checks, terminology and cross-references — and explain the verification report. Use when the user asks how reliable the book is, wants checks re-run, or needs to fix unverified sources or failed checks.
---

# Verification with ScholarForge

- `run_checks(project_id)` re-verifies pending sources, re-runs every math check, checks cross-references,
  circular dependencies between results and notation, and recomputes claim statuses.
- `verification_report(project_id)` writes `reports/verification-report.md`. Summarise it for the user:
  counts per claim status, sources that are not citable (not found, mismatched, retracted, unverifiable) and why,
  failed or inconclusive math checks, blocks marked *needs review*, open issues.
- `check_math(checks=[...])` checks formulas outside a project (SymPy in a sandbox). A *symbolic* pass is a proof
  of the stated identity; a *numeric* pass is evidence only; `scope: instance` checks cover a special case only.
- To fix a source: `add_source` with correct metadata (identifiers only if certain), or drop the citation via
  `save_block`. Never "fix" a reference by guessing a DOI.
- Status meanings — verified: machine-proved; supported: independent evidence agrees; contested: speculative or
  not supported by its source; unverified: no independent evidence (for example the source text was not
  retrievable); requires expert review: a check failed, the source is retracted, or the domain marks the claim as
  high-stakes (e.g. clinical statements).
- Never present unverified material as established. Your own re-reading is not independent verification.
