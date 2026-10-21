"""CODEOWNERS pattern matching and last-match owner resolution."""

from __future__ import annotations

import re

from codeatlas.schema import OwnerRule


def owners_for(path: str, rules: list[OwnerRule]) -> list[str]:
    """Return owners from the last matching rule; an ownerless match clears ownership."""
    chosen: list[str] | None = None
    for rule in rules:
        if codeowners_match(rule.pattern, path):
            chosen = rule.owners
    return list(chosen or [])


def codeowners_match(pattern: str, path: str) -> bool:
    """Match one CODEOWNERS glob using GitHub's documented path semantics."""
    anchored = pattern.startswith("/")
    pat = pattern[1:] if anchored else pattern
    if not pat:
        return False

    directory_pattern = pat.endswith("/")
    if directory_pattern:
        pat = pat[:-1]
    has_path_separator = "/" in pat

    if not has_path_separator and not anchored:
        if directory_pattern:
            return re.search(rf"(?:^|/){_translate(pat)}(?:/.*)?$", path) is not None
        return re.search(rf"(?:^|/){_translate(pat)}$", path) is not None

    regex = _translate(pat)
    final_segment = pat.rsplit("/", 1)[-1]
    literal_directory = not any(wildcard in final_segment for wildcard in "*?")
    suffix = r"(?:/.*)?" if directory_pattern or literal_directory else ""
    return re.fullmatch(regex + suffix, path) is not None


def _translate(pattern: str) -> str:
    """Translate CODEOWNERS wildcards; `*` and `?` never cross path separators."""
    result: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    result.append(r"(?:.*/)?")
                    index += 1
                else:
                    result.append(".*")
                continue
            result.append("[^/]*")
        elif char == "?":
            result.append("[^/]")
        else:
            result.append(re.escape(char))
        index += 1
    return "".join(result)
