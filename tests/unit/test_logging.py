"""Tests for docs/specs/step-8-pipeline-cli.md AC9: JSON-lines logging with redaction."""

import io
import json

import pytest

from codeatlas.logging import EventLog, redact


def lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_ac9_token_and_email_are_redacted() -> None:
    stream = io.StringIO()
    log = EventLog("run-1", stream=stream)

    log.event(
        "request",
        jira_token="very-secret-token",
        Authorization="Basic abc",
        note="sent by alice.real@gmail.com",
    )

    text = stream.getvalue()
    assert "very-secret-token" not in text
    assert "Basic abc" not in text
    assert "alice.real@gmail.com" not in text
    (record,) = lines(stream)
    assert record["jira_token"] == "[redacted]"
    assert record["note"] == "sent by [redacted]"


def test_records_have_the_spec_fields() -> None:
    stream = io.StringIO()
    log = EventLog("run-7", stream=stream)

    with log.stage("collect"):
        pass

    start, end = lines(stream)
    assert (start["event"], end["event"]) == ("stage_start", "stage_end")
    assert start["run_id"] == end["run_id"] == "run-7"
    assert start["stage"] == "collect"
    assert isinstance(end["duration_ms"], int)
    assert str(start["ts"]).endswith("Z")
    assert start["level"] == "INFO"


def test_failed_stage_is_logged_and_reraised() -> None:
    stream = io.StringIO()
    log = EventLog("run-1", stream=stream)

    with pytest.raises(ValueError, match="boom"), log.stage("freeze"):
        raise ValueError("boom")

    assert lines(stream)[-1]["event"] == "stage_failed"
    assert lines(stream)[-1]["error"] == "ValueError"


def test_level_filters_debug() -> None:
    stream = io.StringIO()
    log = EventLog("run-1", stream=stream, level="INFO")

    log.event("noise", level="DEBUG")

    assert stream.getvalue() == ""


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("password", "x", "[redacted]"),
        ("client_secret", "x", "[redacted]"),
        ("count", 3, 3),
        ("message", "mail user-01@example.test now", "mail [redacted] now"),
        ("nested", {"token": "x"}, {"token": "[redacted]"}),
    ],
)
def test_redact(key: str, value: object, expected: object) -> None:
    assert redact(key, value) == expected
