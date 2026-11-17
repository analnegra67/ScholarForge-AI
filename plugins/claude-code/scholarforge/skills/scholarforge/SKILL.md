---
name: scholarforge
description: Write rigorous scientific books, textbooks, monographs, handbooks or encyclopedias with ScholarForge — research planning, verified sources, machine-checked mathematics, figures, and print-ready typesetting. Use when the user asks to create, continue, revise, verify or publish a scientific or academic book on any subject.
---

# ScholarForge: autonomous scientific book workflow

ScholarForge is a local MCP server. **You** do the thinking, researching and writing; ScholarForge plans the
work as tasks, validates and stores every result, verifies sources and formulas, keeps terminology consistent and
typesets the book (HTML, EPUB, PDF, print PDF). It never calls a language model itself.

## Start

1. If the ScholarForge tools are missing or fail, follow the `scholarforge-setup` skill.
2. New book: call `create_project` with `topic` and the user's request verbatim in `request`; pass any options the
   user stated (language, level, audience, depth, target_pages, formats, required_topics, elements...). Do not
   invent constraints the user did not ask for; ScholarForge chooses sensible defaults.
3. Existing book: `list_projects`, then `resume_project(project_id)` at the start of every new session.

## The work loop

Repeat until `next_task` returns `ready_to_build`:

1. `next_task(project_id)` returns `task_id`, `lease_token`, `instructions`, `context` and `output_schema`.
2. Do exactly what `instructions` say, using `context` (book parameters, position in the outline, summaries of
   earlier parts, terminology and notation registries, citable sources, existing cross-reference labels).
   Use your own knowledge and any research tools you have (web search, papers).
3. `submit_task(project_id, task_id, lease_token, result)` with a JSON object matching `output_schema`.
4. If `accepted` is false, read `feedback`, fix every `error`, and resubmit with the same `lease_token`.
   Warnings are recorded as issues; fix them when it is cheap.

Status values: `engine_busy` (call `next_task` again), `waiting_for_user` (an approval is needed — ask the user),
`ready_to_build` (call `build_book`), `complete`.

Work in long uninterrupted runs: after each accepted task immediately request the next one. Report progress to the
user briefly every few chapters, not after every task. Everything is saved after each submission, so the user can
stop at any time and resume later.

## Writing rules (enforced by validation)

- Quarto Markdown. Never write chapter (#) or section (##) headings; use ### and deeper.
- Theorem-like environments: `::: {#thm-name name="Title"}` … `:::` (prefixes thm lem prp cor cnj def exm exr sol
  rem alg); proofs `::: {.proof}`; referenced equations `$$ … $$ {#eq-name}`; cross-references `@thm-name`.
- Cite only citekeys listed in `context.sources` as `[@key]`. Never invent a reference, DOI, dataset or result.
  To use another work, add it to `new_sources` with identifiers you are sure of; it is verified before it can be cited.
- Put `<!-- sf:figure fig-name -->` where a figure belongs and describe it in `figures`. Prefer `matplotlib` (numpy,
  scipy; no file access; do not call savefig) or TikZ for exact scientific figures. Use `external` (paid image
  generation) only for illustrative pictures and only if the project allows it.
- Declare new terms and symbols; reuse the registry exactly. Changing a definition needs `redefine: true` and a
  justification; affected chapters are re-checked.
- Record the main assertions in `claims` (theorem, lemma, fact, empirical, historical, hypothesis, conjecture…)
  with citations; mark open questions `speculative: true`.
- Attach `math_checks` to formulas (SymPy syntax): identity, derivative, gradient, integral, limit, series,
  matrix_identity, inequality, solve, numeric, dimension. Use `scope: instance` when a check covers only a special
  case (fixed dimensions, sample values). Reference check ids from the claims they support.
- No padding, no repetition of earlier blocks, no generic filler — each paragraph must add information.

## Honesty

Never tell the user the content is guaranteed correct. Claim statuses mean: *verified* (proved by computer algebra
or Lean), *supported* (independent evidence agrees), *contested*, *unverified*, *requires expert review*. Your own
reviews are not independent verification. Point the user to `verification_report` for what was and was not checked.

## Finish

`build_book(project_id)` (minutes; formats from the project unless given), then summarise `verification_report`
and `preflight_report` for the user: where the files are, what passed, what needs attention (e.g. claims needing
expert review, printer-specific checks).
