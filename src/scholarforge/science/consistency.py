"""Cross-chapter consistency: terminology, notation, cross-references, dependencies.

* Terms: one registry entry per normalised term; silent redefinitions are refused
  (warning) and deliberate ones (``redefine: true``) are versioned and trigger a
  re-check of every block that uses the term.
* Notation: a symbol with two different meanings in one chapter is an error; across
  chapters it is a warning (overloading must be explicit).
* Cross-references: every ``@label`` must resolve; theorem/definition dependencies
  must be acyclic; proofs that rely on later results are flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from scholarforge.models.enums import NodeKind, Severity
from scholarforge.models.results import NotationDef, TermDef
from scholarforge.science.markdown import analyze
from scholarforge.storage.repo import ProjectDB, normalize_symbol
from scholarforge.util.text import normalize_for_match, title_similarity

DEPENDENCY_KINDS = ("thm", "lem", "prp", "cor", "def", "cnj")


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    details: dict[str, Any]


def _definitions_agree(a: str, b: str) -> float:
    return title_similarity(a, b)


class ConsistencyEngine:
    def __init__(self, pdb: ProjectDB):
        self.pdb = pdb

    # --------------------------------------------------------------------------------- terms
    def check_terms(self, node_id: str, terms: list[TermDef]) -> tuple[list[Finding], list[TermDef], list[TermDef]]:
        """Return (findings, new_terms, redefinitions)."""
        findings: list[Finding] = []
        new: list[TermDef] = []
        redefs: list[TermDef] = []
        for t in terms:
            existing = self.pdb.terms.get(t.term) or self._by_alias(t.term)
            if existing is None:
                new.append(t)
                continue
            if existing.defined_in == node_id:
                if normalize_for_match(existing.definition) != normalize_for_match(t.definition):
                    redefs.append(t)  # the defining block itself is being revised
                continue
            sim = _definitions_agree(existing.definition, t.definition)
            if t.redefine:
                if not t.justification:
                    findings.append(Finding("error", "REDEFINITION_WITHOUT_JUSTIFICATION",
                                            f"term '{t.term}' is redefined without a justification",
                                            {"term": t.term}))
                else:
                    redefs.append(t)
            elif sim < 0.5:
                findings.append(Finding(
                    "warning", "TERM_DEFINITION_DIFFERS",
                    f"term '{t.term}' is already defined differently; the registry keeps the original definition "
                    f"(set redefine: true with a justification to change it)",
                    {"term": t.term, "registered": existing.definition, "submitted": t.definition,
                     "similarity": round(sim, 2)}))
        return findings, new, redefs

    def _by_alias(self, term: str):
        norm = normalize_for_match(term)
        for t in self.pdb.terms.list():
            if norm in (normalize_for_match(a) for a in t.aliases):
                return t
        return None

    def apply_terms(self, node_id: str, new: list[TermDef], redefs: list[TermDef]) -> list[str]:
        """Register terms; return ids of terms whose definition changed (for dependent re-checks)."""
        changed: list[str] = []
        for t in new:
            self.pdb.terms.add(t.term, t.definition, node_id, t.aliases)
        for t in redefs:
            existing = self.pdb.terms.get(t.term) or self._by_alias(t.term)
            if existing is None:
                continue
            self.pdb.terms.redefine(existing.id, t.definition, node_id)
            changed.append(existing.id)
        return changed

    def update_usage(self, node_id: str, markdown: str) -> list[str]:
        text = normalize_for_match(re.sub(r"\$[^$]*\$", " ", markdown))
        padded = f" {text} "
        used = []
        for t in self.pdb.terms.list():
            variants = [t.term, *t.aliases]
            if any(f" {normalize_for_match(v)} " in padded for v in variants if v):
                used.append(t.id)
        self.pdb.terms.set_usage(node_id, used)
        return used

    def dependents_of_term(self, term_id: str) -> list[str]:
        term = self.pdb.terms.by_id(term_id)
        return [n for n in self.pdb.terms.users_of(term_id) if n != term.defined_in]

    # ------------------------------------------------------------------------------ notation
    def check_notation(self, node_id: str, notation: list[NotationDef]) -> list[Finding]:
        findings: list[Finding] = []
        chapter = self.pdb.nodes.ancestor_of_kind(node_id, NodeKind.CHAPTER)
        seen_local: dict[str, str] = {}
        for n in notation:
            key = normalize_symbol(n.symbol)
            if key in seen_local and title_similarity(seen_local[key], n.meaning) < 0.5:
                findings.append(Finding("error", "NOTATION_CONFLICT",
                                        f"symbol {n.symbol} is given two meanings in the same block",
                                        {"symbol": n.symbol}))
            seen_local[key] = n.meaning
            for ex in self.pdb.notation.by_symbol(n.symbol):
                if ex.defined_in == node_id:
                    continue
                if title_similarity(ex.meaning, n.meaning) >= 0.5:
                    continue
                ex_ch = self.pdb.nodes.ancestor_of_kind(ex.defined_in, NodeKind.CHAPTER) if ex.defined_in else None
                same_chapter = chapter is not None and ex_ch is not None and ex_ch.id == chapter.id
                if same_chapter:
                    findings.append(Finding("error", "NOTATION_CONFLICT",
                                            f"symbol {n.symbol} already means '{ex.meaning}' in this chapter",
                                            {"symbol": n.symbol, "existing": ex.meaning, "new": n.meaning}))
                elif ex.scope == "global" and n.scope == "global":
                    findings.append(Finding("warning", "NOTATION_OVERLOADED",
                                            f"symbol {n.symbol} already means '{ex.meaning}' elsewhere; declare "
                                            "scope 'chapter' and say so in the text if the overloading is intended",
                                            {"symbol": n.symbol, "existing": ex.meaning, "new": n.meaning}))
        return findings

    def apply_notation(self, node_id: str, notation: list[NotationDef]) -> None:
        self.pdb.notation.delete_for_node(node_id)
        for n in notation:
            self.pdb.notation.add(n.symbol, n.meaning, n.scope, node_id)

    # ---------------------------------------------------------------------- references / graph
    def reading_order(self) -> dict[str, int]:
        return {n.id: i for i, n in enumerate(self.pdb.nodes.walk())}

    def dependency_edges(self, texts: dict[str, str]) -> list[tuple[str, str, str]]:
        """Edges (from_label, to_label, node_id): results that rely on other results."""
        edges = []
        for node_id, md in texts.items():
            an = analyze(md)
            for label, body in an.label_blocks.items():
                if label.split("-", 1)[0] not in DEPENDENCY_KINDS:
                    continue
                for ref in set(re.findall(r"@((?:thm|lem|prp|cor|def|cnj)-[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?)", body)):
                    if ref != label:
                        edges.append((label, ref, node_id))
            # proofs following a theorem: refs inside the proof belong to the preceding result
            for m in re.finditer(r"\{#((?:thm|lem|prp|cor)-[A-Za-z0-9][A-Za-z0-9_-]*)[^}]*\}(.*?)(?=\{#(?:thm|lem|prp|cor)-|\Z)",
                                 md, flags=re.S):
                label, tail = m.group(1), m.group(2)
                proof = re.search(r":::\s*\{\.proof[^}]*\}(.*?)\n:::", tail, flags=re.S)
                if proof:
                    for ref in set(re.findall(r"@((?:thm|lem|prp|cor|def)-[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?)",
                                              proof.group(1))):
                        if ref != label:
                            edges.append((label, ref, node_id))
        return list(dict.fromkeys(edges))

    @staticmethod
    def find_cycles(edges: list[tuple[str, str, str]]) -> list[list[str]]:
        graph: dict[str, set[str]] = {}
        for a, b, _ in edges:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set())
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        cycles: list[list[str]] = []
        counter = [0]

        def strongconnect(v: str) -> None:
            index[v] = low[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on_stack.add(v)
            for w in graph[v]:
                if w not in index:
                    strongconnect(w)
                    low[v] = min(low[v], low[w])
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in graph[v]:
                    cycles.append(sorted(comp))

        for v in list(graph):
            if v not in index:
                strongconnect(v)
        return cycles

    def global_findings(self, texts: dict[str, str]) -> list[tuple[str | None, Finding]]:
        out: list[tuple[str | None, Finding]] = []
        labels = self.pdb.labels.all()
        order = self.reading_order()
        for node_id, label in self.pdb.labels.unresolved():
            out.append((node_id, Finding("error", "UNRESOLVED_REFERENCE",
                                         f"cross-reference @{label} does not resolve to any label in the book",
                                         {"label": label})))
        edges = self.dependency_edges(texts)
        for cycle in self.find_cycles(edges):
            out.append((None, Finding("error", "CIRCULAR_DEPENDENCY",
                                      "circular dependency between results: " + " -> ".join(cycle),
                                      {"cycle": cycle})))
        for a, b, node_id in edges:
            target = labels.get(b)
            if target and target.get("node_id") and order.get(target["node_id"], -1) > order.get(node_id, -1):
                out.append((node_id, Finding("warning", "FORWARD_DEPENDENCY",
                                             f"{a} relies on {b}, which appears later in the book", {"from": a, "to": b})))
        # notation overloaded globally (summary)
        by_symbol: dict[str, set[str]] = {}
        for n in self.pdb.notation.list():
            by_symbol.setdefault(n.normalized, set()).add(n.meaning)
        for sym, meanings in by_symbol.items():
            if len(meanings) > 1:
                distinct = []
                for m in meanings:
                    if all(title_similarity(m, d) < 0.5 for d in distinct):
                        distinct.append(m)
                if len(distinct) > 1:
                    out.append((None, Finding("info", "NOTATION_MULTIPLE_MEANINGS",
                                              f"symbol {sym} has {len(distinct)} meanings across the book",
                                              {"symbol": sym, "meanings": distinct})))
        return out

    def severity(self, f: Finding) -> Severity:
        return {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}.get(f.severity, Severity.INFO)
