"""Tests for docs/specs/step-5-jira-collector.md AC9-AC10 (sanitiser, committed fixtures).

The input here is a Jira-shaped dict built in code with invented people, to test the sanitiser
itself; it is not a fixture.
"""

import json
import re
from pathlib import Path
from typing import Any

from codeatlas.collect.jira.recorder import Sanitiser

FIXTURES = Path(__file__).parents[1] / "fixtures" / "jira"
FAKE = "example.atlassian.net"
SITE = "acme-real.atlassian.net"


def person(account_id: str, name: str, email: str) -> dict[str, Any]:
    return {
        "self": f"https://{SITE}/rest/api/3/user?accountId={account_id}",
        "accountId": account_id,
        "emailAddress": email,
        "avatarUrls": {"48x48": f"https://avatar-management.example/{account_id}/48"},
        "displayName": name,
        "active": True,
        "timeZone": "Asia/Karachi",
        "accountType": "atlassian",
    }


ALICE = person("712020:aaaa-1111", "Alice Real", "alice.real@gmail.com")
BOB = person("712020:bbbb-2222", "Bob Real", "bob@corp.com")


def issue() -> dict[str, Any]:
    return {
        "self": f"https://{SITE}/rest/api/3/issue/10001",
        "key": "SBX-9",
        "fields": {
            "summary": "Renew a loan",
            "reporter": ALICE,
            "assignee": BOB,
            "description": {"type": "doc", "content": [{"type": "text", "text": "Ask Alice Real"}]},
        },
    }


def changelog() -> list[dict[str, Any]]:
    return [
        {
            "id": "1",
            "author": BOB,
            "created": "2026-10-07T16:27:48.950+0500",
            "items": [
                {
                    "field": "assignee",
                    "from": "712020:aaaa-1111",
                    "fromString": "Alice Real",
                    "to": "712020:bbbb-2222",
                    "toString": "Bob Real",
                }
            ],
        }
    ]


def all_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *all_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in all_strings(v)]
    return [str(value)]


def test_ac9_no_personal_value_survives() -> None:
    sanitiser = Sanitiser(site_host=SITE)

    out = json.dumps([sanitiser.sanitise(issue()), sanitiser.sanitise(changelog())])

    for secret in ("aaaa-1111", "bbbb-2222", "Alice Real", "Bob Real", "gmail.com", "corp.com"):
        assert secret not in out
    assert SITE not in out
    assert "avatar-management" not in out


def test_ac9_same_person_gets_the_same_fake_everywhere() -> None:
    sanitiser = Sanitiser(site_host=SITE)

    fields = sanitiser.sanitise(issue())["fields"]
    (history,) = sanitiser.sanitise(changelog())
    item = history["items"][0]

    assert fields["reporter"]["accountId"] == "user-01"
    assert fields["reporter"]["displayName"] == "User 01"
    assert fields["reporter"]["emailAddress"] == "user-01@example.test"
    assert fields["assignee"]["accountId"] == history["author"]["accountId"] == "user-02"
    assert (item["from"], item["fromString"]) == ("user-01", "User 01")
    assert (item["to"], item["toString"]) == ("user-02", "User 02")
    assert fields["description"]["content"][0]["text"] == "Ask User 01"
    assert fields["reporter"]["self"].startswith("https://example.atlassian.net/")


def test_ac9_structure_and_other_text_unchanged() -> None:
    original = issue()

    out = Sanitiser(site_host=SITE).sanitise(original)

    assert out["key"] == "SBX-9"
    assert out["fields"]["summary"] == "Renew a loan"
    assert out["fields"]["reporter"]["timeZone"] == "Asia/Karachi"
    assert out["fields"]["reporter"]["active"] is True
    assert set(out["fields"]["reporter"]) == set(original["fields"]["reporter"])
    assert original["fields"]["reporter"]["displayName"] == "Alice Real"  # input not mutated


def test_ac9_known_mapping_keeps_fakes_stable_across_runs() -> None:
    first = Sanitiser(site_host=SITE)
    first.sanitise(changelog())  # Bob is seen first here

    second = Sanitiser(site_host=SITE, known=first.mapping())

    assert second.sanitise(issue())["fields"]["assignee"]["accountId"] == "user-01"
    assert second.sanitise(issue())["fields"]["reporter"]["accountId"] == "user-02"


# --- AC10: committed fixtures hold no personal data ---------------------------------------------

EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")
HOST = re.compile(r"[\w-]+\.atlassian\.net")


URL_ACCOUNT = re.compile(r"accountId=[0-9]+%3A")


def test_ac10_committed_jira_fixtures_are_sanitised() -> None:
    files = sorted(FIXTURES.rglob("*.json"))

    assert files, "no recorded Jira fixtures"
    for file in files:
        text = file.read_text(encoding="utf-8")
        assert not URL_ACCOUNT.search(text), file.name
        for match in EMAIL.finditer(text):
            assert match.group().endswith("@example.test"), (file.name, match.group())
        for match in HOST.finditer(text):
            assert match.group() == "example.atlassian.net", (file.name, match.group())
        for value in all_strings(json.loads(text)):
            assert not value.startswith("712020:"), file.name


# --- finding: account ids inside URLs (self links) must not survive -----------------------------


def test_url_encoded_account_id_in_self_link_is_replaced() -> None:
    raw = "712020:b7db8c30-0831-4bf5-bf14-e0263ca25279"
    encoded = "712020%3Ab7db8c30-0831-4bf5-bf14-e0263ca25279"
    issue = {"fields": {"assignee": person(raw, "Real Name", "real@corp.test")}}

    out = Sanitiser(site_host=SITE).sanitise(issue)
    text = json.dumps(out)

    assert raw not in text
    assert encoded not in text
    assert out["fields"]["assignee"]["self"].endswith("accountId=user-01")


def test_scrub_fixes_recorded_urls_without_learning_people() -> None:
    encoded = "712020%3Ab7db8c30-0831-4bf5-bf14-e0263ca25279"
    recorded = {
        "author": {
            "self": f"https://{FAKE}/rest/api/3/user?accountId={encoded}",
            "accountId": "user-01",
            "emailAddress": "user-01@example.test",
        }
    }

    out = Sanitiser(site_host=SITE).scrub(recorded)

    assert out["author"]["self"] == f"https://{FAKE}/rest/api/3/user?accountId=user-01"
    assert out["author"]["accountId"] == "user-01"
    assert out["author"]["emailAddress"] == "user-01@example.test"


def test_stray_account_id_outside_a_person_is_neutralised() -> None:
    stray = {
        "note": "see https://x.example/user?accountId=712020%3Aaaaaaaa-1111-2222-3333-444444444444"
    }

    out = Sanitiser(site_host=SITE).scrub(stray)

    assert "712020" not in json.dumps(out)
    assert "accountId=user-00" in out["note"]
