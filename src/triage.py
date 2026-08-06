"""Deterministic triage: the filters that run before any model call.

Roughly two thirds of a shop's inbox is newsletters, platform notifications and
cold outreach. None of it needs a language model to recognise, and every message
rejected here is a message that costs nothing. Just as important, this layer
owns the loop protection: without it an external auto-responder and this daemon
will happily draft at each other forever.

Rules are split in two because the data arrives in two stages. `screen` runs on
the fields the list query already returned; `screen_headers` runs after the
detail fetch, which is only paid for by messages that survived `screen`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger
from .models import EmailMessage, TriageVerdict

__all__ = ["Triage", "load_blocklist"]

_LOG = get_logger("triage")

# Local-parts that no human uses. Matched as substrings of the address' local
# part, so `no-reply-24@` and `newsletter.noreply@` both land.
_ROBOT_LOCAL_PARTS: tuple[str, ...] = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "notifications",
    "notification",
    "mailer-daemon",
    "mailerdaemon",
    "postmaster",
    "bounce",
    "bounces",
    "automated",
    "newsletter",
)

# Headers that mean "this was generated, not typed". Presence alone is enough
# for the first three; the rest are checked by value.
_BULK_HEADERS: tuple[str, ...] = (
    "list-unsubscribe",
    "list-id",
    "x-auto-response-suppress",
    "x-campaign-id",
    "x-mailer-campaign-id",
    "feedback-id",
)

_PRECEDENCE_VALUES = frozenset({"bulk", "list", "junk", "auto_reply"})

_AUTO_SUBMITTED_RE = re.compile(r"^\s*auto-", re.IGNORECASE)

# Seeds the blocklist when no file is present. Platforms that email a shop
# constantly and never expect a human reply.
DEFAULT_BLOCKED_DOMAINS: tuple[str, ...] = (
    "shopify.com",
    "stripe.com",
    "paypal.com",
    "mailchimp.com",
    "mailchimpapp.net",
    "sendgrid.net",
    "facebookmail.com",
    "google.com",
    "googlemail.com",
    "linkedin.com",
    "notifications.intercom.com",
    "ctt.pt",
    "dhl.com",
    "ups.com",
    "dpd.com",
)


def load_blocklist(path: Path) -> frozenset[str]:
    """Read one domain per line; `#` starts a comment.

    A missing file is not an error — it just means the client has not curated
    one yet, so the shipped defaults apply.
    """
    if not path.exists():
        return frozenset(DEFAULT_BLOCKED_DOMAINS)

    domains: set[str] = set(DEFAULT_BLOCKED_DOMAINS)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("blocklist unreadable", extra={"path": str(path), "error": exc})
        return frozenset(domains)

    for line in raw.splitlines():
        entry = line.split("#", 1)[0].strip().lower().lstrip("@")
        if entry:
            domains.add(entry)
    return frozenset(domains)


@dataclass(frozen=True, slots=True)
class Triage:
    """Applies the deterministic rules. No network, no model, fully testable."""

    mailbox: str
    own_domain: str
    drafted_category: str
    escalated_category: str
    blocked_domains: frozenset[str] = field(default_factory=frozenset)

    # -- stage 1: fields the list query already returned --------------------- #

    def screen(self, email: EmailMessage) -> TriageVerdict:
        """Cheap rules. Run before paying for the message detail fetch."""
        marker = self._processed_marker(email)
        if marker:
            return TriageVerdict.reject("already-processed", f"Ja marcado como {marker}.")

        sender = email.sender.lower()
        if not sender:
            return TriageVerdict.reject("no-sender", "Mensagem sem remetente.")

        if sender == self.mailbox:
            return TriageVerdict.reject("self", "Remetente e a propria caixa.")

        # Loop protection. An email from our own domain is a colleague, a
        # forward, or this daemon's own draft bouncing back — never a customer.
        if email.sender_domain == self.own_domain:
            return TriageVerdict.reject("own-domain", "Remetente do dominio da loja.")

        local_part = sender.partition("@")[0]
        for pattern in _ROBOT_LOCAL_PARTS:
            if pattern in local_part:
                return TriageVerdict.reject("robot-sender", f"Remetente automatico ({pattern}).")

        domain = email.sender_domain
        if domain in self.blocked_domains or any(
            domain.endswith(f".{blocked}") for blocked in self.blocked_domains
        ):
            return TriageVerdict.reject("blocked-domain", f"Dominio na lista negra ({domain}).")

        # If the shop is neither in To nor Cc, this arrived via Bcc or a list.
        # Customers write to the shop directly; bulk senders do not.
        recipients = {address.lower() for address in email.recipients}
        if recipients and self.mailbox not in recipients:
            return TriageVerdict.reject("not-addressed", "Loja nao consta em Para nem Cc.")

        return TriageVerdict.accept()

    # -- stage 2: needs the detail fetch ------------------------------------- #

    def screen_headers(self, email: EmailMessage) -> TriageVerdict:
        """Header rules. These catch bulk mail that uses a human-looking From."""
        for header in _BULK_HEADERS:
            if email.has_header(header):
                return TriageVerdict.reject("bulk-header", f"Cabecalho {header} presente.")

        precedence = (email.header("precedence") or "").strip().lower()
        if precedence in _PRECEDENCE_VALUES:
            return TriageVerdict.reject("precedence", f"Precedence: {precedence}.")

        auto_submitted = (email.header("auto-submitted") or "").strip()
        if auto_submitted and _AUTO_SUBMITTED_RE.match(auto_submitted):
            return TriageVerdict.reject("auto-submitted", f"Auto-Submitted: {auto_submitted}.")

        if not email.body.strip():
            return TriageVerdict.reject("empty-body", "Mensagem sem corpo utilizavel.")

        return TriageVerdict.accept()

    # -- internals ----------------------------------------------------------- #

    def _processed_marker(self, email: EmailMessage) -> str | None:
        """Return the category that says this message was already handled.

        The category on the message is the durable record; the local state file
        is only a fast path. If the state file is deleted, this is what stops a
        second pass from drafting over the team's work.
        """
        for category in email.categories:
            if category in (self.drafted_category, self.escalated_category):
                return category
        return None
