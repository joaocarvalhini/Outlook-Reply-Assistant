"""Local processing ledger.

Two things must survive a restart: how far through the mailbox we got, and which
messages we already touched. The Outlook category on each message is the durable
record — this file is the fast path that keeps a restart from re-listing weeks of
mail and re-fetching every message just to discover it was already handled.

Losing this file is safe. Losing the categories is not.

Entries are keyed on `EmailMessage.ledger_key`, which is the Message-ID rather
than Graph's per-folder `id` — see the property's docstring for why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

__all__ = ["ProcessingState"]

_LOG = get_logger("state")

# Enough to cover any plausible backlog between polls without growing forever.
MAX_REMEMBERED_IDS = 2000


@dataclass(slots=True)
class ProcessingState:
    """Mutable, explicitly persisted. Call `save()` after each message."""

    path: Path
    last_received: str = ""
    processed: list[str] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def load(cls, path: Path) -> ProcessingState:
        """Read the ledger, or start a fresh one if it is missing or corrupt."""
        if not path.exists():
            return cls(path=path)

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt ledger must not stop the daemon: the categories on the
            # messages still prevent duplicate drafts.
            _LOG.warning("state unreadable, starting fresh", extra={"error": exc})
            return cls(path=path)

        processed = [str(item) for item in raw.get("processed", [])]
        state = cls(
            path=path,
            last_received=str(raw.get("last_received", "")),
            processed=processed,
        )
        state._seen = set(processed)
        _LOG.info(
            "state loaded",
            extra={"last_received": state.last_received or "never", "known": len(processed)},
        )
        return state

    def was_processed(self, key: str) -> bool:
        return key in self._seen

    def mark(self, key: str, received_at: str) -> None:
        """Record a message as handled and advance the watermark."""
        if key not in self._seen:
            self._seen.add(key)
            self.processed.append(key)
            if len(self.processed) > MAX_REMEMBERED_IDS:
                dropped = self.processed[:-MAX_REMEMBERED_IDS]
                self.processed = self.processed[-MAX_REMEMBERED_IDS:]
                self._seen.difference_update(dropped)

        # Messages are processed in ascending receivedDateTime order, but never
        # move the watermark backwards if that assumption is ever violated.
        if received_at > self.last_received:
            self.last_received = received_at

    def save(self) -> None:
        """Write the ledger. A failure here is logged, never raised."""
        payload = {"last_received": self.last_received, "processed": self.processed}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)
        except OSError as exc:
            _LOG.error("could not persist state", extra={"path": str(self.path), "error": exc})
