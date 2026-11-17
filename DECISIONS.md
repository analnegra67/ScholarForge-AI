# Architecture decisions

Each entry: decision — reason — consequences. Dates are when the decision was made (2026-11-17).

1. **The host AI does all generation; ScholarForge never calls an LLM.** — Required by the product
   model (no inference costs, no own GPUs) and by MCP reality: a server cannot invoke the client's model
   through an arbitrary API. — Work is expressed as leased tasks with schemas; the loop is driven by the
   host via `next_task`/`submit_task`.

2. **Python 3.12+, pydantic v2, SQLite, plain files.** — Mature scientific stack (SymPy, NumPy, SciPy,
   Matplotlib, pikepdf), first-class MCP SDK, zero-admin storage. — One SQLite DB per project (portable
   folder); Markdown sources are authoritative and human-editable. The minimum was 3.11 until the pinned
   versions in `constraints.txt` turned out to require 3.12 (NumPy 2.5, SciPy 1.18, contourpy 1.4); untested
   alternative pins for 3.11 were not added.

3. **MCP Python SDK 2.x (`MCPServer`).** — The installed current SDK (2.3) renamed FastMCP and changed
   APIs; we target it directly and pin `mcp>=2.3,<3`. Tested in-process and over a real stdio subprocess
   with the classic initialize handshake used by today's clients.

4. **Derived phases and leases instead of a stored state machine.** — Robust to crashes, killed
   terminals and manual edits; a stale submission cannot overwrite newer work (`LeaseConflict`).

5. **Rejected submissions keep their lease.** — The host fixes and resubmits immediately with the same
   token; attempts are bounded (`max_attempts`). Found by an integration test.

6. **Verification statuses are evidence-based and conservative.** — "Verified" only for exact symbolic
   or formal proofs of the stated claim; numeric agreement, special-case checks and quoted source
   evidence give "supported"; host-model judgements alone never upgrade a claim. Absolute truth is never
   claimed.

7. **Quotes must be found in retrieved source text.** — The cheapest independent guard against
   hallucinated support. Consequence: claims citing works whose abstracts are not retrievable (some
   publishers withhold them from Crossref/OpenAlex) remain "unverified" — reported honestly.

8. **Two sandboxes.** Math: token whitelist + restricted namespace + isolated worker process with
   timeout. Figures: AST allow-list + PEP 578 audit hook + process isolation + memory limit. SymPy is not
   available to figure code (it evaluates strings). fontTools may compile internal expressions only when
   the call originates from its own files (needed for PDF font subsetting).

9. **Quarto + Pandoc + LuaLaTeX as the publishing core** (requirement). Builds run in an ASCII-safe
   work directory because TeX tools break on non-ASCII paths (common on non-English Windows profiles).

10. **Print colour: late binding.** — Colour editions keep RGB values but tag them ICC-based sRGB
    (allowed in PDF/X-4, converted by the printer's RIP with the output intent); B/W editions convert
    everything to DeviceGray. No lossy conversions of our own; unsupported constructs block the label.

11. **PDF/X-4 label only after an independent preflight passes.** — Our writer and our checker are
    separate code paths; if the checker fails, the label is withdrawn and the file shipped as a plain
    print PDF. Reports state that this is not a certified ISO 15930-7 validation.

12. **Output intent profile from TeX Live's `colorprofiles` (FOGRA39L) by default, or the printer's own
    ICC.** — Avoids redistributing ICC profiles; display profiles are refused as output intents.

13. **EPUB post-processing.** — Quarto 1.11 writes `alt` on figure wrapper `div`s, which epubcheck
    rejects (RSC-005). We remove invalid attributes and repack with `mimetype` first.

14. **OpenAlex is used with an API key when available.** — Since 2026 OpenAlex requires keys (free;
    100 credits/day without). Crossref is the primary DOI resolver; arXiv, Semantic Scholar and Open
    Library fill gaps. All responses are cached; recorded real responses make tests reproducible.

15. **No contact e-mail is sent to APIs unless the user configures one** (`network.contact_email`).

16. **External images: OpenAI Images API, `gpt-image-2.6-sunburst` by default** (verified on the
    official model list). Disabled by default, user's own key in the OS credential store, cost estimate
    from the official per-token prices with a dated source, explicit confirmation token and MCP
    elicitation when available; actual cost computed from the returned usage; AI-generated images are
    labelled in captions and provenance.

17. **Plugins share one source; the launcher installs a private runtime.** — Neither platform lets a
    plugin reference files outside its folder, so `scripts/build_plugins.py` copies the shared skills and
    launcher into each plugin. The venv lives in `${CLAUDE_PLUGIN_DATA}` (Claude Code) or the user data
    directory (Codex), survives plugin updates and is keyed by version.

18. **The interpreter for the MCP server is configurable** (`python_command` user option in Claude Code,
    default `python`). — There is no command that works on all OSes (`python3` is the Microsoft Store
    stub on Windows; `python` may be missing on macOS). The setup skill guides the user.

19. **Licensing is an adapter concern.** — The core has no license checks; enforcement is off in source
    builds and on in packaged releases; reading/exporting is never blocked. Ed25519 tokens, offline grace,
    device limit 3, hashed keys/e-mails, `secure_delete` in the server DB, idempotent webhooks.

20. **Payment providers behind an interface; Stripe implemented without its SDK.** — Signature
    verification and event mapping follow Stripe's documented scheme and are unit-tested with locally
    signed payloads; nothing contacts Stripe without configured secrets. A merchant-of-record setup
    (VAT/sales tax) should be evaluated before launch.

21. **Windows path length.** — Slugs in content paths are capped at 24 characters and temporary files
    use short names; found by an integration test that exceeded MAX_PATH.

22. **Dev environment outside OneDrive.** — The development venv and installed tools live under
    `%LOCALAPPDATA%\ScholarForge` (not synced); user projects may live anywhere, with a warning for
    cloud-synced folders.
