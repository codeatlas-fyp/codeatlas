"""JSON-lines event log with redaction (spec §15 "Logging", §16; step-8 spec).

Each line holds `ts`, `level`, `event`, `run_id` and any fields given; stages also log `stage`
and `duration_ms`. Values of fields whose name contains token, authorization, password or secret,
and every email address anywhere, are replaced by `[redacted]` before anything is written.
"""

import json
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TextIO

REDACTED = "[redacted]"
_SENSITIVE = ("token", "authorization", "password", "secret")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def redact(key: str, value: Any) -> Any:
    if any(word in key.lower() for word in _SENSITIVE):
        return REDACTED
    if isinstance(value, str):
        return _EMAIL.sub(REDACTED, value)
    if isinstance(value, dict):
        return {k: redact(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(key, v) for v in value]
    return value


class EventLog:
    def __init__(self, run_id: str, *, stream: TextIO | None = None, level: str = "INFO") -> None:
        self.run_id = run_id
        self._stream = stream or sys.stderr
        self._threshold = _LEVELS.get(level.upper(), 20)

    def event(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        if _LEVELS.get(level, 20) < self._threshold:
            return
        record = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": level,
            "event": redact("event", event),
            "run_id": self.run_id,
            **{key: redact(key, value) for key, value in fields.items()},
        }
        self._stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._stream.flush()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Log the start and end (with duration) of a pipeline stage; failures are logged too."""
        started = time.perf_counter()
        self.event("stage_start", stage=name)
        try:
            yield
        except Exception as error:
            self.event(
                "stage_failed",
                level="ERROR",
                stage=name,
                error=type(error).__name__,
                message=str(error),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        self.event(
            "stage_end", stage=name, duration_ms=round((time.perf_counter() - started) * 1000)
        )
