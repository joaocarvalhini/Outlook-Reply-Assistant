"""Stage two: write the reply, or refuse to.

The drafter sees the knowledge base and one email, and produces exactly one of
two things: a reply body grounded in the documents, or a single `ESCALATE:` line
naming what is missing. There is no third path where it improvises.

Truncation and empty responses are treated as escalations rather than partial
answers -- a reply cut off mid-sentence is worse than no reply, because it looks
finished enough to approve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .classifier import MessagesClient
from .escalation import detect_escalation
from .logger import get_logger
from .models import DraftOutcome, EmailMessage, KnowledgeBase, Outcome, TokenUsage
from .prompts import ReplyPromptBuilder, render_email
from .utils import sanitize_html

__all__ = ["Drafter", "DrafterError"]

_LOG = get_logger("drafter")

_REASON_EMPTY = "Modelo devolveu uma resposta vazia."
_REASON_TRUNCATED = "Resposta excedeu o limite de tokens e ficou incompleta."
_REASON_UNUSABLE = "Resposta do modelo nao continha HTML utilizavel."


class DrafterError(RuntimeError):
    """Raised when the model could not be reached or errored."""


@dataclass(slots=True)
class Drafter:
    """Produces a grounded reply body for one email."""

    client: MessagesClient
    model: str
    prompts: ReplyPromptBuilder
    knowledge_base: KnowledgeBase
    max_tokens: int = 1024
    max_body_chars: int = 4000
    _system: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Built once: this string is the cached prefix of every request, so it
        # must be byte-stable for the life of the process.
        self._system = self.prompts.build(self.knowledge_base)

    def draft(self, email: EmailMessage) -> DraftOutcome:
        """Return a reply body or an escalation. Raises `DrafterError`."""
        try:
            response = self.client.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": self._system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": render_email(email, self.max_body_chars)}
                ],
            )
        except Exception as exc:
            raise DrafterError(f"{type(exc).__name__}: {exc}") from exc

        return self._interpret(response)

    # -- internals ----------------------------------------------------------- #

    def _interpret(self, response: Any) -> DraftOutcome:
        usage = TokenUsage.from_response(response)
        text = self._first_text(response)

        if not text:
            return DraftOutcome(Outcome.ESCALATED, reason=_REASON_EMPTY, usage=usage)

        if getattr(response, "stop_reason", None) == "max_tokens":
            return DraftOutcome(Outcome.ESCALATED, reason=_REASON_TRUNCATED, usage=usage)

        reason = detect_escalation(text)
        if reason is not None:
            _LOG.info("escalated by model", extra={"reason": reason})
            return DraftOutcome(Outcome.ESCALATED, reason=reason, usage=usage)

        # Sanitising happens here rather than at the Graph boundary so that an
        # unusable body is caught as an escalation, not written as an empty draft.
        html = sanitize_html(text)
        if not html:
            return DraftOutcome(Outcome.ESCALATED, reason=_REASON_UNUSABLE, usage=usage)

        return DraftOutcome(Outcome.DRAFTED, html=html, usage=usage)

    @staticmethod
    def _first_text(response: Any) -> str:
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", "")).strip()
        return ""
