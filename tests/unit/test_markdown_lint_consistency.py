from scholarforge.models.entities import Claim, Source
from scholarforge.models.enums import ClaimStatus, NodeKind, SourceStatus
from scholarforge.models.results import NotationDef, TermDef
from scholarforge.profiles import get_registry
from scholarforge.science.claims import compute_status, quote_in_text
from scholarforge.science.consistency import ConsistencyEngine
from scholarforge.science.lint import lint_block
from scholarforge.science.markdown import analyze, insert_index_entry

GOOD = """Some text with a citation [@rumelhart1986; @cybenko1989, p. 3] and narrative @hornik1991 work.

::: {#thm-chain name="Chain rule"}
If $g$ is differentiable then $(f\\circ g)' = f'(g)g'$, see @eq-one and @sec-intro.
:::

::: {.proof}
Trivial, using @lem-aux.
:::

$$
a = b
$$ {#eq-one}

<!-- sf:figure fig-plot -->

Email me at someone@example.org (not a citation).
"""


def test_analyze_extracts_structure():
    an = analyze(GOOD)
    assert an.labels == {"thm-chain": "theorem", "eq-one": "equation"}
    assert an.refs == {"eq-one", "sec-intro", "lem-aux"}
    assert an.citations == {"rumelhart1986", "cybenko1989", "hornik1991"}
    assert an.figure_markers == ["fig-plot"]
    assert an.environments == {"theorem": 1, "proof": 1}
    assert not [p for p in an.problems if p.severity == "error"]
    assert "lem-aux" in an.label_blocks["thm-chain"] or "lem-aux" not in an.label_blocks.get("thm-chain", "")


def test_structural_errors_detected():
    bad = "# Heading\n\nUnclosed $x\n\n$$\na\n\n::: {#thm-x}\nno close\n\n```python\ncode"
    codes = {p.code for p in analyze(bad).problems}
    assert {"RESERVED_HEADING_LEVEL", "UNBALANCED_INLINE_MATH", "UNBALANCED_DISPLAY_MATH", "UNBALANCED_DIV",
            "UNBALANCED_CODE_FENCE"} <= codes


def test_code_blocks_are_masked():
    an = analyze("```\n@thm-fake [@notacite] $\n```\n\nReal @eq-a.")
    assert an.refs == {"eq-a"} and an.citations == set()


def test_index_entry_insertion_skips_math_and_headings():
    md = "### Gradient\n\nThe $gradient$ symbol. The gradient of f is a vector."
    out = insert_index_entry(md, "gradient")
    assert out.count("\\index{gradient}") == 1
    assert "The gradient`\\index{gradient}`{=latex} of f" in out


def test_lint_flags_short_padding_and_repetition():
    md = ("It is important to note that the method works. " * 3 + "\n\n"
          "The quick derivation of the gradient formula follows from the chain rule applied twice in a row. "
          "The quick derivation of the gradient formula follows from the chain rule applied twice in a row.")
    an = analyze(md)
    other = {"nd_x": md}
    codes = {p.code for p in lint_block(md, an, target_words=600, elements=["theorem", "exercise"],
                                        other_blocks=other)}
    assert {"TOO_SHORT", "MISSING_ELEMENT", "FILLER", "REPEATED_SENTENCE", "OVERLAPS_OTHER_BLOCK"} <= codes


def test_terms_notation_and_cycles(project):
    db = project.db
    ch1 = db.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="A", slug="a")
    s1 = db.nodes.create(kind=NodeKind.SECTION, parent_id=ch1.id, position=0, title="S", slug="s")
    b1 = db.nodes.create(kind=NodeKind.BLOCK, parent_id=s1.id, position=0, title="B1", slug="b1")
    b2 = db.nodes.create(kind=NodeKind.BLOCK, parent_id=s1.id, position=1, title="B2", slug="b2")
    eng = ConsistencyEngine(db)
    f, new, redefs = eng.check_terms(b1.id, [TermDef(term="gradient", definition="vector of partial derivatives")])
    assert not f and len(new) == 1
    eng.apply_terms(b1.id, new, redefs)
    # silent redefinition elsewhere -> warning, registry unchanged
    f, new, redefs = eng.check_terms(b2.id, [TermDef(term="Gradient", definition="the slope of a staircase")])
    assert f[0].code == "TERM_DEFINITION_DIFFERS" and not new and not redefs
    # deliberate redefinition needs a justification
    f, _, _ = eng.check_terms(b2.id, [TermDef(term="gradient", definition="new", redefine=True)])
    assert f[0].code == "REDEFINITION_WITHOUT_JUSTIFICATION"
    eng.apply_notation(b1.id, [NotationDef(symbol="\\sigma", meaning="logistic sigmoid")])
    f = eng.check_notation(b2.id, [NotationDef(symbol="\\sigma", meaning="standard deviation of the noise")])
    assert f[0].code == "NOTATION_CONFLICT"  # same chapter
    texts = {b1.id: "::: {#thm-a}\nuses @thm-b\n:::\n", b2.id: "::: {#thm-b}\nuses @thm-a\n:::\n"}
    cycles = eng.find_cycles(eng.dependency_edges(texts))
    assert cycles == [["thm-a", "thm-b"]]


def _claim(**kw):
    base = dict(id="clm_1", node_id="nd_1", kind="proposition", text="t", created_at="x", updated_at="x")
    base.update(kw)
    return Claim(**base)


def _src(key, status=SourceStatus.VERIFIED):
    return Source(id="src_" + key, citekey=key, title="T", status=status, created_at="x", updated_at="x")


def test_claim_status_rules():
    prof = get_registry().get("mathematics")
    proof = {"c": {"id": "c", "outcome": "pass", "method": "symbolic", "scope": "general"}}
    inst = {"c": {"id": "c", "outcome": "pass", "method": "symbolic", "scope": "instance"}}
    fail = {"c": {"id": "c", "outcome": "fail", "method": "numeric", "message": "counterexample"}}
    st = lambda claim, **kw: compute_status(claim, math_results=kw.get("m", {}), sources=kw.get("s", {}),  # noqa
                                            assessments=kw.get("a", []), profile=prof,
                                            has_proof_text=kw.get("p", False))[0]
    assert st(_claim(check_ids=["c"]), m=proof) == ClaimStatus.VERIFIED
    assert st(_claim(check_ids=["c"]), m=inst) == ClaimStatus.SUPPORTED
    assert st(_claim(check_ids=["c"]), m=fail) == ClaimStatus.REQUIRES_EXPERT_REVIEW
    assert st(_claim(kind="conjecture")) == ClaimStatus.CONTESTED
    srcs = {"k": _src("k")}
    good = [{"citekey": "k", "verdict": "supports", "quote_verified": True}]
    llm_only = [{"citekey": "k", "verdict": "supports", "quote_verified": False}]
    neg = [{"citekey": "k", "verdict": "contradicts"}]
    assert st(_claim(citations=["k"]), s=srcs, a=good) == ClaimStatus.SUPPORTED
    assert st(_claim(citations=["k"]), s=srcs, a=llm_only) == ClaimStatus.UNVERIFIED  # model's word is not evidence
    assert st(_claim(citations=["k"]), s=srcs, a=neg) == ClaimStatus.CONTESTED
    assert st(_claim(citations=["k"]), s={"k": _src("k", SourceStatus.RETRACTED)}) == ClaimStatus.REQUIRES_EXPERT_REVIEW
    assert st(_claim(kind="theorem", label="thm-x"), p=True) == ClaimStatus.UNVERIFIED
    med = get_registry().get("medicine")
    assert compute_status(_claim(kind="clinical"), math_results={}, sources={}, assessments=[], profile=med,
                          has_proof_text=False)[0] == ClaimStatus.REQUIRES_EXPERT_REVIEW


def test_quote_matching():
    abstract = ("We describe a new learning procedure, back-propagation, for networks of neurone-like units. As a "
                "result of the weight adjustments, internal hidden units come to represent important features.")
    assert quote_in_text("internal hidden units come to represent important features", abstract)
    assert quote_in_text("Internal  HIDDEN units come to represent important features!", abstract)
    assert not quote_in_text("hidden units are useless for representing features of tasks", abstract)
    assert not quote_in_text("short", abstract)


def test_profiles_inherit_and_match():
    reg = get_registry()
    tp = reg.get("theoretical-physics")
    assert tp.lineage == ["generic-science", "physics", "theoretical-physics"]
    assert tp.claims.speculative_policy == "strict"
    assert tp.verification.dimensional_analysis == "recommended"
    assert reg.match("Maximally deep monograph on quantum gravity")[0] == "theoretical-physics"
    assert reg.match("Mathematical Foundations of Deep Learning")[0] == "machine-learning"
    assert reg.match("Пособие по биохимии ферментов")[0] == "biochemistry"
    assert reg.match("Underwater basket weaving")[0] == "generic-science"
