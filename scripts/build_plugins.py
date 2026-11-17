"""Assemble the Claude Code and Codex plugins from the shared sources.

    python scripts/build_plugins.py [--wheel] [--check]

* copies plugins/shared/skills and plugins/shared/runtime into each plugin directory
  (each plugin must be self-contained: neither platform allows paths outside the plugin);
* --wheel builds the ScholarForge wheel and bundles it with constraints.txt, so the
  launcher installs exactly this version (offline-capable apart from dependencies);
* --check validates the manifests (JSON, required fields, referenced paths, version sync)
  and runs `claude plugin validate` when the Claude Code CLI is available.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "plugins" / "shared"
TARGETS = {"claude-code": ROOT / "plugins" / "claude-code" / "scholarforge",
           "codex": ROOT / "plugins" / "codex" / "scholarforge"}


def version() -> str:
    text = (ROOT / "src" / "scholarforge" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def sync() -> None:
    for name, target in TARGETS.items():
        for sub in ("skills", "runtime"):
            dest = target / sub
            keep_wheels = dest / "wheels" if sub == "runtime" and (dest / "wheels").exists() else None
            saved = None
            if keep_wheels:
                saved = target.parent / f".wheels-{name}"
                shutil.rmtree(saved, ignore_errors=True)
                shutil.copytree(keep_wheels, saved)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(SHARED / sub, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if saved:
                shutil.copytree(saved, dest / "wheels")
                shutil.rmtree(saved, ignore_errors=True)
            if sub == "runtime" and (ROOT / "constraints.txt").exists():
                shutil.copy2(ROOT / "constraints.txt", dest / "constraints.txt")
        (target / "assets").mkdir(exist_ok=True)
        if (SHARED / "assets" / "icon.png").exists():
            shutil.copy2(SHARED / "assets" / "icon.png", target / "assets" / "icon.png")
            if name == "claude-code":
                shutil.copy2(SHARED / "assets" / "icon.png", target / ".claude-plugin" / "icon.png")
        manifest = target / (".claude-plugin" if name == "claude-code" else ".codex-plugin") / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["version"] = version()
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"synced {name}: {target}")


def build_wheel() -> Path:
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    subprocess.check_call([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(ROOT)])
    wheel = sorted(out.glob(f"scholarforge-{version()}-*.whl"))[-1]
    for target in TARGETS.values():
        wheels = target / "runtime" / "wheels"
        shutil.rmtree(wheels, ignore_errors=True)
        wheels.mkdir(parents=True)
        shutil.copy2(wheel, wheels / wheel.name)
        if (ROOT / "constraints.txt").exists():
            shutil.copy2(ROOT / "constraints.txt", target / "runtime" / "constraints.txt")
    print(f"bundled {wheel.name}")
    return wheel


def check() -> list[str]:
    problems: list[str] = []
    v = version()
    for name, target in TARGETS.items():
        manifest = target / (".claude-plugin" if name == "claude-code" else ".codex-plugin") / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("name") != "scholarforge":
            problems.append(f"{name}: plugin name must be 'scholarforge'")
        if data.get("version") != v:
            problems.append(f"{name}: version {data.get('version')} != package {v}")
        mcp = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))
        server = mcp["mcpServers"]["scholarforge"]
        if not any("launcher.py" in a for a in server["args"]):
            problems.append(f"{name}: MCP server must start runtime/launcher.py")
        if not (target / "runtime" / "launcher.py").exists():
            problems.append(f"{name}: runtime/launcher.py missing (run sync)")
        for skill in (SHARED / "skills").iterdir():
            md = target / "skills" / skill.name / "SKILL.md"
            if not md.exists():
                problems.append(f"{name}: skill {skill.name} missing")
                continue
            head = md.read_text(encoding="utf-8").split("---")[1]
            if f"name: {skill.name}" not in head or "description:" not in head:
                problems.append(f"{name}: skill {skill.name} frontmatter incomplete")
        if name == "codex":
            for key in ("skills", "mcpServers"):
                ref = data.get(key)
                if not ref or not (target / ref).exists():
                    problems.append(f"codex: manifest '{key}' path {ref} does not exist")
    for mp in (ROOT / ".claude-plugin" / "marketplace.json", ROOT / ".agents" / "plugins" / "marketplace.json"):
        data = json.loads(mp.read_text(encoding="utf-8"))
        for entry in data["plugins"]:
            src = entry["source"] if isinstance(entry["source"], str) else entry["source"]["path"]
            if not (ROOT / src).exists():
                problems.append(f"{mp.name}: source {src} does not exist")
    claude = shutil.which("claude")
    if claude:
        for path in (ROOT, TARGETS["claude-code"]):
            proc = subprocess.run([claude, "plugin", "validate", str(path)], capture_output=True, text=True)
            print(proc.stdout.strip() or proc.stderr.strip())
            if proc.returncode != 0:
                problems.append(f"claude plugin validate failed for {path}")
    else:
        print("note: Claude Code CLI not on PATH; skipped `claude plugin validate`")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheel", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sync()
    if args.wheel:
        build_wheel()
    if args.check:
        problems = check()
        for p in problems:
            print("PROBLEM:", p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
