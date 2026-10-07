"""Create generated SBX issues from seeded plans and log the ground truth.

Each action is performed through `JiraWriter`, then the issue's changelog is read back so that
every action carries **Jira's** timestamp (no dependence on this machine's clock). Commit
markers get a time halfway between their neighbouring actions; actions are spaced `gap` seconds
apart so these times never fall in the same second as an action.

Usage: uv run python -m tools.scenario_generator --count 50 --seed 2026
"""

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tools.scenario_generator.jira_writer import JiraWriter
from tools.scenario_generator.oracle import Done, expected
from tools.scenario_generator.plan import IssuePlan, make_plans

ROOT = Path(__file__).resolve().parents[2]
STATUS_FOR = {"approve": "Approved", "unapprove": "To Do"}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)


def jira_times(plan: IssuePlan, changelog: list[dict[str, Any]], created: datetime) -> list[Done]:
    """Match each performed action to its changelog entry and place the commit markers."""
    items = sorted(
        ((parse_time(h["created"]), int(h["id"]), item) for h in changelog for item in h["items"]),
        key=lambda entry: entry[:2],
    )
    wanted = {
        "edit": "description",
        "approve": "status",
        "unapprove": "status",
        "priority": "priority",
    }
    done: list[Done | None] = []
    position = 0
    for action in plan.actions:
        if action.kind in ("first_commit", "last_commit"):
            done.append(None)
            continue
        while (items[position][2].get("fieldId") or items[position][2]["field"]) != wanted[
            action.kind
        ]:
            position += 1
        done.append(Done(action.kind, items[position][0], action.text))
        position += 1
    # Commit markers: halfway between the actions around them.
    result: list[Done] = []
    for index, (action, entry) in enumerate(zip(plan.actions, done, strict=True)):
        if entry is not None:
            result.append(entry)
            continue
        before = next((d.at for d in reversed(done[:index]) if d is not None), created)
        after = next((d.at for d in done[index + 1 :] if d is not None), None)
        at = before + (after - before) / 2 if after else before + timedelta(seconds=2)
        result.append(Done(action.kind, at.replace(microsecond=0)))
    return result


def run(
    writer: JiraWriter,
    plans: list[IssuePlan],
    run_label: str,
    *,
    gap: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    records = []
    for plan in plans:
        key = writer.create_issue(plan.summary, plan.description, run_label)
        created = datetime.now(UTC).replace(microsecond=0)
        for action in plan.actions:
            sleep(gap)
            if action.kind == "edit":
                writer.edit_description(key, action.text or "")
            elif action.kind in STATUS_FOR:
                writer.move_to(key, STATUS_FOR[action.kind])
            elif action.kind == "priority":
                writer.set_priority(key, action.priority or "Medium")
        log = jira_times(plan, writer.changelog(key), created)
        truth = expected(plan.description, log)
        records.append(
            {
                "key": key,
                "summary": plan.summary,
                "initial_description": plan.description,
                "actions": [
                    {"kind": d.kind, "at": d.at.strftime("%Y-%m-%dT%H:%M:%SZ"), "text": d.text}
                    for d in log
                ],
                "expected": asdict(truth),
            }
        )
        print(f"{key}: {len(plan.actions)} actions", flush=True)
    return records


def main() -> None:
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--project", default="SBX")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "report" / "data")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    base_url = env.get("CODEATLAS_JIRA_BASE_URL") or ""
    label = f"gen-{args.seed}-{args.count}"
    writer = JiraWriter(
        base_url,
        env.get("CODEATLAS_JIRA_EMAIL") or "",
        env.get("CODEATLAS_JIRA_TOKEN") or "",
        project=args.project,
    )
    try:
        records = run(writer, make_plans(args.count, args.seed), label)
    finally:
        writer.close()
    out = args.out / f"ground-truth-{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "seed": args.seed,
        "count": args.count,
        "label": label,
        "actor": "the generator's Jira account (account id not stored)",
        "issues": records,
    }
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(out)


if __name__ == "__main__":
    main()
