"""Tests for docs/specs/step-8-pipeline-cli.md: configuration loading (part of AC8)."""

from pathlib import Path

import pytest

from codeatlas.config import (
    DEFAULT_POLICY,
    Settings,
    load_change,
    load_identity_map,
    load_policy,
)
from codeatlas.errors import ConfigError

ROOT = Path(__file__).parents[2]


def test_example_policy_and_identity_map_load() -> None:
    policy, notes = load_policy(ROOT / "config" / "policy.example.yaml")
    identity = load_identity_map(ROOT / "config" / "identity_map.example.yaml")

    assert notes == []
    assert policy.approval.status == "Approved"
    assert policy.approval.approver_account_ids == ["user-01"]
    assert policy.priority_authority.account_ids == ["user-01"]
    assert [p.jira_account_id for p in identity.people] == ["user-01", "user-02"]


def test_missing_policy_uses_defaults_and_says_so() -> None:
    policy, notes = load_policy(None)

    assert policy == DEFAULT_POLICY
    assert notes == ["no policy file: built-in default policy used"]


def test_no_identity_map_is_empty() -> None:
    assert load_identity_map(None).people == []


@pytest.mark.parametrize(
    "text",
    ["version: [", "version: 1\n", "- just\n- a list\n", "approval: {status: Approved}\n"],
    ids=["bad-yaml", "missing-keys", "not-a-mapping", "no-approvers"],
)
def test_invalid_policy_is_a_config_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError) as raised:
        load_policy(path)

    assert raised.value.exit_code == 4
    assert "policy.yaml" in str(raised.value)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_policy(tmp_path / "absent.yaml")


def test_change_file_loads_and_rejects_unknown_fields(tmp_path: Path) -> None:
    good = tmp_path / "change.json"
    good.write_text(
        '{"repo": "o/r", "pr_number": 1, "base_sha": "a", "head_sha": "b", '
        '"merge_base_sha": "c", "work_items": [{"key": "SBX-1", "found_in": "pr_title"}], '
        '"commits": []}',
        encoding="utf-8",
    )
    bad = tmp_path / "bad.json"
    bad.write_text(good.read_text(encoding="utf-8").replace('"repo"', '"repository"'))

    assert load_change(good).work_items[0].key == "SBX-1"
    with pytest.raises(ConfigError, match="bad.json"):
        load_change(bad)


def test_settings_name_the_missing_jira_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEATLAS_JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("CODEATLAS_JIRA_EMAIL", "user-01@example.test")
    monkeypatch.delenv("CODEATLAS_JIRA_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="CODEATLAS_JIRA_TOKEN") as raised:
        Settings(_env_file=None).jira()  # type: ignore[call-arg]

    assert raised.value.exit_code == 4


def test_settings_token_is_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEATLAS_JIRA_TOKEN", "very-secret-token")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "very-secret-token" not in repr(settings)
    assert "very-secret-token" not in str(settings.model_dump())
