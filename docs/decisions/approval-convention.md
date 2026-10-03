# Decision: what counts as "approved" and "authorised priority change"

- **Status:** accepted
- **Date:** 2026-10-03
- **Owner:** Areej
- **Applies to:** the Jira project `SBX` (sandbox requirements), and the CodeAtlas rules that read it

## Context

Jira Software has no built-in "requirement approval". An issue is just in some status, and anyone
with permission can move it. CodeAtlas needs a definition it can check from the issue's history
(the changelog). So we define approval and priority authority **by configuration**:
a workflow status plus a Jira group.

Restricting transitions in the workflow is only a convenience. The rules always check **who** made
the change, using the changelog and that person's group membership. So the convention holds even
on a Jira plan where the restriction can't be enforced.

## Decision

### 1. Approved

A ticket counts as **approved** when its changelog has a status transition **into `Approved`**,
made by a user who belongs to the group **`sbx-requirement-approvers`**.

- The workflow is: `To Do → Ready for Approval → Approved → In Progress → In Review → Done`.
- Being in `Approved` (or any later status) is **not enough** on its own. The transition into
  `Approved` must have been made by a group member. For example, if an admin drags the ticket
  straight to `In Progress`, it is not approved.
- If a ticket entered `Approved` more than once, the **most recent** entry counts.
- If the ticket's **Acceptance Criteria** field changes *after* that approval, the approval is
  stale. CodeAtlas reports this as **REVIEW**, not PASS (scenario SBX-3).

### 2. Authorised priority change

A priority change counts as **authorised** when the changelog entry for the `priority` field was
made by a user who belongs to **`sbx-priority-authority`**. A priority change by anyone else is
**unauthorised** and gets flagged (rule R3, scenario SBX-4).

The priority set when the ticket was created is not a change, so this rule doesn't check it.

### 3. Group membership

| Group | Members (sandbox) | Meaning |
|---|---|---|
| `sbx-requirement-approvers` | Areej | may approve SBX requirements |
| `sbx-priority-authority` | Areej | may change SBX priorities |

Yusra and Saleha are deliberately **not** in either group, so they can produce the negative cases.
CodeAtlas reads group names from `.codeatlas/policy.yaml` in the target repo. It never hard-codes
them.

Membership is checked as it is **now** (Jira has no membership history API). Don't change these
groups after the demo tickets are created.

## Consequences

- The meaning of "approved" lives in configuration (status name + group name). Another team can
  reuse CodeAtlas by changing `policy.yaml`, without changing code.
- The Jira collector must fetch the issue changelog (`/rest/api/3/issue/{key}/changelog`) and
  group members (`/rest/api/3/group/member`). Status alone is not enough.
- If someone with the right status permission but outside the group moves a ticket to
  `Approved`, the ticket shows up as *not approved*. That is intended.
