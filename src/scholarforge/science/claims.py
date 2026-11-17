"""Claim status: what evidence actually supports each scientific statement.

Status levels (from the strongest):

``verified``
    an independent machine check proved it (exact symbolic computation, a formal proof).
``supported``
    independent evidence agrees but is not a proof: numerical agreement, or a verified
    source whose retrieved text contains the quoted evidence and was judged supportive.
``contested``
    a source was judged not to support it, or the statement is a hypothesis / speculative
    research programme (labelled as such in the text).
``unverified``
    no independent evidence yet (e.g. support asserted by the host model only, no citation,
    unresolvable citation, proof written in prose but not machine-checked).
``requires_expert_review``
    a machine check failed, a cited work is retracted, or the domain marks the claim kind
    as high-stakes (e.g. clinical statements).

A second opinion of the same language model never upgrades a claim.
"""

from __future__ import annotations

from typing import Any

from scholarforge.models.entities import Claim, Source
from scholarforge.models.enums import ClaimStatus, SourceStatus
from scholarforge.profiles.registry import Profile

RESULT_KINDS = ("theorem", "lemma", "proposition", "corollary")


def compute_status(claim: Claim, *, math_results: dict[str, dict[str, Any]], sources: dict[str, Source],
                   assessments: list[dict[str, Any]], profile: Profile, has_proof_text: bool) -> tuple[ClaimStatus, str]:
    linked = [math_results[c] for c in claim.check_ids if c in math_results]
    failed = [r for r in linked if r.get("outcome") == "fail"]
    if failed:
        return ClaimStatus.REQUIRES_EXPERT_REVIEW, f"machine check '{failed[0].get('id')}' failed: {failed[0].get('message', '')}"
    if claim.kind in profile.claims.high_stakes_kinds:
        return ClaimStatus.REQUIRES_EXPERT_REVIEW, f"'{claim.kind}' statements always require review by a qualified expert"
    cited = [sources.get(k) for k in claim.citations]
    if any(s is not None and s.status == SourceStatus.RETRACTED for s in cited):
        return ClaimStatus.REQUIRES_EXPERT_REVIEW, "cites a retracted work"

    proofs = [r for r in linked if r.get("outcome") == "pass" and r.get("method") in ("symbolic", "formal")
              and r.get("scope", "general") == "general"]
    numeric = [r for r in linked if r.get("outcome") == "pass" and (r.get("method") in ("numeric", "dimensional")
                                                                    or r.get("scope") == "instance")]
    if linked and len(proofs) == len(linked):
        how = "formal proof (Lean)" if any(r.get("method") == "formal" for r in proofs) else "exact symbolic computation (SymPy)"
        return ClaimStatus.VERIFIED, f"independently verified by {how}"

    if claim.speculative or claim.kind in ("hypothesis", "conjecture"):
        return ClaimStatus.CONTESTED, "speculative / open question, labelled as such"

    negative = [a for a in assessments if a.get("verdict") in ("does_not_support", "contradicts")]
    if negative:
        return ClaimStatus.CONTESTED, f"source {negative[0]['citekey']} judged not to support the claim"

    if claim.citations:
        missing = [k for k, s in zip(claim.citations, cited) if s is None]
        if missing:
            return ClaimStatus.UNVERIFIED, f"unknown citation key(s): {', '.join(missing)}"
        bad = [s for s in cited if s.status != SourceStatus.VERIFIED]
        if bad:
            return ClaimStatus.UNVERIFIED, f"citation {bad[0].citekey} is {bad[0].status.value}"
        strong = [a for a in assessments if a.get("verdict") in ("supports", "partially_supports") and a.get("quote_verified")]
        if strong:
            partial = all(a["verdict"] == "partially_supports" for a in strong)
            return ClaimStatus.SUPPORTED, (
                f"verified source {strong[0]['citekey']} contains the quoted evidence"
                + (" (partial support)" if partial else "")
            )
        if numeric:
            return ClaimStatus.SUPPORTED, "special-case or numerical/dimensional checks agree; sources exist"
        asserted = [a for a in assessments if a.get("verdict") in ("supports", "partially_supports")]
        if asserted:
            return ClaimStatus.UNVERIFIED, ("cited sources exist; support was asserted by the host model without "
                                           "verifiable quoted evidence (not independent)")
        if assessments:
            no_text = all(not a.get("abstract_available") for a in assessments)
            return ClaimStatus.UNVERIFIED, ("cited sources exist; no retrievable abstract or text to confirm the "
                                           "claim" if no_text else
                                           "cited sources exist; the retrieved abstract does not confirm the claim")
        return ClaimStatus.UNVERIFIED, "cited sources exist; support not yet assessed"

    if numeric:
        return ClaimStatus.SUPPORTED, "evidence from special-case, numerical or dimensional checks (not a proof)"
    if claim.kind in RESULT_KINDS and has_proof_text:
        return ClaimStatus.UNVERIFIED, "proof given in the text; not machine-checked"
    if claim.kind in profile.claims.require_citation_kinds:
        return ClaimStatus.UNVERIFIED, f"'{claim.kind}' claims need a citation"
    return ClaimStatus.UNVERIFIED, "no independent evidence"


def quote_in_text(quote: str | None, text: str | None, threshold: float = 0.9) -> bool:
    """Is ``quote`` (approximately verbatim) contained in ``text``? Robust to case, spacing, punctuation."""
    if not quote or not text:
        return False
    from scholarforge.util.text import normalize_for_match

    q = normalize_for_match(quote)
    t = normalize_for_match(text)
    if len(q) < 20:
        return False
    if q in t:
        return True
    q_words = q.split()
    t_words = t.split()
    n = len(q_words)
    if n == 0 or len(t_words) < n:
        return False
    qset = set(q_words)
    best = 0.0
    for i in range(0, len(t_words) - n + 1):
        window = t_words[i: i + n]
        overlap = sum(1 for a, b in zip(window, q_words) if a == b) / n
        if overlap > best:
            best = overlap
        if best >= threshold:
            return True
    return len(qset & set(t_words)) / len(qset) >= 0.98 and best >= threshold - 0.1
