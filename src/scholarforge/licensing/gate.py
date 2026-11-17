"""Entitlement gate used by the adapters (CLI/MCP). The scientific core never calls this.

Policy: a single plan, "pro", unlocks every feature with no page/project limits.
When enforcement is on and the subscription is not active, *creating and advancing*
work is refused; opening, reading, building and exporting existing projects always
work — users never lose access to their own books.
"""

from __future__ import annotations

from typing import Any

from scholarforge.errors import EntitlementRequired

PLAN_FEATURES = {"pro": ["*"]}


def _enforcement() -> str:
    from scholarforge.settings import load_settings

    return load_settings().license.enforcement


def require_entitlement(plan: str = "pro") -> None:
    if _enforcement() != "on":
        return
    from scholarforge.licensing.client import LicenseClient

    status = LicenseClient().maybe_refresh()
    if status.usable and status.plan == plan:
        return
    raise EntitlementRequired(
        f"ScholarForge Pro subscription required ({status.state}: {status.message})",
        hint="subscribe ($20/month) and run `scholarforge license activate <KEY>`; existing projects stay readable "
             "and exportable",
    )


def license_summary() -> dict[str, Any]:
    from scholarforge.licensing.client import LicenseClient

    mode = _enforcement()
    try:
        st = LicenseClient().status()
        state = st.model_dump()
    except Exception as exc:  # misconfiguration must not break diagnostics
        state = {"state": "invalid", "message": str(exc)}
    return {"enforcement": mode, "plan": "ScholarForge Pro ($20/month, all features)", "status": state,
            "write_operations_allowed": mode != "on" or state.get("state") in ("active", "grace")}
