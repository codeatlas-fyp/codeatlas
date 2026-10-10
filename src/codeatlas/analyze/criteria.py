"""Acceptance-criteria splitter (spec §6 `analyze.criteria`, step-12/13).

Given a requirement's description text + version, produce one `Criterion`
per acceptance criterion:

- If the description contains an explicit "Acceptance Criteria" (or similar)
  heading followed by bullets/numbered list, each bullet is one Criterion.
- Else, split the description's sentences; each sentence is one Criterion,
  flagged `derived_from_description=True`.

Pure function. No I/O.
"""

from __future__ import annotations

import re

from codeatlas.schema import Criterion

EXTRACTOR_VERSION = "criteria@0.1.0"

_AC_HEADER_RE = re.compile(
    r"(?im)^\s*(?:acceptance\s+criteria|acceptance|ac|criteria)\s*:?\s*$",
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*?)\s*$", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_criteria(
    *,
    requirement_key: str,
    version_no: int,
    description: str,
) -> list[Criterion]:
    """Return the ordered acceptance criteria for one requirement version."""
    if not description or not description.strip():
        return []

    bullets = _extract_bullets_after_ac_header(description)
    if bullets:
        return [
            Criterion(
                criterion_id=f"{requirement_key}:v{version_no}:ac{i}",
                key=requirement_key,
                version_no=version_no,
                text=text,
                derived_from_description=False,
            )
            for i, text in enumerate(bullets, start=1)
        ]

    # No AC section — fall back to sentence split of the whole description.
    sentences = _split_sentences(description)
    return [
        Criterion(
            criterion_id=f"{requirement_key}:v{version_no}:ac{i}",
            key=requirement_key,
            version_no=version_no,
            text=text,
            derived_from_description=True,
        )
        for i, text in enumerate(sentences, start=1)
    ]


def _extract_bullets_after_ac_header(description: str) -> list[str]:
    """Find an 'Acceptance Criteria' header and return bullets that follow it."""
    match = _AC_HEADER_RE.search(description)
    if match is None:
        return []
    tail = description[match.end():]
    # Stop at the next heading-ish line (something ending with ':' at column 0
    # and not itself a bullet).
    end_match = re.search(r"\n\s*\n\s*\w[^\n]*:\s*\n", tail)
    section = tail[: end_match.start()] if end_match else tail
    bullets = [m.group(1).strip() for m in _BULLET_RE.finditer(section)]
    return [b for b in bullets if b]


def _split_sentences(text: str) -> list[str]:
    """Simple sentence splitter — good enough for mid-eval."""
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks = _SENTENCE_RE.split(cleaned)
    return [c.strip().rstrip(".") + "." for c in chunks if c.strip()]
