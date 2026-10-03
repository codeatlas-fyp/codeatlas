# Jira sandbox setup (Areej, Day 1)

Step-by-step checklist for the Jira side of the demo. Tick the steps off as you go. The rules
behind it are in [approval-convention.md](../decisions/approval-convention.md).

## 1. Site and people

- [ ] Create a free Jira Cloud site (e.g. `codeatlas-fyp.atlassian.net`).
- [ ] Invite Yusra and Saleha (*Settings → User management*).

## 2. Projects

- [ ] **CA**: a team-managed **Scrum** project for our own work. Create `CA-1` to `CA-12`.
- [ ] **SBX**: a **company-managed** project (*Create project → Software → Kanban → choose
  "company-managed"*). Company-managed lets you control its workflow and fields.

## 3. SBX workflow

*Settings → Issues → Workflows* → copy the SBX workflow and edit it:

```
To Do → Ready for Approval → Approved → In Progress → In Review → Done
```

- [ ] Add the statuses and transitions above. Use the status name `Approved` exactly.
- [ ] On the transition **Ready for Approval → Approved**, add the condition
  *User Is In Group: `sbx-requirement-approvers`*. If your plan doesn't offer that condition,
  skip it. The convention doc already covers this case.
- [ ] Attach the workflow to SBX through a workflow scheme.

## 4. Field and groups

- [ ] Custom field: *Settings → Issues → Custom fields → Create → Paragraph*, named
  **`Acceptance Criteria`**. Add it to the SBX create/edit/view screens.
- [ ] Groups (*Settings → User management → Groups*):
  - `sbx-requirement-approvers`: Areej only
  - `sbx-priority-authority`: Areej only
- [ ] Note the field's ID (`customfield_100xx`). The Jira collector needs it. You can find it at
  `https://<site>/rest/api/3/field`.

## 5. API token

- [ ] Create one at https://id.atlassian.com/manage-profile/security/api-tokens
- [ ] Store it in the shared password manager. **Never put it in the repo.**
- [ ] Ask Saleha to add the GitHub Actions secrets `JIRA_EMAIL`, `JIRA_API_TOKEN` and
  `JIRA_BASE_URL`.

## 6. Scenario tickets

Create them **in this order**, so the keys come out as SBX-1 … SBX-5. Paste the summary,
description and Acceptance Criteria as written.

### SBX-1: PASS

- **Summary:** Authorize card payments up to a configurable limit
- **Description:** `authorize_payment` must reject amounts above the merchant's limit and record an
  audit entry for every decision.
- **Acceptance Criteria:**
  1. Amounts ≤ limit return an approved authorization with an id.
  2. Amounts > limit are declined with reason `LIMIT_EXCEEDED`.
  3. Every decision writes an audit entry via `sandbox.audit.logger.audit_entry`.
  4. Unit tests cover the approve and decline paths.
- **State now:** `To Do → Ready for Approval → Approved`. **Areej** makes the Approved
  transition.

### SBX-2: FAIL (not approved)

- **Summary:** Support partial refunds
- **Description:** `refund` should accept an amount smaller than the original payment.
- **Acceptance Criteria:**
  1. A partial refund ≤ the remaining balance succeeds.
  2. A refund above the remaining balance is rejected.
  3. Each refund writes an audit entry.
- **State now:** move to `Ready for Approval` and **leave it there**.

### SBX-3: REVIEW (AC changes after implementation)

- **Summary:** Reject expired authentication tokens
- **Description:** `verify_token` must reject tokens whose expiry time has passed.
- **Acceptance Criteria (initial):**
  1. A token with `exp` in the past is rejected with `TokenExpired`.
  2. A valid token returns its subject.
- **State now:** Approved by **Areej**.
- **Day 2, after Yusra's first commit for SBX-3:** edit the AC and add
  `3. Allow 30 seconds of clock skew.` Don't re-approve.

### SBX-4: REVIEW (governance change) and R3 data

- **Summary:** Hand over audit module ownership to the payments team
- **Description:** Update `CODEOWNERS` so `/sandbox/audit/` is owned by the payments owner, and
  record the reason in an ADR.
- **Acceptance Criteria:**
  1. `CODEOWNERS` lists the new owner for `/sandbox/audit/`.
  2. A new ADR explains the change.
- **State now:** Approved by **Areej**.
- **Then:** log in as **Yusra or Saleha** (not in `sbx-priority-authority`) and change the
  priority, e.g. Medium → High. This gives rule R3 an unauthorised change to find.

### SBX-5: UNKNOWN (test evidence required)

- **Summary:** Include request id in audit entries
- **Description:** `audit_entry` should store the caller's request id so entries can be correlated.
- **Acceptance Criteria:**
  1. `audit_entry` accepts a `request_id` and stores it.
  2. **Test evidence required:** a passing CI run that covers `audit_entry` with a request id.
- **State now:** Approved by **Areej**.

## 7. Done when

- [ ] All five tickets exist and show the right history (open each one → *History* tab).
- [ ] SBX-4 has a priority change made by someone outside `sbx-priority-authority`.
- [ ] Someone else reads the convention doc and understands it without asking you.
