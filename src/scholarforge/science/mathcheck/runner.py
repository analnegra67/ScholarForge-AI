"""Parent-side orchestration of math checks in an isolated subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from scholarforge.logging_setup import get_logger
from scholarforge.science.mathcheck.lean import check_lean

log = get_logger("mathcheck")

_ENV_KEEP = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING",
             "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA")


@dataclass
class MathResult:
    id: str
    kind: str
    outcome: str  # pass | fail | inconclusive | error | skipped
    method: str  # symbolic | numeric | dimensional | formal | structural | none
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def independent_proof(self) -> bool:
        """True when the check is an exact proof independent of any language model."""
        return self.outcome == "pass" and self.method in ("symbolic", "formal")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "outcome": self.outcome, "method": self.method,
                "message": self.message, "details": self.details}


def scrubbed_env() -> dict[str, str]:
    """Environment for untrusted computations: no API keys, tokens or credentials."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    src_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    env["PYTHONPATH"] = src_root
    return env


class MathCheckRunner:
    def __init__(self, *, timeout_s: int = 30, samples: int = 24, tol: float = 1e-8,
                 lean: str | None = None, lake: str | None = None, mathlib_project: str | None = None):
        self.timeout_s = timeout_s
        self.samples = samples
        self.tol = tol
        self.lean, self.lake, self.mathlib_project = lean, lake, mathlib_project

    @classmethod
    def from_settings(cls, settings) -> "MathCheckRunner":
        return cls(timeout_s=settings.math.timeout_s, samples=settings.math.numeric_samples,
                   tol=settings.math.rel_tolerance, lean=settings.tools.lean, lake=settings.tools.lake,
                   mathlib_project=settings.tools.mathlib_project)

    def _spawn(self, specs: list[dict[str, Any]], timeout: float) -> list[dict[str, Any]] | None:
        payload = json.dumps({"checks": specs, "samples": self.samples, "tol": self.tol}).encode("utf-8")
        # -I (isolated mode) ignores PYTHONPATH, user site-packages and the current directory, so a planted
        # module next to the project cannot be imported; the package root is passed explicitly instead.
        cmd = [sys.executable, "-I", "-c",
               "import sys; sys.path.insert(0, sys.argv[1]); "
               "from scholarforge.science.mathcheck.worker import main; raise SystemExit(main())",
               scrubbed_env()["PYTHONPATH"]]
        try:
            proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout, env=scrubbed_env())
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0:
            log.warning("math worker exited with %s: %s", proc.returncode, proc.stderr.decode("utf-8", "replace")[-800:])
            return [{"id": s.get("id"), "kind": s.get("kind"), "outcome": "error", "method": "none",
                     "message": "math worker crashed", "details": {}} for s in specs]
        try:
            data = json.loads(proc.stdout.decode("utf-8"))
        except json.JSONDecodeError:
            return [{"id": s.get("id"), "kind": s.get("kind"), "outcome": "error", "method": "none",
                     "message": "math worker returned invalid output", "details": {}} for s in specs]
        return data.get("results", [])

    def run(self, specs: list[dict[str, Any]]) -> list[MathResult]:
        if not specs:
            return []
        results: dict[str, dict[str, Any]] = {}
        sympy_specs = [s for s in specs if s.get("kind") != "lean"]
        for spec in (s for s in specs if s.get("kind") == "lean"):
            res = check_lean(spec.get("code") or "", lean=self.lean, lake=self.lake,
                             mathlib_project=self.mathlib_project)
            results[spec["id"]] = {"id": spec["id"], "kind": "lean", **res}
        if sympy_specs:
            batch = self._spawn(sympy_specs, timeout=self.timeout_s * max(1, len(sympy_specs)) * 0.5 + 10)
            if batch is None:
                # isolate the slow check(s): run one by one with the per-check timeout
                for spec in sympy_specs:
                    single = self._spawn([spec], timeout=self.timeout_s + 10)
                    if single is None:
                        results[spec["id"]] = {"id": spec["id"], "kind": spec.get("kind"), "outcome": "inconclusive",
                                               "method": "none", "message": f"timed out after {self.timeout_s}s",
                                               "details": {}}
                    else:
                        results[spec["id"]] = single[0]
            else:
                for r in batch:
                    results[r["id"]] = r
        out = []
        for spec in specs:
            r = results.get(spec["id"]) or {"outcome": "error", "method": "none", "message": "no result", "details": {}}
            out.append(MathResult(id=spec["id"], kind=spec.get("kind", "?"), outcome=r.get("outcome", "error"),
                                  method=r.get("method", "none"), message=r.get("message", ""),
                                  details=r.get("details") or {}))
        return out
