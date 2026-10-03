"""Enforce the CONTRIBUTING.md naming rules in CI.

Usage:
    python scripts/check_commit_msgs.py <base_ref> <head_ref> [branch_name]

Every non-merge commit subject in base..head must look like `CA-<n>: <message>`.
If branch_name is given, it must be develop or feature|fix/CA-<n>-<kebab-name>.
A develop -> main release PR is allowed.
"""

import re
import subprocess
import sys

COMMIT_RE = re.compile(r"^CA-\d+: \S.*$")
BRANCH_RE = re.compile(r"^(develop|(feature|fix)/CA-\d+-[a-z0-9]+(-[a-z0-9]+)*)$")


def main() -> int:
    base, head = sys.argv[1], sys.argv[2]
    branch = sys.argv[3] if len(sys.argv) > 3 else None
    errors = []

    if branch and not BRANCH_RE.match(branch):
        errors.append(f"branch '{branch}' must match feature/CA-<n>-<short-name> or fix/CA-<n>-...")

    log = subprocess.run(
        ["git", "log", "--no-merges", "--format=%h %s", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in log.splitlines():
        sha, _, subject = line.partition(" ")
        if not COMMIT_RE.match(subject):
            errors.append(f"commit {sha} '{subject}' must start with 'CA-<n>: '")

    for error in errors:
        print(f"::error::{error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
