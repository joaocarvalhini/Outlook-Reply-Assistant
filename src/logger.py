"""Structured logging.

Every log line is a single, greppable record:

    2026-08-06T09:14:02 | INFO    | reply_assistant.triage | rejected | rule=header

Fields passed through `extra=` are appended as `key=value` pairs, so operational
data stays machine-readable without pulling in a logging framework. This daemon
runs unattended, so the log is the only account of what it did to the mailbox.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

__all__ = ["LOGGER_ROOT", "StructuredFormatter", "configure_logging", "get_logger"]

LOGGER_ROOT = "reply_assistant"

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Attributes present on every LogRecord; anything else was supplied via `extra`.
_RESERVED_FIELDS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__
) | {"message", "asctime", "taskName"}


def _render_value(value: Any) -> str:
    text = str(value)
    if text == "" or any(char.isspace() for char in text):
        return f'"{text}"'
    return text


class StructuredFormatter(logging.Formatter):
    """Human-readable formatter that appends `extra=` fields as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, _TIME_FORMAT),
            f"{record.levelname:<7}",
            record.name,
            record.getMessage(),
        ]
        line = " | ".join(parts)

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_FIELDS
        }
        if extras:
            rendered = " ".join(
                f"{key}={_render_value(value)}" for key, value in sorted(extras.items())
            )
            line = f"{line} | {rendered}"

        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.WARNING


def configure_logging(level: str | int = "INFO", log_file: Path | None = None) -> None:
    """Install handlers on the package logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_ROOT)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(_resolve_level(level))
    console.setFormatter(StructuredFormatter())
    logger.addHandler(console)

    if log_file is None:
        return

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
    except OSError as exc:
        logger.warning("log file unavailable", extra={"path": str(log_file), "error": exc})
        return

    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child of the package logger."""
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")
