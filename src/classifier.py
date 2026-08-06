"""Stage one: decide whether an email deserves a reply at all.

Cheap by design. It runs on every message that survives triage, sees no
knowledge base, and returns a structured verdict rather than prose -- the schema
is enforced server-side, so `should_reply` is a real boolean and the category is
always one of ours. Nothing downstream parses free text.

Failures raise. The poll loop owns the single place where any failure becomes
"flag it for a human", so this module does not need an opinion about recovery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .logger import get_logger
from .models import Category, Classification, EmailMessage, TokenUsage, Urgency
from .prompts import CLASSIFICATION_SCHEMA, ClassifierPromptBuilder, render_email
from .utils import truncate

__all__ = ["Classifier", "ClassifierError", "MessagesClient"]

_LOG = get_logger("classifier")

# Enough for the JSON verdict and nothing more.
_MAX_TOKENS = 256


class MessagesClient(Protocol):
    """The slice of `anthropic.Anthropic().messages` this package depends on."""

    def create(self, **kwargs: Any) -> Any: ...


class ClassifierError(RuntimeError):
    """Raised when no usable verdict could be produced."""


@dataclass(frozen=True, slots=True)
class Classifier:
    """Sorts inbound mail into one of seven categories."""

    client: MessagesClient
    model: str
    prompts: ClassifierPromptBuilder
    max_body_chars: int = 2000

    def classify(self, email: EmailMessage) -> Classification:
        """Return the verdict for `email`. Raises `ClassifierError`."""
        system = self.prompts.build()

        try:
            response = self.client.create(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                output_config={
                    "format": {"type": "json_schema", "schema": CLASSIFICATION_SCHEMA}
                },
                messages=[
                    {"role": "user", "content": render_email(email, self.max_body_chars)}
                ],
            )
        except Exception as exc:
            raise ClassifierError(f"{type(exc).__name__}: {exc}") from exc

        verdict = self._parse(response)
        _LOG.info(
            "classified",
            extra={
                "message": email.id[:16],
                "category": verdict.category.value,
                "reply": verdict.should_reply,
                "urgency": verdict.urgency.value,
            },
        )
        return verdict

    # -- internals ----------------------------------------------------------- #

    def _parse(self, response: Any) -> Classification:
        text = self._first_text(response)
        if not text:
            raise ClassifierError("Model returned an empty response")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClassifierError(f"Model returned invalid JSON: {truncate(text, 120)}") from exc

        try:
            category = Category(payload["categoria"])
            urgency = Urgency(payload["urgencia"])
            should_reply = bool(payload["responder"])
        except (KeyError, ValueError) as exc:
            raise ClassifierError(f"Verdict did not match the schema: {exc}") from exc

        # The schema cannot express this, and a `should_reply` that contradicts
        # the category would send a newsletter to the drafter. The category is
        # the more reliable signal, so it wins.
        if should_reply and category not in (Category.CUSTOMER_REQUEST, Category.PRE_SALES):
            _LOG.warning(
                "verdict overridden", extra={"category": category.value, "reply": True}
            )
            should_reply = False

        return Classification(
            category=category,
            should_reply=should_reply,
            urgency=urgency,
            reason=str(payload.get("motivo", "")).strip(),
            usage=TokenUsage.from_response(response),
        )

    @staticmethod
    def _first_text(response: Any) -> str:
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", "")).strip()
        return ""
