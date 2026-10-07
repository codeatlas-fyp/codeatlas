# Step 5: Jira collector, ADF and recorder

- **Owner:** Areej (@areej8)
- **Step:** 5 of the mid-evaluation spec (section 10)
- **Prerequisites:** G1 (steps 2–4)
- **Spec sections:** 5 (stage 4), 6 (`collect.jira`), 7.2 (`WorkItemSource`), 13 (integration
  tests on recordings, network blocked), 15 (errors), 16 (read-only client, personal data)
- **Branch:** `feature/jira-collector`, stacked on `feature/rule-framework`

## Purpose

Read one Jira issue, its full changelog and the members of a group, read-only, and record those
responses so every later test runs from real data with the network blocked.

## What the real SBX project returns (recorded 2026-10-07, `.recordings/spike/`, not committed)

These facts come from real responses, not assumptions (CLAUDE.md: "cite a recorded response").

| Fact | Evidence |
|---|---|
| `GET /rest/api/3/issue/{key}?fields=*all` returns `description` as ADF (`{"type":"doc",...}`) | SBX-1..7 issue responses |
| The acceptance criteria are inside the description (heading "Acceptance criteria" + ordered list); there is no separate custom field | SBX-6 issue; only `customfield_10019` (rank) is non-empty |
| `GET /rest/api/3/issue/{key}/changelog` is a page: `startAt`, `maxResults`, `total`, `isLast`, `values` | all seven changelog responses |
| Changelog items carry `field`, `fieldId`, `fieldtype`, `from`, `fromString`, `to`, `toString` | SBX-6 histories 10093–10095 |
| For `description`, `fromString`/`toString` are **wiki markup**, not ADF: `h2. Acceptance criteria`, `# item` | SBX-6 histories 10093–10095 |
| History `author` and issue `reporter` carry `accountId`, `displayName`, **`emailAddress`**, `avatarUrls` | SBX-6 |
| `GET /rest/api/3/group/member?groupname=sbx-requirement-approvers` returns 404 "group … does not exist" | spike |
| `GET /rest/api/3/issue/{key}/comment` is a page with `startAt`, `maxResults`, `total`, `comments` (no `isLast`) | SBX-6 comments |
| Responses carry `X-RateLimit-Limit` / `X-RateLimit-Remaining` headers | `GET /myself` |

Rate limiting, checked against Atlassian's official page "Rate limiting" for Jira Cloud
(developer.atlassian.com/cloud/jira/platform/rate-limiting, read 2026-10-07): Jira returns
`429 Too Many Requests`; `Retry-After` is "only returned with 429 responses" and "indicates how
many seconds to wait"; apps should use "exponential backoff with jitter", e.g. a 2-second base
delay doubled per retry, jitter factor 0.7–1.3, about 4 attempts. The client does exactly this:
it honours `Retry-After` when present, otherwise waits 2, 4, 8 s times a jitter factor, and makes
at most 4 attempts.

## Inputs and outputs

| Item | Signature |
|---|---|
| `collect.jira.client.JiraClient(base_url, email, token, *, transport=None, sleep=time.sleep, jitter=random 0.7–1.3, max_retries=3)` | implements `WorkItemSource` |
| `JiraClient.fetch_issue(key)` | `-> dict` raw issue (`fields=*all`) |
| `JiraClient.fetch_changelog(key)` | `-> list[dict]` every history, all pages, oldest first |
| `JiraClient.group_members(group)` | `-> set[str]` account ids, all pages |
| `JiraClient.fetch_comments(key)` | `-> list[dict]` every comment, all pages (recorded; no rule uses comments at mid) |
| `collect.jira.adf.adf_to_text(value)` | ADF, string or `None` `-> str`; never raises on malformed ADF |
| `collect.jira.adf.wiki_to_text(value)` | changelog wiki markup or `None` `-> str` in the same plain form as `adf_to_text` |
| `collect.jira.recorder.record(source, keys, out_dir)` | writes `<KEY>.issue.json`, `<KEY>.changelog.json`, `<KEY>.comments.json` |
| `collect.jira.recorder.Sanitiser` | replaces account ids, names, emails, avatar and site URLs with consistent fakes |
| `collect.jira.recorder.FixtureJiraSource(folder)` | `WorkItemSource` reading recorded files |

### Plain-text form (shared by `adf_to_text` and `wiki_to_text`)

One block per line, no blank lines, trailing spaces removed:
paragraphs and headings as their text; ordered list items as `1. item` (numbered from the list's
start); bullet items as `- item`; hard breaks as newlines; mentions and emoji as their text;
links as their URL; table rows as `cell | cell`; media as `[image]`.
Wiki markup handled (the subset seen in recordings plus bullets): `hN. text` headings, `# item`
ordered items, `* item` bullets. Other wiki markup is kept as written.

## Error handling (all mapped to `errors.py`, nothing above `collect/` sees httpx)

| Response | Behaviour | Error / exit |
|---|---|---|
| 200 | parse JSON | — |
| 429 | wait `Retry-After` seconds (or backoff 2, 4, 8 s × jitter), retry up to `max_retries` = 3 | then `SourceUnavailable`, exit 3 |
| 500–599 | backoff 2, 4, 8 s × jitter, retry up to `max_retries` = 3 | then `SourceUnavailable`, exit 3 |
| timeout / connection error | backoff and retry | then `SourceUnavailable`, exit 3 |
| 401, 403 | no retry | `CollectionError(status, retryable=False)`, exit 3 |
| 404 | no retry | `CollectionError(status=404)`; the pipeline turns a missing issue into a collection note (step 8) |
| body is not JSON | no retry | `CollectionError`, exit 3 |
| non-GET request | refused by the transport before sending | `CollectionError("read-only")` |

## Acceptance criteria

- **AC1** Requests go to `<base>/rest/api/3/...` with Basic auth and are GET only; a non-GET
  request through the client's transport is refused (spec §16).
- **AC2 (pagination)** Changelog, group members and comments follow pages until `isLast` is true or
  a page is empty, and return every value in server order.
- **AC3 (429)** A 429 with `Retry-After: 7` waits exactly 7 s (injected sleep) and then succeeds;
  repeated 429s end in `SourceUnavailable` with exit code 3.
- **AC4 (500, timeout)** 5xx and timeouts are retried with backoff 2, 4, 8 s (jitter fixed to 1 in
  tests), then
  `SourceUnavailable`, exit 3.
- **AC5 (401, 404)** 401 raises `CollectionError` with status 401 and `retryable=False` (wrong
  token → exit 3) without retrying; 404 raises status 404.
- **AC6 (ADF)** Each node type in the plain-text form converts as specified; `None` → `""`.
- **AC7 (malformed ADF)** Unknown node types, missing `content`, non-dict nodes and non-string
  text never raise; known text is kept.
- **AC8 (wiki)** The SBX-6 changelog strings convert to the same text as the ADF converter gives
  for the same content (headings and numbered items).
- **AC9 (sanitiser)** No account id, display name, email, avatar URL or site host from the input
  survives; the same person always gets the same fake (`user-01`, `User 01`,
  `user-01@example.test`); structure and non-personal text are unchanged.
- **AC10 (fixtures)** Every file under `tests/fixtures/jira/` contains no email outside
  `example.test` and no `atlassian.net` host other than `example.atlassian.net`.
- **AC11 (integration, network blocked)** `FixtureJiraSource` over the recorded SBX fixtures
  returns the issue, three SBX-6 description histories in order, and no error.

## Fixtures (recorded, then sanitised)

| File | From | Expected content |
|---|---|---|
| `tests/fixtures/jira/SBX-1..7.issue.json` | live SBX, 2026-10-07 | summary, ADF description, labels `scenario-*` |
| `tests/fixtures/jira/SBX-1..7.changelog.json` | live SBX | SBX-6: 3 description histories; others: as recorded on the day (may be empty) |
| `tests/fixtures/jira/SBX-1..7.comments.json` | live SBX | as recorded |

SBX-1..5 and SBX-7 had no history yet on 2026-10-07 (their scenario actions are not done); they
are re-recorded once the owner completes them. This is listed in the evaluation guide.

## Files to touch

- `src/codeatlas/collect/jira/{__init__,client,adf,recorder}.py`
- `scripts/record_jira.py` (records live → `.recordings/raw/`, sanitises → `tests/fixtures/jira/`)
- `tests/unit/test_jira_client.py`, `tests/unit/test_adf.py`, `tests/unit/test_sanitiser.py`,
  `tests/integration/test_jira_recordings.py`
- `.gitignore` (already ignores `.recordings/`)

## Out of scope

- Version reconstruction, approvals, chain check (step 6).
- Turning a 404 into `collection_notes` (pipeline, step 8).
- Writing to Jira (the scenario generator, after step 7).
