import json
import logging

import pytest

from scholarforge.logging_setup import configure_logging, redact
from scholarforge.util.atomic import atomic_write_json, atomic_write_text, read_json, read_text
from scholarforge.util.ids import is_valid_id, new_id
from scholarforge.util.text import (
    detect_script_language,
    jaccard,
    last_paragraphs,
    shingles,
    slugify,
    title_similarity,
    word_count,
)


def test_atomic_write_creates_parents_and_leaves_no_temp(tmp_path):
    target = tmp_path / "a" / "b" / "file.md"
    atomic_write_text(target, "hello\r\nworld")
    assert read_text(target) == "hello\nworld"
    atomic_write_text(target, "second")
    assert read_text(target) == "second"
    leftovers = [p.name for p in target.parent.iterdir() if p.name != "file.md"]
    assert leftovers == []


def test_atomic_write_json_roundtrip_unicode(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"заголовок": "Квантовая гравитация", "n": 3})
    assert read_json(p) == {"заголовок": "Квантовая гравитация", "n": 3}
    assert "Квантовая" in p.read_text(encoding="utf-8")


def test_atomic_write_failure_keeps_original(tmp_path, monkeypatch):
    tmp_path = tmp_path / "isolated"
    tmp_path.mkdir()
    p = tmp_path / "keep.txt"
    atomic_write_text(p, "original")

    import scholarforge.util.atomic as atomic

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(atomic, "replace_with_retry", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "new content")
    assert read_text(p) == "original"
    assert [x.name for x in tmp_path.iterdir()] == ["keep.txt"]


def test_ids_are_prefixed_unique_and_sortable():
    ids = [new_id("tsk") for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(is_valid_id(i, "tsk") for i in ids)
    assert not is_valid_id("tsk_short")
    assert not is_valid_id(ids[0], "prj")
    with pytest.raises(ValueError):
        new_id("BAD")


def test_slugify_transliterates_cyrillic_and_greek():
    assert slugify("Квантовая гравитация") == "kvantovaya-gravitatsiya"
    assert slugify("Ґрунтознавство і їжа") == "gruntoznavstvo-i-yizha"
    assert slugify("Σ-algebras & measures") == "sigma-algebras-measures"
    assert slugify("!!!") == "item"


def test_title_similarity_is_robust():
    assert title_similarity("Learning representations by back-propagating errors",
                            "Learning Representations by Back-Propagating Errors") > 0.95
    assert title_similarity("Deep learning", "Deep Learning: an introduction to the field") > 0.85
    assert title_similarity("Adam: A Method for Stochastic Optimization", "Quantum gravity in two dimensions") < 0.5


def test_word_count_ignores_math_and_markup():
    md = "## Title\n\nThe gradient $\\nabla f$ is a vector.\n\n$$\nf(x) = x^2\n$$\n\n::: {#thm-a}\nText [@key].\n:::\n"
    assert word_count(md) == 8  # Title The gradient MATH is a vector Text


def test_shingles_and_jaccard_detect_duplicates():
    a = shingles("the chain rule states that the derivative of a composition is a product of derivatives")
    b = shingles("the chain rule states that the derivative of a composition is a product of derivatives")
    c = shingles("eigenvalues of symmetric matrices are real and eigenvectors can be chosen orthonormal")
    assert jaccard(a, b) == 1.0
    assert jaccard(a, c) == 0.0


def test_last_paragraphs_and_language_detection():
    text = "p1\n\np2\n\np3"
    assert last_paragraphs(text, 2) == "p2\n\np3"
    assert detect_script_language("Создай учебник") == "ru"
    assert detect_script_language("Створи підручник з її розділами") == "uk"
    assert detect_script_language("Create a textbook") == "en"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123",
        "sk_live_ABCDEFGHIJKLMNOP",
        "whsec_1234567890abcdef",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "api_key=supersecretvalue123",
        "someone@example.org",
    ],
)
def test_redaction(secret):
    out = redact(f"value is {secret} end")
    assert secret not in out
    assert "REDACTED" in out or "***" in out


def test_logs_never_contain_secrets(sf_home):
    from scholarforge import paths

    logger = configure_logging("DEBUG")
    logger.info("calling API with key %s", "sk-proj-THISISASECRETKEY123456")
    try:
        raise RuntimeError("failure with token sk-THISISANOTHERSECRET9999")
    except RuntimeError:
        logger.exception("boom")
    for h in logger.handlers:
        h.flush()
    content = (paths.logs_dir() / "scholarforge.log").read_text(encoding="utf-8")
    assert "THISISASECRETKEY" not in content
    assert "THISISANOTHERSECRET" not in content
    lines = [json.loads(line) for line in content.splitlines()]
    assert any("REDACTED" in line["msg"] for line in lines)
    logging.getLogger("scholarforge").handlers.clear()
