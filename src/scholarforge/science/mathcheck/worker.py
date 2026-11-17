"""Worker entry point: ``python -I -m scholarforge.science.mathcheck.worker``.

Protocol: one JSON object on stdin ``{"checks": [...], "samples": n, "tol": x}``;
one JSON object on stdout ``{"results": [...]}``. The parent enforces the timeout
and runs this process with a scrubbed environment.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8")
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({"fatal": f"bad request: {exc}"}))
        return 2
    from scholarforge.science.mathcheck.checks import run_check

    results = []
    for spec in request.get("checks", []):
        results.append(run_check(spec, int(request.get("samples", 24)), float(request.get("tol", 1e-8))))
    sys.stdout.write(json.dumps({"results": results}, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
