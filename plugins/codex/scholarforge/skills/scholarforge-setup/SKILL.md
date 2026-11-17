---
name: scholarforge-setup
description: Install, repair or diagnose ScholarForge (Python runtime, Quarto, TinyTeX/LuaLaTeX, epubcheck, license). Use when ScholarForge tools are unavailable, fail to start, or the user asks to set up ScholarForge.
---

# ScholarForge setup

ScholarForge needs Python 3.12+ on the user's machine. Its MCP server runs from a private virtual environment that
the plugin's launcher creates on first start (this downloads the Python dependencies once, ~150 MB).

1. Prepare the runtime explicitly (recommended before first use, avoids a slow first start). The runtime must
   be prepared where the MCP server looks for it:
   - Claude Code: `SCHOLARFORGE_RUNTIME_DIR="${CLAUDE_PLUGIN_DATA}" python "${CLAUDE_PLUGIN_ROOT}/runtime/launcher.py" --setup`
   - Codex: `python ./runtime/launcher.py --setup` in the installed plugin folder (default runtime location).
   On macOS/Linux use `python3`. Then reconnect the MCP server (Claude Code: `/mcp`; Codex: restart the session).
   If the first automatic start timed out while dependencies were installing, the installation keeps running;
   reconnect after a few minutes.
2. Call the `diagnostics` tool. It lists what is installed and how to fix what is missing.
3. Large external tools are installed only with the user's consent. Tell the user what will be downloaded and
   its size, then call `install_tools` with `confirm=true`:
   - `quarto` (~140 MB): required for every output format.
   - `tinytex` (several hundred MB): LuaLaTeX for PDF and print PDF.
   - `epubcheck` (~5 MB, needs Java 11+): optional EPUB validation.
   Everything goes into the user profile; no administrator rights, PATH is not modified.
4. Optional: an OpenAI API key for paid illustration generation (`scholarforge secret set openai_api_key` in a
   terminal, or the plugin's configuration), and an OpenAlex API key for higher bibliographic lookup limits.

If Python is missing or older than 3.12, ask the user to install Python 3.12+ from python.org (Windows: tick
"Add python.exe to PATH") and repeat step 1. If the MCP server cannot find Python, set the plugin option
`python_command` (for example `python3` or `py`).
