"""
tests/test_tools.py

Tests for the three FitFindr tools + parse_query. At least one test per failure
mode (no listings found / empty wardrobe / incomplete outfit).

The two tools that call the LLM are tested with a monkeypatched _chat so the
suite runs fast and doesn't need an API key — I'm testing the branching logic
(empty wardrobe vs not, empty outfit guard), not the model's wording.

Run with:  pytest tests/ -q
"""

import tools
from tools import search_listings, suggest_outfit, create_fit_card, parse_query


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # nothing in the dataset is a designer ballgown in XXS under $5
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []          # empty list, NOT an exception


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=30)
    assert all(item["price"] <= 30 for item in results)


def test_search_size_filter_case_insensitive():
    # "m" should still match sizes like "M" and "S/M"
    results = search_listings("tee", size="m", max_price=None)
    assert all("M" in item["size"].upper() for item in results)


def test_search_results_sorted_by_relevance():
    # more keyword overlap should rank higher — the top hit should match
    # at least as many keywords as the last hit
    results = search_listings("vintage denim jacket", size=None, max_price=None)
    assert len(results) > 1
    text = lambda it: (it["title"] + " " + it["description"] + " " + " ".join(it["style_tags"])).lower()
    kws = ["vintage", "denim", "jacket"]
    top_score = sum(k in text(results[0]) for k in kws)
    last_score = sum(k in text(results[-1]) for k in kws)
    assert top_score >= last_score


# ── parse_query ───────────────────────────────────────────────────────────────

def test_parse_pulls_price_and_size():
    p = parse_query("vintage graphic tee under $30, size M")
    assert p["max_price"] == 30.0
    assert p["size"] == "M"


def test_parse_no_filters():
    p = parse_query("flowy midi skirt")
    assert p["max_price"] is None
    assert p["size"] is None


# ── suggest_outfit (LLM mocked) ───────────────────────────────────────────────

def _fake_chat(prompt, **kwargs):
    """Stand-in for the LLM — echoes back a tag so we can assert which branch ran.
    The non-empty branch lists the closet with this exact phrase."""
    if "already in their closet" in prompt:
        return "MOCK_OUTFIT (saw closet)"
    return "MOCK_OUTFIT (general)"


def test_suggest_outfit_with_wardrobe(monkeypatch):
    monkeypatch.setattr(tools, "_chat", _fake_chat)
    item = search_listings("vintage graphic tee", None, 50)[0]
    wardrobe = {"items": [{"name": "baggy jeans", "category": "bottoms",
                           "colors": ["blue"], "style_tags": ["denim"], "notes": None}]}
    out = suggest_outfit(item, wardrobe)
    assert isinstance(out, str) and out.strip() != ""
    assert "closet" in out  # took the "has a wardrobe" branch


def test_suggest_outfit_empty_wardrobe(monkeypatch):
    # failure mode: empty wardrobe should still return useful advice, not crash
    monkeypatch.setattr(tools, "_chat", _fake_chat)
    item = search_listings("vintage graphic tee", None, 50)[0]
    out = suggest_outfit(item, {"items": []})
    assert isinstance(out, str) and out.strip() != ""
    assert "general" in out  # took the empty-wardrobe branch


# ── create_fit_card ───────────────────────────────────────────────────────────

def test_fit_card_empty_outfit_returns_message():
    # failure mode: missing outfit -> descriptive string, NOT an exception,
    # and the LLM should never even be called
    item = search_listings("vintage graphic tee", None, 50)[0]
    card = create_fit_card("", item)
    assert isinstance(card, str)
    assert "without an outfit" in card.lower()


def test_fit_card_whitespace_outfit_returns_message():
    item = search_listings("vintage graphic tee", None, 50)[0]
    card = create_fit_card("   \n  ", item)
    assert "without an outfit" in card.lower()


def test_fit_card_valid(monkeypatch):
    monkeypatch.setattr(tools, "_chat", lambda prompt, **kw: "thrifted fit, love it")
    item = search_listings("vintage graphic tee", None, 50)[0]
    card = create_fit_card("jeans + sneakers", item)
    assert card == "thrifted fit, love it"
