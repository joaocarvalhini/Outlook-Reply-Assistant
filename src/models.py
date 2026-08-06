"""Immutable domain types.

Everything here is a frozen dataclass: a message moves through the pipeline
(Graph -> triage -> classifier -> drafter -> Graph) without any stage mutating
another's state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

__all__ = [
    "Category",
    "Classification",
    "Document",
    "DraftOutcome",
    "EmailMessage",
    "KnowledgeBase",
    "Outcome",
    "TokenUsage",
    "TriageVerdict",
    "Urgency",
]


class Category(str, Enum):
    """What kind of email this is. Only the first two ever earn a reply."""

    CUSTOMER_REQUEST = "pedido_cliente"
    PRE_SALES = "pre_venda"
    NEWSLETTER = "newsletter"
    SYSTEM_NOTIFICATION = "notificacao_sistema"
    SPAM = "spam"
    SUPPLIER = "fornecedor"
    INTERNAL = "interno"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class Urgency(str, Enum):
    LOW = "baixa"
    MEDIUM = "media"
    HIGH = "alta"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class Outcome(str, Enum):
    """What the pipeline did with one message."""

    DRAFTED = "drafted"
    ESCALATED = "escalated"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Document:
    """One cleaned knowledge-base file."""

    name: str
    path: Path
    content: str

    @property
    def char_count(self) -> int:
        return len(self.content)

    def as_context(self) -> str:
        """Wrap the document in tags so the model can cite boundaries clearly."""
        return f'<documento nome="{self.name}">\n{self.content}\n</documento>'


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    """The complete set of documents the assistant is allowed to draw on."""

    documents: tuple[Document, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.documents

    @property
    def total_chars(self) -> int:
        return sum(document.char_count for document in self.documents)

    def as_context(self) -> str:
        return "\n\n".join(document.as_context() for document in self.documents)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One inbound message, as much of it as the pipeline needs.

    `headers` is empty until the detail fetch runs — the list query deliberately
    does not ask for them, since most messages are rejected before that point.
    """

    id: str
    conversation_id: str
    internet_message_id: str
    subject: str
    sender: str
    sender_name: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    received_at: str
    body: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    categories: tuple[str, ...] = ()

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup. Returns the first match."""
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return None

    def has_header(self, name: str) -> bool:
        return self.header(name) is not None

    @property
    def sender_domain(self) -> str:
        _, _, domain = self.sender.partition("@")
        return domain.lower()

    @property
    def recipients(self) -> tuple[str, ...]:
        return self.to + self.cc


@dataclass(frozen=True, slots=True)
class TriageVerdict:
    """The outcome of the deterministic filters. No model was involved."""

    accepted: bool
    rule: str = ""
    reason: str = ""

    @classmethod
    def accept(cls) -> TriageVerdict:
        return cls(accepted=True)

    @classmethod
    def reject(cls, rule: str, reason: str) -> TriageVerdict:
        return cls(accepted=False, rule=rule, reason=reason)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token accounting, summed across both model calls for one message."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @classmethod
    def from_response(cls, response: object) -> TokenUsage:
        """Read the usage block off a Messages API response, defensively.

        Cost reporting must never be the thing that breaks a poll, so a response
        shaped differently than expected reports zero rather than raising.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return cls()
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )


@dataclass(frozen=True, slots=True)
class Classification:
    """The classifier's verdict on one message."""

    category: Category
    should_reply: bool
    urgency: Urgency
    reason: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """What the drafter produced: a reply body, or a reason it refused to."""

    outcome: Outcome
    html: str = ""
    reason: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def has_draft(self) -> bool:
        return self.outcome is Outcome.DRAFTED and bool(self.html)
