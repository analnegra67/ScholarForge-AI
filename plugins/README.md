# ScholarForge plugins for Claude Code and Codex

Both plugins are thin adapters over the same engine: identical skills (`plugins/shared/skills`), the same
stdlib launcher (`plugins/shared/runtime/launcher.py`) and the same local MCP server
(`scholarforge.mcp.server`). Regenerate the plugin folders after changing shared files:

```bash
python scripts/build_plugins.py --wheel --check
```

`--wheel` bundles the ScholarForge wheel and `constraints.txt` into `runtime/`, so the launcher installs
exactly that version; without it the launcher installs `scholarforge==<plugin version>` from PyPI.

**Before publishing a marketplace from a Git repository:** ScholarForge is not on PyPI, and
`.gitignore` excludes `plugins/**/runtime/wheels/*.whl`. A plugin installed from the repository therefore
has no wheel and its first start fails (the launcher says so). Either commit the bundled wheel for each
release (remove that ignore rule after `build_plugins.py --wheel`) or publish the package to PyPI first.

## Requirements on the user's machine

* Python 3.12+ (the launcher creates a private virtual environment on first start; ~150 MB of
  dependencies are downloaded once).
* For builds: Quarto and a TeX distribution with LuaLaTeX — installed on request by the `install_tools`
  MCP tool or `scholarforge tools install quarto,tinytex,epubcheck` (user profile, no admin rights).

## Claude Code

Repository marketplace: `.claude-plugin/marketplace.json` (plugin source `./plugins/claude-code/scholarforge`).

```bash
claude plugin marketplace add <path-or-owner/repo>
claude plugin install scholarforge@scholarforge
```

In a session: `/plugin install scholarforge --marketplace <owner>/<repo>` (Claude Code 2.1.275+).
For development: `claude --plugin-dir plugins/claude-code/scholarforge`.

* MCP server: `.mcp.json` runs `${user_config.python_command} ${CLAUDE_PLUGIN_ROOT}/runtime/launcher.py`;
  the runtime lives in `${CLAUDE_PLUGIN_DATA}` and survives plugin updates; tool timeout 1 h for builds.
* Options (`/config` → plugin): `python_command` (default `python`; use `python3` on most macOS/Linux
  systems or a full path), optional `openai_api_key` and `openalex_api_key` (sensitive, stored in secure
  storage and passed to the server as environment variables).
* Skills: `/scholarforge:scholarforge`, `/scholarforge:scholarforge-setup`, `/scholarforge:scholarforge-publish`,
  `/scholarforge:scholarforge-verify` — and they trigger automatically from their descriptions.
* Verified with `claude plugin validate` (Claude Code 2.1.325): marketplace and plugin pass.

First start: Claude Code starts the server as soon as the plugin is enabled. The first start installs the
runtime (about 2–3 minutes) and usually exceeds the client's start-up timeout; the installation keeps running,
and a reconnect after it finishes takes seconds. To prepare it in advance, run the setup skill, or in a
terminal set `SCHOLARFORGE_RUNTIME_DIR` to the plugin's data folder
(`~/.claude/plugins/data/scholarforge-<marketplace>`, e.g. `scholarforge-scholarforge`) and run
`python <plugin root>/runtime/launcher.py --setup` — without that variable the runtime goes to the default
location, which the Claude Code plugin does not use. The installation lock records the launcher and its pip
process, so a launcher killed by the client never blocks later starts, and two installations never run at once.

Verified on 2026-11-17 on Windows: installed from the local marketplace (`claude plugin marketplace add
<checkout>`, `claude plugin install scholarforge@scholarforge`, user scope); a plugin from a local marketplace
runs directly from the checkout. `claude mcp list` shows `plugin:scholarforge:scholarforge … Connected`; a
stdio client calling the installed server got 27 tools, `diagnostics` ok and a symbolic `check_math` proof.

## Codex

Repository marketplace: `.agents/plugins/marketplace.json` (plugin source `./plugins/codex/scholarforge`).

```bash
codex plugin marketplace add <path-or-owner/repo>
codex plugin add scholarforge@scholarforge
```

* Manifest `.codex-plugin/plugin.json` (skills `./skills/`, MCP `./.mcp.json`, interface metadata).
* MCP server: `python ./runtime/launcher.py` with `cwd: "."` (the installed plugin root),
  `startup_timeout_sec: 900` for the first installation, `tool_timeout_sec: 3600` for builds and an
  explicit `env_vars` pass-through list (Codex does not forward the full environment).
* Verified with Codex CLI 0.146 in an isolated `CODEX_HOME`: marketplace added, plugin installed and
  enabled, `codex mcp list` shows the server; the server started from the plugin cache answered MCP calls.
  A full model-driven Codex session was not run here (it requires the user's Codex login).

## Limitations

* The MCP server needs a local Python; this is stated in both listings.
* `claude.ai` / Cowork do not run local stdio servers; ScholarForge targets the Claude Code CLI/desktop app.
* Paid image generation is optional and billed to the user's own OpenAI account.
