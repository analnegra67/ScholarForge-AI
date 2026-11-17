"""Verification report: what was checked, how, with what result — and what was not checked."""

from __future__ import annotations

from typing import Any

from scholarforge.models.enums import ClaimStatus, NodeKind, NodeStatus, Severity, SourceStatus
from scholarforge.util.atomic import atomic_write_json, atomic_write_text
from scholarforge.util.timeutil import now_iso
from scholarforge.version import __version__

NOT_VERIFIED_BY_DESIGN = [
    "Prose arguments and proofs written in natural language are not machine-checked unless a math check or a "
    "Lean proof is attached to them.",
    "Claim-support assessments are judgements of the host language model; only evidence quotes that match the "
    "retrieved source text count as independent evidence.",
    "Source verification confirms that a cited work exists and matches its metadata; it does not confirm that "
    "every detail of the work is correct.",
    "Numerical agreement of formulas at random points is evidence, not proof.",
    "Figure correctness is reviewed by the host model unless the figure is generated from code you can inspect "
    "(sources are stored under figures/).",
    "Language-model review of its own text is never counted as independent verification.",
]


def build_report(project) -> dict[str, Any]:
    pdb = project.db
    chapters = {}
    for ch in pdb.nodes.chapters():
        for d in pdb.nodes.descendants(ch.id):
            chapters[d.id] = ch.title
    claims = pdb.claims.all()
    claim_rows = []
    for c in claims:
        claim_rows.append({"chapter": chapters.get(c.node_id, "?"), "kind": c.kind, "status": c.status.value,
                           "reason": c.status_reason, "text": c.text[:300], "citations": c.citations,
                           "label": c.label})
    counts: dict[str, int] = {s.value: 0 for s in ClaimStatus}
    for c in claims:
        counts[c.status.value] += 1
    sources = pdb.sources.list()
    src_rows = [{"citekey": s.citekey, "status": s.status.value, "title": s.title, "year": s.year, "doi": s.doi,
                 "arxiv": s.arxiv_id, "isbn": s.isbn, "reason": (s.verification or {}).get("reason")} for s in sources]
    math_rows = []
    for chk in pdb.checks.all():
        if chk.check_type.startswith("math."):
            node_id, _, local = chk.subject_id.partition(":")
            math_rows.append({"chapter": chapters.get(node_id, "?"), "id": local, "kind": chk.check_type[5:],
                              "outcome": chk.outcome.value, "method": chk.method,
                              "message": (chk.details or {}).get("message", "")})
    latest_math: dict[tuple[str, str], dict] = {}
    for row in math_rows:
        latest_math[(row["chapter"], row["id"])] = row
    figures = [{"label": f.label, "status": f.status.value, "renderer": f.renderer, "mode": f.mode,
                "ai_generated": bool((f.provenance or {}).get("ai_generated")),
                "host_review": (f.qc or {}).get("host_review"), "caption": f.caption[:200]} for f in pdb.figures.list()]
    issues = pdb.issues.list(status=None, min_severity=Severity.WARNING)
    open_issues = [i for i in issues if i.status.value in ("open", "expert_review")]
    blocks = pdb.nodes.blocks()
    return {
        "generated_at": now_iso(), "engine_version": __version__, "project_id": project.id,
        "title": pdb.meta.get("book_title") or project.config.title,
        "blocks": {"total": len(blocks), **{s.value: sum(1 for b in blocks if b.status == s) for s in NodeStatus}},
        "needs_expert_review_blocks": [b.title for b in blocks if b.status == NodeStatus.NEEDS_REVIEW],
        "claims": {"counts": counts, "items": claim_rows},
        "sources": {"counts": {s.value: sum(1 for x in sources if x.status == s) for s in SourceStatus},
                    "items": src_rows},
        "math_checks": list(latest_math.values()),
        "figures": figures,
        "open_issues": [{"severity": i.severity.value, "category": i.category, "code": i.code, "message": i.message,
                         "status": i.status.value, "chapter": chapters.get(i.node_id or "", None)} for i in open_issues],
        "not_verified_by_design": NOT_VERIFIED_BY_DESIGN,
    }


def report_markdown(r: dict[str, Any]) -> str:
    lines = [f"# Verification report — {r['title']}", "",
             f"Generated {r['generated_at']} by ScholarForge {r['engine_version']}.", "",
             "> ScholarForge does not guarantee that every statement is true. This report lists exactly what was "
             "verified, by which method, and what remains unverified or needs expert review.", "",
             "## Claims", "", "| Status | Count |", "|:--|--:|"]
    for k, v in r["claims"]["counts"].items():
        lines.append(f"| {k.replace('_', ' ')} | {v} |")
    for status in ("requires_expert_review", "contested", "unverified"):
        items = [c for c in r["claims"]["items"] if c["status"] == status]
        if items:
            lines += ["", f"### {status.replace('_', ' ').capitalize()} ({len(items)})", ""]
            for c in items[:200]:
                lines.append(f"- [{c['chapter']}] *{c['kind']}*: {c['text']} — {c['reason']}")
    lines += ["", "## Sources", "", "| Status | Count |", "|:--|--:|"]
    for k, v in r["sources"]["counts"].items():
        if v:
            lines.append(f"| {k.replace('_', ' ')} | {v} |")
    bad = [s for s in r["sources"]["items"] if s["status"] not in ("verified",)]
    if bad:
        lines += ["", "Sources that are not citable:", ""]
        lines += [f"- `{s['citekey']}` ({s['status']}): {s['title']} — {s['reason']}" for s in bad]
    lines += ["", "## Mathematical checks", "", "| Chapter | Check | Kind | Outcome | Method |",
              "|:--|:--|:--|:--|:--|"]
    for m in r["math_checks"]:
        lines.append(f"| {m['chapter']} | {m['id']} | {m['kind']} | {m['outcome']} | {m['method']} |")
    lines += ["", "## Figures", ""]
    for f in r["figures"]:
        review = (f.get("host_review") or {}).get("verdict", "not reviewed")
        lines.append(f"- {f['label']}: {f['status']} ({f['renderer']}{', AI-generated' if f['ai_generated'] else ''}); "
                     f"review: {review}")
    if r["needs_expert_review_blocks"]:
        lines += ["", "## Blocks that need expert review", ""] + [f"- {t}" for t in r["needs_expert_review_blocks"]]
    if r["open_issues"]:
        lines += ["", f"## Open issues ({len(r['open_issues'])})", ""]
        for i in r["open_issues"][:300]:
            lines.append(f"- **{i['severity']}** [{i['category']}] {i['chapter'] or 'book'}: {i['message']}")
    lines += ["", "## Not verified by design", ""] + [f"- {x}" for x in r["not_verified_by_design"]]
    return "\n".join(lines) + "\n"


def write_verification_report(project) -> dict[str, Any]:
    r = build_report(project)
    json_path = atomic_write_json(project.reports_dir / "verification-report.json", r)
    md_path = atomic_write_text(project.reports_dir / "verification-report.md", report_markdown(r))
    return {"json": str(json_path), "markdown": str(md_path), "claims": r["claims"]["counts"],
            "sources": {k: v for k, v in r["sources"]["counts"].items() if v}, "open_issues": len(r["open_issues"])}
