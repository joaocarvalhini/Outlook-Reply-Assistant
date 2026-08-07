"""The processing ledger and the key it is built on.

The first test here is the one that matters: a message that moves folder keeps
its Message-ID but is handed a new Graph `id`. A ledger keyed on `id` would
treat the moved message as never seen, which is how the same email ends up with
a second draft after someone tidies the inbox.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.models import EmailMessage
from src.state import MAX_REMEMBERED_IDS, ProcessingState


def make_email(**overrides: object) -> EmailMessage:
    defaults: dict[str, object] = {
        "id": "AAMkAGI2-inbox",
        "conversation_id": "conv-1",
        "internet_message_id": "<CAF123@mail.gmail.com>",
        "subject": "Duvida",
        "sender": "cliente@gmail.com",
        "sender_name": "Ana",
        "to": ("apoio@loja.pt",),
        "cc": (),
        "received_at": "2026-08-06T10:00:00Z",
    }
    defaults.update(overrides)
    return EmailMessage(**defaults)  # type: ignore[arg-type]


class LedgerKeyTests(unittest.TestCase):
    def test_prefers_the_message_id(self) -> None:
        email = make_email()
        self.assertEqual(email.ledger_key, "<CAF123@mail.gmail.com>")

    def test_falls_back_to_the_graph_id(self) -> None:
        """A message with no Message-ID is malformed, but must not be unkeyed."""
        email = make_email(internet_message_id="")
        self.assertEqual(email.ledger_key, "AAMkAGI2-inbox")

    def test_a_moved_message_keeps_its_key(self) -> None:
        state = ProcessingState(path=Path("unused.json"))
        original = make_email()
        state.mark(original.ledger_key, original.received_at)

        # Someone drags the email to another folder: Graph reassigns `id`,
        # the Message-ID is unchanged.
        moved = make_email(id="AAMkAGI2-arquivo")

        self.assertNotEqual(moved.id, original.id)
        self.assertTrue(state.was_processed(moved.ledger_key))

    def test_a_different_message_is_not_confused_with_it(self) -> None:
        state = ProcessingState(path=Path("unused.json"))
        state.mark(make_email().ledger_key, "2026-08-06T10:00:00Z")

        other = make_email(internet_message_id="<OUTRO@mail.gmail.com>")
        self.assertFalse(state.was_processed(other.ledger_key))


class LedgerTests(unittest.TestCase):
    def test_missing_file_starts_fresh(self) -> None:
        with TemporaryDirectory() as directory:
            state = ProcessingState.load(Path(directory) / "state.json")

        self.assertEqual(state.last_received, "")
        self.assertEqual(state.processed, [])

    def test_corrupt_file_starts_fresh_without_raising(self) -> None:
        """A broken ledger must not stop the daemon: the categories still hold.

        It must say so, though -- a ledger that silently resets looks identical
        to a first run, and a first run skips the entire backlog.
        """
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{ isto nao e json", encoding="utf-8")
            with self.assertLogs("reply_assistant.state", level="WARNING") as captured:
                state = ProcessingState.load(path)

        self.assertEqual(state.processed, [])
        self.assertIn("state unreadable", captured.output[0])

    def test_survives_a_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = ProcessingState.load(path)
            state.mark("<a@x>", "2026-08-06T10:00:00Z")
            state.mark("<b@x>", "2026-08-06T11:00:00Z")
            state.save()

            reloaded = ProcessingState.load(path)

        self.assertTrue(reloaded.was_processed("<a@x>"))
        self.assertTrue(reloaded.was_processed("<b@x>"))
        self.assertEqual(reloaded.last_received, "2026-08-06T11:00:00Z")

    def test_marking_twice_does_not_duplicate(self) -> None:
        state = ProcessingState(path=Path("unused.json"))
        state.mark("<a@x>", "2026-08-06T10:00:00Z")
        state.mark("<a@x>", "2026-08-06T10:00:00Z")

        self.assertEqual(state.processed, ["<a@x>"])

    def test_watermark_never_moves_backwards(self) -> None:
        state = ProcessingState(path=Path("unused.json"))
        state.mark("<a@x>", "2026-08-06T12:00:00Z")
        state.mark("<b@x>", "2026-08-06T09:00:00Z")

        self.assertEqual(state.last_received, "2026-08-06T12:00:00Z")

    def test_the_ledger_is_bounded(self) -> None:
        state = ProcessingState(path=Path("unused.json"))
        for index in range(MAX_REMEMBERED_IDS + 50):
            state.mark(f"<{index}@x>", "2026-08-06T10:00:00Z")

        self.assertEqual(len(state.processed), MAX_REMEMBERED_IDS)
        self.assertTrue(state.was_processed(f"<{MAX_REMEMBERED_IDS + 49}@x>"))
        # The oldest entries are dropped from the lookup set too, not just the
        # list -- otherwise the set grows forever behind a bounded list.
        self.assertFalse(state.was_processed("<0@x>"))

    def test_save_writes_readable_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = ProcessingState.load(path)
            state.mark("<a@x>", "2026-08-06T10:00:00Z")
            state.save()
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["last_received"], "2026-08-06T10:00:00Z")
        self.assertEqual(payload["processed"], ["<a@x>"])


if __name__ == "__main__":
    unittest.main()
