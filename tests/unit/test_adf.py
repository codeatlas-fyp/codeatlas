"""Tests for docs/specs/step-5-jira-collector.md AC6-AC8 (ADF and wiki markup to plain text)."""

from typing import Any

import pytest

from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text


def text(value: str, **marks: Any) -> dict[str, Any]:
    return {"type": "text", "text": value, **marks}


def para(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(content)}


def items(*texts: str) -> list[dict[str, Any]]:
    return [{"type": "listItem", "content": [para(text(t))]} for t in texts]


def doc(*content: Any) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(content)}


# --- AC6: each node type ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (doc(para(text("Intro"))), "Intro"),
        (doc({"type": "heading", "attrs": {"level": 2}, "content": [text("Title")]}), "Title"),
        (doc({"type": "bulletList", "content": items("a", "b")}), "- a\n- b"),
        (
            doc({"type": "orderedList", "attrs": {"order": 3}, "content": items("c", "d")}),
            "3. c\n4. d",
        ),
        (doc({"type": "orderedList", "content": items("x")}), "1. x"),
        (doc(para(text("a"), {"type": "hardBreak"}, text("b"))), "a\nb"),
        (doc(para({"type": "mention", "attrs": {"text": "@User 01"}})), "@User 01"),
        (doc(para({"type": "emoji", "attrs": {"text": ":)"}})), ":)"),
        (doc(para({"type": "inlineCard", "attrs": {"url": "https://u"}})), "https://u"),
        (
            doc({"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "x"}}]}),
            "[image]",
        ),
        (
            doc(
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {"type": "tableHeader", "content": [para(text("k"))]},
                                {"type": "tableCell", "content": [para(text("v"))]},
                            ],
                        }
                    ],
                }
            ),
            "k | v",
        ),
        (doc(para(text("bold", marks=[{"type": "strong"}]), text(" plain "))), "bold plain"),
    ],
    ids=[
        "paragraph",
        "heading",
        "bullets",
        "ordered-start",
        "ordered-default",
        "hard-break",
        "mention",
        "emoji",
        "link",
        "media",
        "table",
        "marks-and-trailing-space",
    ],
)
def test_ac6_adf_node_types(node: dict[str, Any], expected: str) -> None:
    assert adf_to_text(node) == expected


def test_ac6_none_and_plain_strings() -> None:
    assert adf_to_text(None) == ""
    assert adf_to_text("\n line one  \n\n line two\n") == " line one\n line two"


# --- AC7: malformed ADF never raises --------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (doc({"type": "futureNode", "content": [para(text("kept"))]}), "kept"),
        (doc({"type": "paragraph"}), ""),
        (doc("not a node", 42, None, para(text("ok"))), "ok"),
        (doc(para({"type": "text", "text": 5})), "5"),
        (doc(para({"type": "text"})), ""),
        ({"type": "doc", "content": "not a list"}, ""),
        (doc({"type": "orderedList", "attrs": {"order": "x"}, "content": items("y")}), "1. y"),
        (doc({"type": "bulletList", "content": [para(text("not an item"))]}), "- not an item"),
        (doc({"type": "mention"}), ""),
        (12345, "12345"),
        ([para(text("list root"))], "list root"),
    ],
    ids=[
        "unknown-type",
        "missing-content",
        "non-dict-children",
        "non-string-text",
        "text-without-text",
        "content-not-a-list",
        "bad-order",
        "list-without-items",
        "mention-without-attrs",
        "number",
        "list-root",
    ],
)
def test_ac7_malformed_adf_is_converted_without_raising(node: Any, expected: str) -> None:
    assert adf_to_text(node) == expected


# --- AC8: wiki markup from the changelog ----------------------------------------------------------

# fromString of SBX-6 history 10093, as recorded on 2026-10-07 (public library-story text).
SBX6_WIKI = (
    "As a visitor, I want to register as a member so that I can borrow books.\n\nh2. Acceptance "
    "criteria\n\n# Given a name and an email, when a visitor registers, then a member account is "
    "created."
)


def test_ac8_wiki_matches_adf_for_the_same_content() -> None:
    adf = doc(
        para(text("As a visitor, I want to register as a member so that I can borrow books.")),
        {"type": "heading", "attrs": {"level": 2}, "content": [text("Acceptance criteria")]},
        {
            "type": "orderedList",
            "attrs": {"order": 1},
            "content": items(
                "Given a name and an email, when a visitor registers, then a member account "
                "is created."
            ),
        },
    )

    assert wiki_to_text(SBX6_WIKI) == adf_to_text(adf)


@pytest.mark.parametrize(
    ("wiki", "expected"),
    [
        (None, ""),
        ("", ""),
        ("h1. Big\nh6. Small", "Big\nSmall"),
        ("# one\n# two\nplain\n# again", "1. one\n2. two\nplain\n1. again"),
        ("* a\n* b", "- a\n- b"),
        ("#no-space stays", "#no-space stays"),
        ("*bold* text", "*bold* text"),
        ("line  \n\n\nnext", "line\nnext"),
        ("h7. not a heading", "h7. not a heading"),
    ],
    ids=[
        "none",
        "empty",
        "headings",
        "numbering-restarts",
        "bullets",
        "hash-without-space",
        "bold-kept",
        "blank-lines",
        "h7",
    ],
)
def test_ac8_wiki_subset(wiki: str | None, expected: str) -> None:
    assert wiki_to_text(wiki) == expected
