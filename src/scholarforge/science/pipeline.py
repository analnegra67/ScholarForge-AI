"""Whole-book verification pass (``scholarforge verify`` / MCP ``run_checks``)."""

from __future__ import annotations

from typing import Any

from scholarforge.models.enums import CheckOutcome, IssueStatus, NodeStatus, Severity, SourceStatus
from scholarforge.science.consistency import ConsistencyEngine
from scholarforge.science.mathcheck import MathCheckRunner
from scholarforge.science.sources.manager import SourceManager


class VerificationPipeline:
    def __init__(self, project):
        self.project = project
        self.pdb = project.db

    def run_all(self, *, network: bool = True, math: bool = True) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if network and self.project.settings.network.enabled:
            summary["sources"] = SourceManager(self.project).verify_pending(budget_s=120)
        else:
            summary["sources"] = {"skipped": "offline"}
        if math:
            summary["math"] = self.rerun_math()
        summary["consistency"] = self.global_consistency()
        summary["claims"] = self.recompute_claims()
        summary["open_issues"] = self.pdb.issues.counts()
        self.pdb.events.log("verify.run", data={k: v for k, v in summary.items() if k != "consistency"})
        return summary

    def rerun_math(self) -> dict[str, int]:
        runner = MathCheckRunner.from_settings(self.project.settings)
        counts: dict[str, int] = {}
        for node in self.pdb.nodes.blocks():
            rows = self.pdb.assertions.for_node(node.id)
            if not rows:
                continue
            specs = [{k: v for k, v in r["spec"].items() if not k.startswith("_")} for r in rows]
            for res in runner.run(specs):
                outcome = CheckOutcome(res.outcome) if res.outcome in CheckOutcome._value2member_map_ else CheckOutcome.ERROR
                self.pdb.checks.add(subject_type="assertion", subject_id=f"{node.id}:{res.id}",
                                    check_type=f"math.{res.kind}", method=res.method,
                                    independent=res.method in ("symbolic", "formal", "numeric", "dimensional"),
                                    outcome=outcome, details={"message": res.message, **res.details})
                self.pdb.assertions.set_outcome(node.id, res.id, res.outcome)
                key = f"{res.outcome}:{res.method}"
                counts[key] = counts.get(key, 0) + 1
                if res.outcome == "fail":
                    self.pdb.issues.add(severity=Severity.ERROR, category="math", code="MATH_CHECK_FAILED",
                                        message=f"math check '{res.id}' failed on re-run: {res.message}",
                                        node_id=node.id)
        return counts

    def global_consistency(self) -> dict[str, Any]:
        texts = {b.id: self.project.content.read_block(b.id) or "" for b in self.pdb.nodes.blocks()
                 if b.status != NodeStatus.PLANNED}
        engine = ConsistencyEngine(self.pdb)
        for issue in self.pdb.issues.list(category="consistency"):
            self.pdb.issues.set_status([issue.id], IssueStatus.RESOLVED)
        findings = engine.global_findings(texts)
        out: dict[str, int] = {}
        for node_id, f in findings:
            self.pdb.issues.add(severity=engine.severity(f), category="consistency", code=f.code, message=f.message,
                                node_id=node_id, details=f.details)
            out[f.code] = out.get(f.code, 0) + 1
        return out

    def recompute_claims(self) -> dict[str, int]:
        from scholarforge.core.workflow import Coordinator
        from scholarforge.workflow.drafting import update_claim_statuses

        wf = Coordinator(self.project)
        totals: dict[str, int] = {}
        for node in self.pdb.nodes.blocks():
            if node.status == NodeStatus.PLANNED:
                continue
            for k, v in update_claim_statuses(wf, node).items():
                totals[k] = totals.get(k, 0) + v
        return totals

    def unusable_sources(self) -> list[dict[str, Any]]:
        bad = (SourceStatus.NOT_FOUND, SourceStatus.MISMATCH, SourceStatus.RETRACTED, SourceStatus.UNVERIFIABLE,
               SourceStatus.PENDING)
        return [{"citekey": s.citekey, "status": s.status.value, "reason": (s.verification or {}).get("reason")}
                for s in self.pdb.sources.list(list(bad))]
