# CLAUDE.md

Rules for AI agents working in this repository. The source of truth is the mid-evaluation spec,
`docs/specs/mid-eval-spec.md`, as changed by the amendments below and by the per-step specs in
`docs/specs/step-<N>-<name>.md`. The section that follows is copied verbatim from spec section 14.

## AI-assisted development rules (spec section 14, verbatim)

The team will use Claude Code for most implementation. These rules make that safe and make the human contribution visible. Copy this section into `CLAUDE.md` at the repo root in step 1 so the agent reads it every session.

**Workflow per issue (spec-first)**

1. The owner opens the issue and asks the agent for a spec only: `docs/specs/<issue#>-<name>.md` with purpose, inputs/outputs using section 7 types, acceptance criteria (Given/When/Then), fixture list with expected verdicts, files to touch, and open questions.
2. The owner reads, edits and commits the spec. **No code before the spec is committed.**
3. The agent writes tests from the spec, then code to pass them, on the feature branch.
4. The owner runs the tests locally, reads every changed line, and opens the PR.
5. A second member reviews against the spec, not against the agent's summary.

**The agent must not**

- invent API endpoints, fields or behaviour of Jira, GitHub, pygit2 or tree-sitter; when unsure it reads the official docs or a recorded response and cites which;
- invent requirements, cases, thresholds or schema fields not in this specification; if one seems needed, it stops and lists it as an open question;
- change `schema/` after G1, the truth table, or the layer contracts without an approved issue;
- write tests that only check that code runs, hand-write fixtures, edit a golden file to make a test pass, or weaken an assertion to go green;
- add a dependency not listed in section 4 without saying so;
- make one commit touching more than one issue, or a PR above the size limit without explanation;
- touch secrets, real personal data or `.env`; print tokens;
- call Jira or GitHub with write methods (CodeAtlas is read-only);
- push, merge, tag or close issues; humans do those.

**The agent must report at the end of every task**

- files changed and why;
- assumptions made;
- tests added; tests run and their result; tests **not** run and why;
- anything in the spec it could not satisfy;
- risks or follow-ups.

The owner pastes this report into the PR description.

**Disclosure:** the final report has a GenAI disclosure section listing tools used, what they were used for, and how output was verified. Each member must be able to explain, without the agent, any file they own in the demo Q&A.

## Amendments (decided by the owner, 2026-10-07)

These override the spec where they differ. Recorded in `docs/specs/step-1-repo-hygiene.md`.

- **A1 Branches:** `feature/<kebab-name>`, `fix/<kebab-name>`, `docs/<kebab-name>`. No issue
  number.
- **A2 Commits:** Conventional Commits `type(scope): message`, type one of `feat`, `fix`, `docs`,
  `chore`, `test`, `refactor`; scope optional. No `(#n)` required (allowed, since GitHub adds the
  PR number to squash commits). `scripts/check_commit_msgs.py` enforces A1 and A2 in CI.
- **A3 Specs:** `docs/specs/step-<N>-<name>.md` instead of `<issue#>-<name>.md`.
- **A4 Pull requests:** the description names the spec step it implements; an issue link is
  optional.
- **A5 Merging:** squash merge into `develop`; merge commit from `develop` into `main`.
- **A6 Issues:** a task board only. Nothing depends on issue numbers.

## Owner's standing rules for the agent

- **Default: never commit or push.** Make the file changes, run the checks, then give the owner
  the exact git commands (branch, add, commit message) to run. The owner commits and pushes.
- **No Claude co-author lines.** Commits are authored by the owner; never add `Co-Authored-By`
  or "Generated with" footers. Disclosure uses the trailer below instead.

## Task authority: Phase 1 autonomous build (granted by Areej, 2026-10-07)

For the task "implement steps 2 to 8 of the mid-evaluation spec", this overrides the default
above. The agent **may**:

- create branches, commit, push feature branches, and open PRs into `develop` with
  `gh pr create`;
- read from Jira using the credentials in `.env` (never print or commit them);
- write to Jira **only** in project SBX, **only** from `tools/scenario_generator/`, and **only**
  to issues labelled `generated`.

The agent **must not**:

- merge PRs, push to `develop` or `main`, force-push, tag, or delete branches;
- edit SBX-1 to SBX-7 (hand-made demo issues);
- touch Yusra's or Saleha's areas (spec section 9);
- change `schema/` after G1 without stopping to ask;
- commit `.env`, tokens, real emails, real names or real account IDs.

Every commit message ends with the trailer `Assisted-by: Claude Code` (GenAI disclosure).
- Run `gh issue create` (or any other GitHub write) only after the owner has seen and approved
  the exact commands.
- Work one step at a time: spec → owner review → tests → code → section 14 report, then stop.
- Never guess GitHub logins, issue numbers, Jira fields or endpoints. Ask, or cite the official
  docs or a recorded response.

## Checks to run before reporting

```bash
uv run pre-commit run --all-files
uv run lint-imports
uv run mypy src
uv run pytest
```
