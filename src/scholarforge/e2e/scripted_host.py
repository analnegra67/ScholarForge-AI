"""A deterministic stand-in for the host AI, answering tasks from fixture files.

It follows the same protocol as Claude Code or Codex would: it reads the task's
instructions and context and submits a JSON result. Where the real host would
reason (e.g. judging whether an abstract supports a claim), the scripted host
applies simple, transparent rules over the *real* retrieved data.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from scholarforge.util.text import normalize_for_match, sentences, slugify

FIXTURES = "scholarforge.e2e.fixtures.deep_learning"

# keywords used to pick an evidence sentence from a retrieved abstract (per citekey)
EVIDENCE_KEYWORDS = {
    "rumelhart1986": ["hidden", "internal", "features of the task"],
    "cybenko1989": ["approximate", "superpositions"],
    "hornik1991": ["universal approximators", "approximators"],
    "baydin2018": ["more general than backpropagation", "backpropagation", "derivatives"],
    "griewank2008": ["reverse"],
    "robbins1951": ["stochastic approximation", "approximation"],
    "bottou2018": ["stochastic gradient"],
}

REVIEWS = {
    "optimization": [
        {"severity": "major", "category": "missing_content",
         "description": "The convergence theorem is stated without any proof; a graduate textbook should at least "
                        "give a proof sketch with the key telescoping step.",
         "suggestion": "Add a proof sketch combining the descent lemma, the convexity inequality and a telescoping sum."},
    ],
}


class ScriptedHost:
    def __init__(self) -> None:
        self.root = resources.files(FIXTURES)
        self.answered: list[dict[str, Any]] = []

    def _yaml(self, *parts: str) -> Any:
        node = self.root
        for p in parts:
            node = node / p
        return yaml.safe_load(node.read_text(encoding="utf-8"))

    def _text(self, *parts: str) -> str:
        node = self.root
        for p in parts:
            node = node / p
        return node.read_text(encoding="utf-8")

    def _exists(self, *parts: str) -> bool:
        node = self.root
        for p in parts:
            node = node / p
        return node.is_file()

    # ------------------------------------------------------------------------------------ api
    def answer(self, task: dict[str, Any]) -> dict[str, Any]:
        kind = task["kind"]
        handler = getattr(self, "_" + kind.replace(".", "_"), None)
        if handler is None:
            raise NotImplementedError(f"scripted host cannot answer task kind {kind}")
        result = handler(task)
        self.answered.append({"kind": kind, "task_id": task["task_id"]})
        return result

    # --------------------------------------------------------------------------------- tasks
    def _research_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        plan = self._yaml("research_plan.yaml")
        return plan

    def _sources_review(self, task: dict[str, Any]) -> dict[str, Any]:
        fixes = []
        for src in task["context"]["sources"]:
            if src["citekey"] == "silver2016" and src["status"] == "mismatch":
                fixes.append({"source_id": src["source_id"], "action": "correct", "corrected": {
                    "title": "Mastering the game of Go with deep neural networks and tree search",
                    "authors": ["David Silver", "Aja Huang", "Chris J. Maddison"], "year": 2016,
                    "doi": "10.1038/nature16961", "venue": "Nature", "kind": "article",
                    "relevance": "corrected identifier after verification"}})
            else:
                fixes.append({"source_id": src["source_id"], "action": "drop"})
        return {"fixes": fixes}

    def _architecture_outline(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._yaml("outline.yaml")

    def _block_dir(self, task: dict[str, Any]) -> str:
        title = task["context"]["position"]["block"]["title"]
        return slugify(title, 60)

    def _draft(self, task: dict[str, Any], revision: bool) -> dict[str, Any]:
        d = self._block_dir(task)
        meta = self._yaml("blocks", d, "meta.yaml")
        md_name = "block.rev1.md" if revision and self._exists("blocks", d, "block.rev1.md") else "block.md"
        result: dict[str, Any] = {
            "markdown": self._text("blocks", d, md_name),
            "summary": meta["summary"],
            "terms": meta.get("terms", []),
            "notation": meta.get("notation", []),
            "claims": list(meta.get("claims", [])),
            "math_checks": list(meta.get("math_checks", [])),
            "figures": [],
        }
        for fig in meta.get("figures", []):
            spec = {k: v for k, v in fig.items() if k != "code_file"}
            if "code_file" in fig:
                spec["code"] = self._text("blocks", d, fig["code_file"])
            result["figures"].append(spec)
        if revision and self._exists("blocks", d, "meta.rev1.yaml"):
            rev = self._yaml("blocks", d, "meta.rev1.yaml")
            result["changes_summary"] = rev.get("changes_summary")
            result["math_checks"] += rev.get("extra_math_checks", [])
            result["claims"] += rev.get("extra_claims", [])
        elif revision:
            result["changes_summary"] = "No substantive change needed; resubmitted unchanged."
        # citations: the fixtures use the citekeys proposed in the research plan; map them to the keys the
        # registry actually assigned (shown in context.sources) in case they differ
        keys = {s["citekey"] for s in task["context"].get("sources", [])}
        missing = sorted(set(re.findall(r"@([a-z]+\d{4}[a-z]*)", result["markdown"])) - keys)
        if missing:
            result.setdefault("_unresolved_citekeys", missing)
        return result

    def _draft_block(self, task: dict[str, Any]) -> dict[str, Any]:
        r = self._draft(task, revision=False)
        r.pop("_unresolved_citekeys", None)
        return r

    def _revise_block(self, task: dict[str, Any]) -> dict[str, Any]:
        r = self._draft(task, revision=True)
        r.pop("_unresolved_citekeys", None)
        return r

    def _claims_support(self, task: dict[str, Any]) -> dict[str, Any]:
        sources = {s["citekey"]: s for s in task["context"]["sources"]}
        out = []
        for claim in task["context"]["claims"]:
            for key in claim["citations"]:
                abstract = (sources.get(key) or {}).get("abstract")
                quote = None
                if abstract:
                    for kw in EVIDENCE_KEYWORDS.get(key, []):
                        for sent in _abstract_sentences(abstract):
                            if kw in sent.lower():
                                quote = sent
                                break
                        if quote:
                            break
                if quote:
                    out.append({"claim_id": claim["claim_id"], "citekey": key, "verdict": "supports",
                                "evidence_quote": quote, "notes": "evidence sentence taken from the abstract"})
                else:
                    out.append({"claim_id": claim["claim_id"], "citekey": key, "verdict": "cannot_determine",
                                "notes": "the retrieved abstract does not state this claim" if abstract
                                else "no abstract available"})
        return {"assessments": out}

    def _review_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        title = task["context"]["chapter"]["title"]
        findings = []
        for f in REVIEWS.get(slugify(title), []):
            block_id = task["context"]["blocks"][0]["block_id"]
            findings.append({"block_id": block_id, **f})
        return {"findings": findings,
                "overall_assessment": f"Chapter '{title}' reviewed by the scripted referee "
                                      f"({len(findings)} finding(s))."}

    def _figure_review(self, task: dict[str, Any]) -> dict[str, Any]:
        ctx = task["context"]
        return {"label": ctx["label"], "verdict": "ok", "problems": []}

    def _figure_fix(self, task: dict[str, Any]) -> dict[str, Any]:
        ctx = task["context"]
        return {"label": ctx["label"], "code": ctx.get("code") or ""}

    def _consistency_recheck(self, task: dict[str, Any]) -> dict[str, Any]:
        return {"items": [{"block_id": b["block_id"], "action": "no_change", "note": "definition change does not "
                                                                                     "affect this block"}
                          for b in task["context"]["blocks"]]}


def _abstract_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    return [p.strip() for p in parts if len(p.split()) >= 6]


def fixture_dir() -> Path:
    return Path(str(resources.files(FIXTURES)))
