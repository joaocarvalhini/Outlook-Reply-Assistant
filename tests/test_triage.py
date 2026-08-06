"""Triage rules.

These are the filters that decide what never reaches a model, and the loop
protection that keeps the daemon from drafting at an auto-responder forever.
They are pure functions over a dataclass, so every rule is cheap to pin down.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.models import EmailMessage
from src.triage import DEFAULT_BLOCKED_DOMAINS, Triage, load_blocklist

MAILBOX = "apoio@loja.pt"


def make_email(**overrides: object) -> EmailMessage:
    defaults: dict[str, object] = {
        "id": "AAMk-1",
        "conversation_id": "conv-1",
        "internet_message_id": "<abc@example.com>",
        "subject": "Duvida sobre uma encomenda",
        "sender": "cliente@gmail.com",
        "sender_name": "Ana Silva",
        "to": (MAILBOX,),
        "cc": (),
        "received_at": "2026-08-06T10:00:00Z",
        "body": "Boa tarde, quando chega a minha encomenda?",
        "headers": (),
        "categories": (),
    }
    defaults.update(overrides)
    return EmailMessage(**defaults)  # type: ignore[arg-type]


def make_triage(**overrides: object) -> Triage:
    defaults: dict[str, object] = {
        "mailbox": MAILBOX,
        "own_domain": "loja.pt",
        "drafted_category": "IA-Rascunhado",
        "escalated_category": "Precisa de humano",
        "blocked_domains": frozenset(DEFAULT_BLOCKED_DOMAINS),
    }
    defaults.update(overrides)
    return Triage(**defaults)  # type: ignore[arg-type]


class ScreenTests(unittest.TestCase):
    """Stage one: the cheap rules, before the detail fetch is paid for."""

    def setUp(self) -> None:
        self.triage = make_triage()

    def test_accepts_a_plain_customer_email(self) -> None:
        self.assertTrue(self.triage.screen(make_email()).accepted)

    def test_rejects_already_drafted(self) -> None:
        verdict = self.triage.screen(make_email(categories=("IA-Rascunhado",)))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "already-processed")

    def test_rejects_already_escalated(self) -> None:
        verdict = self.triage.screen(make_email(categories=("Outra", "Precisa de humano")))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "already-processed")

    def test_unrelated_categories_do_not_block(self) -> None:
        self.assertTrue(self.triage.screen(make_email(categories=("Urgente",))).accepted)

    def test_rejects_own_domain(self) -> None:
        """Loop protection: our own drafts and colleagues are never customers."""
        verdict = self.triage.screen(make_email(sender="joao@loja.pt"))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "own-domain")

    def test_rejects_the_mailbox_itself(self) -> None:
        verdict = self.triage.screen(make_email(sender=MAILBOX))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "self")

    def test_rejects_robot_senders(self) -> None:
        for address in (
            "noreply@parceiro.com",
            "no-reply@parceiro.com",
            "NoReply@Parceiro.com",
            "notifications@app.io",
            "mailer-daemon@servidor.net",
            "bounces+123@campanha.co",
            "newsletter@revista.pt",
        ):
            with self.subTest(address=address):
                verdict = self.triage.screen(make_email(sender=address))
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.rule, "robot-sender")

    def test_rejects_blocked_domains(self) -> None:
        verdict = self.triage.screen(make_email(sender="pedidos@shopify.com"))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "blocked-domain")

    def test_rejects_blocked_subdomains(self) -> None:
        verdict = self.triage.screen(make_email(sender="alertas@mail.stripe.com"))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "blocked-domain")

    def test_similar_domain_is_not_blocked(self) -> None:
        """`notshopify.com` must not match `shopify.com`."""
        self.assertTrue(self.triage.screen(make_email(sender="ana@notshopify.com")).accepted)

    def test_rejects_when_shop_is_not_a_recipient(self) -> None:
        verdict = self.triage.screen(make_email(to=("outra@empresa.pt",), cc=()))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "not-addressed")

    def test_accepts_when_shop_is_only_in_cc(self) -> None:
        email = make_email(to=("outra@empresa.pt",), cc=(MAILBOX,))
        self.assertTrue(self.triage.screen(email).accepted)

    def test_accepts_when_recipients_are_unknown(self) -> None:
        """A Bcc-only delivery has no recipients; do not guess, let the model decide."""
        self.assertTrue(self.triage.screen(make_email(to=(), cc=())).accepted)

    def test_rejects_missing_sender(self) -> None:
        verdict = self.triage.screen(make_email(sender=""))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "no-sender")


class HeaderTests(unittest.TestCase):
    """Stage two: bulk mail that arrives with a human-looking From address."""

    def setUp(self) -> None:
        self.triage = make_triage()

    def test_accepts_a_normal_message(self) -> None:
        email = make_email(headers=(("Received", "from mx.gmail.com"),))
        self.assertTrue(self.triage.screen_headers(email).accepted)

    def test_rejects_list_unsubscribe(self) -> None:
        email = make_email(headers=(("List-Unsubscribe", "<https://x/y>"),))
        verdict = self.triage.screen_headers(email)
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "bulk-header")

    def test_header_match_is_case_insensitive(self) -> None:
        email = make_email(headers=(("list-ID", "<news.loja>"),))
        self.assertFalse(self.triage.screen_headers(email).accepted)

    def test_rejects_bulk_precedence(self) -> None:
        for value in ("bulk", "list", "junk", "auto_reply", "Bulk"):
            with self.subTest(value=value):
                email = make_email(headers=(("Precedence", value),))
                verdict = self.triage.screen_headers(email)
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.rule, "precedence")

    def test_normal_precedence_passes(self) -> None:
        email = make_email(headers=(("Precedence", "normal"),))
        self.assertTrue(self.triage.screen_headers(email).accepted)

    def test_rejects_auto_submitted(self) -> None:
        for value in ("auto-generated", "auto-replied", "AUTO-NOTIFIED"):
            with self.subTest(value=value):
                email = make_email(headers=(("Auto-Submitted", value),))
                verdict = self.triage.screen_headers(email)
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.rule, "auto-submitted")

    def test_auto_submitted_no_is_a_real_message(self) -> None:
        """RFC 3834: `no` is what a human-composed message says."""
        email = make_email(headers=(("Auto-Submitted", "no"),))
        self.assertTrue(self.triage.screen_headers(email).accepted)

    def test_rejects_empty_body(self) -> None:
        verdict = self.triage.screen_headers(make_email(body="   \n  "))
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.rule, "empty-body")


class BlocklistTests(unittest.TestCase):
    def test_missing_file_returns_the_defaults(self) -> None:
        domains = load_blocklist(Path("does-not-exist.txt"))
        self.assertIn("shopify.com", domains)

    def test_file_entries_extend_the_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "blocklist.txt"
            path.write_text(
                "# comentario\n@Fornecedor.PT\n\nplataforma.com  # inline\n",
                encoding="utf-8",
            )
            domains = load_blocklist(path)

        self.assertIn("fornecedor.pt", domains)
        self.assertIn("plataforma.com", domains)
        self.assertIn("stripe.com", domains)


if __name__ == "__main__":
    unittest.main()
