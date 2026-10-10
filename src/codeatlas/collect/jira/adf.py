"""Jira rich text to plain text (step-5 spec "Plain-text form").

Current field values arrive as ADF (Atlassian Document Format, a JSON tree), but changelog
`fromString`/`toString` arrive as wiki markup (recorded SBX-6, 2026-10-07). Both convert to the
same plain form so a changelog chain can be checked against the current value. Input is
untrusted: malformed ADF never raises.
"""

import re
from typing import Any

_INLINE = {"text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status"}
_WIKI_HEADING = re.compile(r"h[1-6]\. (.*)")
_WIKI_ORDERED = re.compile(r"#+ (.*)")
_WIKI_BULLET = re.compile(r"\*+ (.*)")


def adf_to_text(value: Any) -> str:
    """Plain text of an ADF document (or of a string, or `""` for None)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _tidy(value.splitlines())
    if isinstance(value, dict | list):
        return _tidy(_blocks(value))
    return str(value)


def wiki_to_text(value: str | None) -> str:
    """Plain text of Jira wiki markup: `hN.` headings, `#` numbered items, `*` bullets."""
    lines: list[str] = []
    number = 0
    for raw in (value or "").splitlines():
        line = raw.rstrip()
        if heading := _WIKI_HEADING.fullmatch(line):
            lines.append(heading.group(1))
        elif ordered := _WIKI_ORDERED.fullmatch(line):
            number += 1
            lines.append(f"{number}. {ordered.group(1)}")
            continue
        elif bullet := _WIKI_BULLET.fullmatch(line):
            lines.append(f"- {bullet.group(1)}")
        else:
            lines.append(line)
        number = 0
    return _tidy(lines)


def _tidy(lines: list[str]) -> str:
    """Trailing spaces removed, blank lines dropped, one line per block."""
    out = [line.rstrip() for text in lines for line in text.split("\n")]
    return "\n".join(line for line in out if line.strip())


def _children(node: dict[str, Any]) -> list[Any]:
    content = node.get("content")
    return content if isinstance(content, list) else []


def _attr(node: dict[str, Any], name: str) -> Any:
    attrs = node.get("attrs")
    return attrs.get(name) if isinstance(attrs, dict) else None


def _blocks(node: Any) -> list[str]:
    """Lines produced by a block node (or by a list of nodes)."""
    if isinstance(node, list):
        return [line for child in node for line in _blocks(child)]
    if not isinstance(node, dict):
        return []
    kind = node.get("type")
    if kind in _INLINE:
        return [_inline(node)]
    if kind in ("paragraph", "heading"):
        return ["".join(_inline(child) for child in _children(node))]
    if kind in ("bulletList", "orderedList"):
        return _list(node, ordered=kind == "orderedList")
    if kind in ("table", "tableRow"):
        rows = _children(node) if kind == "table" else [node]
        return [_row(row) for row in rows if isinstance(row, dict)]
    if kind == "media":
        return ["[image]"]
    return _blocks(_children(node))


def _inline(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        text = node.get("text")
        return "" if text is None else str(text)
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji"):
        return str(_attr(node, "text") or "")
    if kind == "inlineCard":
        return str(_attr(node, "url") or "")
    if kind == "media":
        return "[image]"
    return "".join(_inline(child) for child in _children(node))


def _list(node: dict[str, Any], *, ordered: bool) -> list[str]:
    start = _attr(node, "order")
    number = start if isinstance(start, int) and not isinstance(start, bool) else 1
    lines: list[str] = []
    for child in _children(node):
        item = _blocks(child) or [""]
        prefix = f"{number}. " if ordered else "- "
        lines.append(prefix + item[0])
        lines.extend(item[1:])
        number += 1
    return lines


def _row(row: dict[str, Any]) -> str:
    cells = [" ".join(_blocks(cell)).strip() for cell in _children(row)]
    return " | ".join(cells)
