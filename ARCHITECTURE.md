# ScholarForge — Architecture

> Host AI is the reasoning engine. ScholarForge is the orchestration, verification and publishing engine.

## 1. Big picture

```
 ┌──────────────── Host AI (Claude Code / Codex) ────────────────┐
 │ user request → skill "scholarforge" → MCP tool calls          │
 └──────────────┬──────────────────────────────▲─────────────────┘
                │ next_task / submit_task      │ task: instructions + context pack + JSON schema
                ▼                              │
 ┌──────────── ScholarForge MCP server (stdio, local) ───────────┐
 │ adapters: mcp/server.py · cli/main.py · licensing/gate.py     │
 ├────────────────────────── core ───────────────────────────────┤
 │ core/workflow.py   Coordinator: task protocol, phases, resume │
 │ core/tasks.py      TaskQueue with leases (crash-safe)         │
 │ workflow/*.py      handlers per task kind (research → print)  │
 ├────────────── scientific engine ──────────┬─── publishing ────┤
 │ science/sources   Crossref/OpenAlex/arXiv │ publishing/       │
 │   S2/OpenLibrary + cache + resolver       │  assemble (Quarto)│
 │ science/mathcheck SymPy/NumPy/dims/Lean   │  quarto (render)  │
 │ science/consistency terms/notation/xrefs  │  pdfcolor, pdfx   │
 │ science/claims    evidence → status       │  cover, preflight │
 │ science/lint      padding, repetition     │  epubfix, builder │
 │ illustrations/    sandbox, renderers, QC, external images     │
 ├────────────────────────── storage ────────────────────────────┤
 │ SQLite per project (WAL, migrations) · Markdown files · YAML  │
 │ versions · checkpoints (content-addressed) · atomic writes    │
 └────────────────────────────────────────────────────────────────┘
        local tools: Quarto · Pandoc · LuaLaTeX (TinyTeX) · epubcheck
        network (optional): scholarly APIs; OpenAI Images (user's key)
        license server (separate deployment): keys, subscriptions — never book content
```

ScholarForge never calls a language model. Every generative step is a *task* rendered for the
host AI with instructions, a budgeted context pack and the JSON schema of the expected result.

## 2. Host-AI protocol

1. `create_project` → profile auto-selected (`profiles/`), `research.plan` queued.
2. `next_task` first runs due *engine* jobs within a time budget (source verification, figure
   rendering, block verification), then leases the next *host* task (lease token + expiry).
3. `submit_task` validates the result (pydantic schema + domain checks). Errors → feedback, the
   lease is kept so the host fixes and resubmits; after `max_attempts` soft-failing content is stored
   and flagged `needs_review`, structurally broken content never is.
4. Accepted results are persisted atomically and follow-up tasks are queued.

Task kinds: `research.plan`, `sources.verify`(engine), `sources.review`, `architecture.outline`,
`architecture.chapter` (large books: chapters detailed lazily), `draft.block`, `verify.block`(engine),
`claims.support`, `review.chapter`, `revise.block`, `consistency.recheck`, `figure.render`(engine),
`figure.fix`, `figure.review`, `figure.external`(user approval).

Phases are *derived* from stored data (`Coordinator.phase`), never trusted from a flag, so a crash or
manual edit cannot desynchronise them: research → architecture → drafting → review → ready_to_build → built.

### Revision and stop criteria
* a block is revised only for error-level issues or major/critical referee findings;
* at most `workflow.max_revision_rounds` (default 2) revisions per block, one referee review per chapter;
* exhausted → block `needs_review`, its open issues move to `expert_review` and to the report.

### Context management
Each task carries its own context (`workflow/context.py`): book parameters and profile guidance,
position in the outline, summaries of earlier blocks/chapters, the previous block's ending, relevant
terminology/notation, existing labels, citable sources — trimmed to a token budget. A new session needs
nothing but `resume_project`.

## 3. Storage and reliability

```
<project>/
  scholarforge.yaml        parameters, print/build settings (validated)
  research/plan.json|md    research plan, outline.json
  content/NN-chapter/NN-section/NN-block.md   authoritative text (human-editable)
  figures/<label>/         source.py|dot|tex, figure.pdf|svg|png, provenance.json
  build/logs               render logs        exports/  deliverables + manifest.json
  reports/                 verification, build, review, e2e reports
  .scholarforge/project.db SQLite (tasks, tree, sources, claims, checks, terms, labels, issues, ...)
  .scholarforge/history/   every version of every block
  .scholarforge/checkpoints, objects/   snapshots (SQLite backup API + content-addressed files)
```

* writes: temp file + fsync + `os.replace` with retries (Windows/OneDrive locks);
* SQLite: WAL, `BEGIN IMMEDIATE`, busy retries, thread-local connections, forward-only migrations with
  automatic backup and refusal to open newer schemas;
* leases expire; `resume` releases them and detects manual edits (hash mismatch → new version → re-verify);
* checkpoints at every phase change and before restores; restore is itself checkpointed;
* builds run in an ASCII-safe work directory (TeX tools and non-ASCII paths), outputs copied back;
  builds never modify sources and can always be repeated without the model.

## 4. Scientific verification

| Layer | What is checked | Independent of the LLM? |
|---|---|---|
| Source existence | DOI/arXiv/ISBN resolution in Crossref, OpenAlex, Semantic Scholar, arXiv, Open Library; title/author/year match; retraction flags | yes |
| Fabrication | identifiers that resolve nowhere → `not_found`; resolving to another work → `mismatch` | yes |
| Claim support | host judges abstract vs. claim; **only quotes found verbatim in the retrieved text count** | partly (quote check yes, judgement no) |
| Mathematics | SymPy exact proofs; numeric sampling (evidence); dimensional analysis; Lean 4 when installed | yes |
| Consistency | terminology registry with versioned redefinitions, notation conflicts, unresolved refs, circular theorem dependencies, forward dependencies | yes |
| Editorial | length, planned elements, padding phrases, repetition (shingles), structure | yes |

Claim statuses: verified · supported · contested · unverified · requires_expert_review (`science/claims.py`).
Instance checks (`scope: instance`, fixed matrix sizes) are evidence, never proof.

Untrusted code: math expressions are token-validated and evaluated in a separate `python -I` worker
with a timeout; figure code is AST-filtered, then executed in a child process with a PEP 578 audit hook
(no network, processes, ctypes, foreign file access, runtime exec), scrubbed environment, timeout and
memory limit (rlimit / Windows job object).

## 5. Publishing

`BookAssembler` writes a Quarto *book* project (chapters, parts, appendices: glossary, notation,
verification report, references; CSL-JSON bibliography; `\index` entries at term definitions).
`BookBuilder` renders HTML, EPUB (then repairs Quarto's invalid `alt` on figure wrappers), PDF
(LuaLaTeX, KOMA `scrbook`), LaTeX and Quarto source exports, and the print edition:

* geometry = trim + bleed, gutter from page count (re-rendered if the page count changes bracket);
* `pdfcolor`: annotations removed; colour → DeviceGray (B/W) or ICC-tagged sRGB (colour, late binding);
* `pdfx`: TrimBox/BleedBox, GTS_PDFX output intent (printer ICC; FOGRA39L from TeX's colorprofiles by
  default), XMP identification, PDF ≥ 1.6, Trapped; the PDF/X-4 label is applied only if the independent
  preflight passes, otherwise it is withdrawn;
* cover: back + spine + front with bleed, spine from page count × paper caliper (KDP factors or PPI),
  barcode area, spine text only above the printer's minimum.

Preflight (`publishing/preflight.py`) re-reads the files: boxes, page sizes, fonts embedded, colour
spaces actually used (content streams, forms, images), effective image ppi via CTM tracking,
annotations, JavaScript, output intent, XMP; EPUB structure + epubcheck; HTML internal links and
unresolved cross-references. Every report lists what is *not* checked.

## 6. Adapters

* **MCP server** (`mcp/server.py`, MCP Python SDK 2.x `MCPServer`, stdio): 27 tools; blocking work in
  worker threads; errors as tool errors with code/message/hint; elicitation for paid image approval
  when the client supports it.
* **CLI** (`scholarforge`): doctor, new, list, status, next, submit, resume, verify, report, build,
  preflight, export, checkpoint, tools install, secret, license, mcp, selftest.
* **Plugins** (`plugins/`): one shared source of skills and a stdlib launcher that creates a private
  venv on first start; Claude Code plugin (`.claude-plugin/plugin.json`, `.mcp.json` with
  `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_PLUGIN_DATA}`/`${user_config.*}`) and Codex plugin
  (`.codex-plugin/plugin.json`, `.mcp.json` with `cwd`, timeouts, env pass-through); marketplaces
  in `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`.

## 7. Licensing (separate from the core)

`licensing/` (client, Ed25519 tokens, device hash, gate, providers) and `license_server/` (Starlette +
SQLite). The core never imports licensing; only the adapters call the gate, and only for creating or
advancing work — reading, building and exporting existing books always works. Offline validation with
grace period; keys hashed with a pepper; e-mails hashed; idempotent signature-verified webhooks;
test provider + Stripe Billing implementation.
