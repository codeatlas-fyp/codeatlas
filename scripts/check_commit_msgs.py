"""Enforce the branch and commit rules in CONTRIBUTING.md (spec step 1, amendments A1 and A2).

Usage:
    python scripts/check_commit_msgs.py <base_ref> <head_ref> [branch_name]

Every non-merge commit subject in base..head must be a Conventional Commit:
`type(scope): message`, with type feat, fix, docs, chore, test or refactor and an optional
lowercase scope. A trailing `(#n)` is allowed, because GitHub adds the PR number to squash
commits. If branch_name is given, it must be develop or feature|fix|docs/<kebab-name>.
"""

import re
import subprocess
import sys

COMMIT_RE = re.compile(r"^(feat|fix|docs|chore|test|refactor)(\([a-z0-9-]+\))?: \S.*$")
BRANCH_RE = re.compile(r"^(develop|(feature|fix|docs)/[a-z0-9]+(-[a-z0-9]+)*)$")


def check_branch(name: str) -> str | None:
    """Return an error message if the branch name breaks the rule, else None."""
    if BRANCH_RE.match(name):
        return None
    return f"branch '{name}' must match feature|fix|docs/<kebab-name>"


def check_subject(sha: str, subject: str) -> str | None:
    """Return an error message if the commit subject is not a Conventional Commit, else None."""
    if COMMIT_RE.match(subject):
        return None
    return (
        f"commit {sha} '{subject}' must look like 'type(scope): message' "
        "with type feat, fix, docs, chore, test or refactor"
    )


def main(argv: list[str]) -> int:
    base, head = argv[0], argv[1]
    branch = argv[2] if len(argv) > 2 else None
    errors = []

    if branch is not None:
        errors.append(check_branch(branch))

    log = subprocess.run(
        ["git", "log", "--no-merges", "--format=%h %s", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in log.splitlines():
        sha, _, subject = line.partition(" ")
        errors.append(check_subject(sha, subject))

    found = [error for error in errors if error is not None]
    for error in found:
        print(f"::error::{error}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
