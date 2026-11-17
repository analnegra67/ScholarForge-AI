"""Optional Lean 4 / Mathlib integration for formalisable statements.

Lean is used only when installed. A check passes only if Lean elaborates the file
with no errors and the source contains no ``sorry``/``admit``/new axioms — a proof
with gaps is reported as *inconclusive*, never as verified.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

FORBIDDEN = re.compile(r"\b(sorry|admit)\b|^\s*axiom\s", re.M)


def find_lean(lean_path: str | None = None, lake_path: str | None = None, mathlib_project: str | None = None) -> dict[str, Any]:
    lean = lean_path or shutil.which("lean")
    lake = lake_path or shutil.which("lake")
    info: dict[str, Any] = {"lean": lean, "lake": lake, "mathlib_project": mathlib_project, "available": bool(lean)}
    if mathlib_project and not (Path(mathlib_project) / "lakefile.lean").exists() and not (
        Path(mathlib_project) / "lakefile.toml"
    ).exists():
        info["mathlib_project_error"] = "no lakefile found in mathlib_project"
    return info


def build_command(source_file: Path, lean: str | None, lake: str | None, mathlib_project: str | None) -> tuple[list[str], str | None]:
    if mathlib_project and lake:
        return [lake, "env", "lean", "--json", str(source_file)], mathlib_project
    if lean:
        return [lean, "--json", str(source_file)], None
    raise FileNotFoundError("Lean is not installed")


def parse_output(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    messages = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            messages.append({"severity": msg.get("severity", "info"), "text": msg.get("data", "")[:2000],
                             "pos": msg.get("pos")})
        except json.JSONDecodeError:
            sev = "error" if "error" in line.lower() else "info"
            messages.append({"severity": sev, "text": line[:2000]})
    errors = [m for m in messages if m["severity"] == "error"]
    if stderr.strip() and returncode != 0 and not errors:
        errors.append({"severity": "error", "text": stderr.strip()[:2000]})
    return {"returncode": returncode, "messages": messages, "errors": errors}


def check_lean(code: str, *, lean: str | None, lake: str | None, mathlib_project: str | None,
               timeout_s: int = 300) -> dict[str, Any]:
    if FORBIDDEN.search(code):
        return {"outcome": "inconclusive", "method": "formal",
                "message": "Lean source contains sorry/admit/axiom: not a complete formal proof", "details": {}}
    info = find_lean(lean, lake, mathlib_project)
    if not info["available"]:
        return {"outcome": "skipped", "method": "formal", "message": "Lean 4 is not installed", "details": info}
    with tempfile.TemporaryDirectory(prefix="sf-lean-") as tmp:
        src = Path(tmp) / "Check.lean"
        src.write_text(code, encoding="utf-8")
        try:
            cmd, cwd = build_command(src, info["lean"], info["lake"], mathlib_project)
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"outcome": "inconclusive", "method": "formal", "message": f"Lean timed out after {timeout_s}s",
                    "details": {}}
        except OSError as exc:
            return {"outcome": "error", "method": "formal", "message": f"cannot run Lean: {exc}", "details": {}}
    parsed = parse_output(proc.stdout, proc.stderr, proc.returncode)
    if parsed["errors"] or proc.returncode != 0:
        return {"outcome": "fail", "method": "formal", "message": "Lean reported errors", "details": parsed}
    warns = [m for m in parsed["messages"] if m["severity"] == "warning" and "sorry" in m["text"]]
    if warns:
        return {"outcome": "inconclusive", "method": "formal", "message": "proof uses sorry", "details": parsed}
    return {"outcome": "pass", "method": "formal", "message": "Lean elaborated the proof without errors",
            "details": parsed}
