"""Human-readable terminal rendering for CLI results (``--json`` keeps the raw, stable output)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any

PHASES = ["research", "architecture", "drafting", "review", "ready_to_build", "built"]


def _supports_unicode() -> bool:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


OK, BAD, OPT = ("✓", "✗", "·") if _supports_unicode() else ("+", "x", "-")


def _width() -> int:
    return max(60, min(110, shutil.get_terminal_size((100, 20)).columns))


def _bar(done: int, total: int, width: int = 28) -> str:
    total = max(total, 1)
    filled = int(round(width * done / total))
    full, empty = ("█", "░") if _supports_unicode() else ("#", ".")
    return full * filled + empty * (width - filled) + f" {done}/{total}"


def status(d: dict[str, Any]) -> str:
    lines = [d["title"], "=" * min(len(d["title"]), _width()), f"id      {d['project_id']}", f"folder  {d['path']}", ""]
    phase = d["phase"]
    steps = []
    for p in PHASES:
        mark = "[" + ("•" if _supports_unicode() else "*") + "]" if p == phase else ("[x]" if PHASES.index(p) < PHASES.index(phase) else "[ ]")
        steps.append(f"{mark} {p.replace('_', ' ')}")
    lines.append("  ".join(steps))
    blocks = d["blocks"]
    written = blocks["total"] - blocks.get("planned", 0)
    lines += ["", f"blocks  {_bar(written, blocks['total'])}   verified {blocks.get('verified', 0)}, "
                  f"needs review {blocks.get('needs_review', 0)}",
              f"words   {d['words']['written']:,} written of ~{d['words']['planned']:,} planned"]
    if d.get("claims"):
        c = d["claims"]
        lines.append("claims  " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(c.items())))
    if d.get("sources"):
        lines.append("sources " + ", ".join(f"{k} {v}" for k, v in sorted(d["sources"].items())))
    if d.get("figures"):
        lines.append("figures " + ", ".join(f"{k} {v}" for k, v in sorted(d["figures"].items())))
    issues = d.get("open_issues") or {}
    lines.append("issues  " + (", ".join(f"{k} {v}" for k, v in issues.items()) if issues else "none open"))
    host = (d.get("tasks") or {}).get("host", {})
    if host:
        lines.append(f"tasks   pending {host.get('pending', 0)}, leased {host.get('leased', 0)}, "
                     f"done {host.get('done', 0)}, failed {host.get('failed', 0)}")
    lb = d.get("last_build")
    if lb:
        lines.append(f"build   {lb.get('status')} at {lb.get('finished_at') or lb.get('started_at')}")
    nxt = {"research": "scholarforge next <id>  (or let your AI client continue)",
           "ready_to_build": "scholarforge build <id>", "built": "open exports/ or rebuild after edits"}
    lines += ["", "next: " + nxt.get(phase, "continue the task loop (next_task / submit_task)")]
    return "\n".join(lines)


def doctor(d: dict[str, Any]) -> str:
    lines = [f"ScholarForge {d['scholarforge']} on {d['platform']}", ""]
    for c in d["checks"]:
        mark = OK if c["ok"] else (BAD if c["required"] else OPT)
        line = f" {mark} {c['name']:<22} {c['detail'][:70]}"
        if not c["ok"] and c.get("fix"):
            line += f"\n     -> {c['fix']}"
        lines.append(line)
    lines += ["", "ready" if d["ok"] else "required components missing: " + ", ".join(d["required_failures"])]
    return "\n".join(lines)


def projects(d: dict[str, Any]) -> str:
    rows = d["projects"]
    if not rows:
        return "no projects yet — scholarforge new --request \"...\""
    w = max(len(r["title"] or "") for r in rows)
    return "\n".join(f"{r['id']}  {(r['title'] or '').ljust(w)}  {r['path']}{'' if r['exists'] else '  [missing]'}"
                     for r in rows)


def next_task(d: dict[str, Any]) -> str:
    if d["status"] != "task":
        extra = d.get("message") or ""
        if d.get("blocked"):
            extra += "\n" + "\n".join(f"  - {b['title']}: {b['reason']}" for b in d["blocked"])
        return f"{d['status']}  ({d.get('phase', '')}) {extra}".rstrip()
    t = d["task"]
    return (f"task {t['task_id']}  [{t['kind']}]  attempt {t['attempt']}/{t['max_attempts']}\n{t['title']}\n\n"
            f"{t['instructions'][:1500]}\n\nlease token: {t['lease_token']}\n"
            "Submit with: scholarforge submit <project> <task_id> --lease <token> --result result.json "
            "(use --json to see the full context and output schema)")


def build(d: dict[str, Any]) -> str:
    lines = [f"build {d['status']} in {d['seconds']} s"]
    for fmt, res in d["results"].items():
        if not isinstance(res, dict) or fmt == "assembly":
            continue
        mark = OK if res.get("ok") else BAD
        lines.append(f" {mark} {fmt:<8} {res.get('path') or ''}")
        if res.get("error"):
            lines.append("     " + res["error"].splitlines()[-1][:160])
    for name, rep in d["preflight"].items():
        reps = rep.items() if "ok" not in rep else [("", rep)]
        for sub, r in reps:
            mark = OK if r.get("ok") else BAD
            label = f"{name}/{sub}" if sub else name
            lines.append(f" {mark} preflight {label}" + (f" — {r['pdfx']}" if r.get("pdfx") else ""))
            for e in r.get("errors", [])[:3]:
                lines.append(f"     {e}")
    if d.get("verification_report"):
        lines.append(f"verification report: {d['verification_report']}")
    return "\n".join(lines)


RENDERERS = {"status": status, "doctor": doctor, "list": projects, "next": next_task, "build": build}


def render(command: str, result: Any) -> str:
    fn = RENDERERS.get(command)
    if fn is not None and isinstance(result, dict):
        try:
            return fn(result)
        except (KeyError, TypeError):
            pass
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
