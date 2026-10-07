"""Rule registry (spec §6 `evaluate.rules`). Rules C1, C3, C6, C7 and C8 arrive in step 7."""

from collections.abc import Iterable

from codeatlas.evaluate.rules.base import Rule


def default_rules() -> tuple[Rule, ...]:
    """The rules every evaluation runs, in no particular order (order never matters)."""
    return ()


def ruleset_version(rules: Iterable[Rule]) -> str:
    """`"C1@1,C3@2"`: the cases and versions that produced a verdict; `"none"` if empty."""
    parts = sorted(f"{rule.case}@{rule.version}" for rule in rules)
    return ",".join(parts) or "none"
