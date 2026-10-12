"""Seeded plans of what to do to each generated SBX issue (pure, no I/O).

A plan is a list of actions per issue: description edits, moves into and out of Approved,
priority changes, and two markers saying where the PR's first and last commits fall. The same
seed always gives the same plans.
"""

import random
from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal["edit", "approve", "unapprove", "priority", "first_commit", "last_commit"]
PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
_VERBS = ("borrow", "return", "renew", "reserve", "search", "register", "pay", "notify")
_THINGS = ("a book", "a loan", "a fine", "a member", "a reservation", "the catalogue")
_CONDITIONS = ("within 14 days", "before the due date", "at most twice", "with a receipt")


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    text: str | None = None  # new description for "edit"
    priority: str | None = None  # new priority for "priority"


@dataclass(frozen=True)
class IssuePlan:
    summary: str
    description: str
    actions: list[Action] = field(default_factory=list)


def _criterion(rng: random.Random) -> str:
    return f"A member can {rng.choice(_VERBS)} {rng.choice(_THINGS)} {rng.choice(_CONDITIONS)}."


def _description(rng: random.Random) -> str:
    return "\n".join(_criterion(rng) for _ in range(rng.randint(1, 3)))


def plan_issue(rng: random.Random, index: int) -> IssuePlan:
    """Random actions; approve/unapprove only when they are possible; both commit markers once."""
    initial = _description(rng)
    actions: list[Action] = []
    approved = False
    priority = "Medium"
    for _ in range(rng.randint(2, 7)):
        choice = rng.choice(["edit", "approve", "unapprove", "priority"])
        if choice == "edit":
            actions.append(Action("edit", text=_description(rng)))
        elif choice == "approve" and not approved:
            approved = True
            actions.append(Action("approve"))
        elif choice == "unapprove" and approved:
            approved = False
            actions.append(Action("unapprove"))
        elif choice == "priority":
            priority = rng.choice([p for p in PRIORITIES if p != priority])
            actions.append(Action("priority", priority=priority))
    first = rng.randint(0, len(actions))
    last = rng.randint(first, len(actions))
    actions.insert(last, Action("last_commit"))
    actions.insert(first, Action("first_commit"))
    return IssuePlan(
        summary=f"Generated requirement {index:03d}: {rng.choice(_VERBS)} {rng.choice(_THINGS)}",
        description=initial,
        actions=actions,
    )


def make_plans(count: int, seed: int) -> list[IssuePlan]:
    rng = random.Random(seed)  # noqa: S311 (test data, not security)
    return [plan_issue(rng, i) for i in range(1, count + 1)]
