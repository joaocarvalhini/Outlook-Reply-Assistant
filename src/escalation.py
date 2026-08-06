"""Escalation detection.

The drafter signals "I cannot answer this" with a single line:

    ESCALATE: <reason>

That line is a parsed contract, not prose. Detection tolerates the model
wrapping the marker in markdown or putting the reason on the next line, but it
never invents a reason where the marker is absent — a missing marker means the
model produced a real reply, and treating that as an escalation would silently
throw away work.
"""

from __future__ import annotations

from typing import Final

from .utils import normalize_whitespace, truncate

__all__ = ["ESCALATION_MARKER", "DEFAULT_REASON", "detect_escalation"]

ESCALATION_MARKER: Final[str] = "ESCALATE:"
DEFAULT_REASON: Final[str] = "Assistente sinalizou baixa confianca sem indicar motivo."
MAX_REASON_LENGTH: Final[int] = 200

# Leading characters a model may add around the marker (markdown, quotes, lists).
_DECORATION = "*_`>#-\"' \t"


def _clean_reason(reason: str) -> str:
    reason = normalize_whitespace(reason).strip(_DECORATION)
    if not reason:
        return DEFAULT_REASON
    return truncate(reason, MAX_REASON_LENGTH)


def detect_escalation(text: str) -> str | None:
    """Return the escalation reason, or None if `text` is a real reply."""
    if not text or ESCALATION_MARKER not in text.upper():
        return None

    lines = text.splitlines()
    for index, line in enumerate(lines):
        candidate = line.strip().lstrip(_DECORATION)
        if not candidate.upper().startswith(ESCALATION_MARKER):
            continue

        reason = candidate[len(ESCALATION_MARKER) :]
        if not reason.strip():
            reason = next(
                (following for following in lines[index + 1 :] if following.strip()), ""
            )
        return _clean_reason(reason)

    return None
