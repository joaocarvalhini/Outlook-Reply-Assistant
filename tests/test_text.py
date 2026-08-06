"""Text handling on both boundaries.

Inbound, an Outlook body has to become something worth paying tokens for.
Outbound, model output has to become something safe to store in a mailbox. Both
directions handle untrusted input, so both are pinned down here.
"""

from __future__ import annotations

import unittest

from src.escalation import detect_escalation
from src.utils import html_to_text, sanitize_html, strip_quoted_reply, truncate


class HtmlToTextTests(unittest.TestCase):
    def test_extracts_text_from_paragraphs(self) -> None:
        text = html_to_text("<p>Boa tarde</p><p>Quando chega?</p>")
        self.assertIn("Boa tarde", text)
        self.assertIn("Quando chega?", text)

    def test_drops_script_and_style_content(self) -> None:
        text = html_to_text("<style>p{color:red}</style><p>Ola</p><script>alert(1)</script>")
        self.assertEqual(text, "Ola")

    def test_decodes_entities_and_nbsp(self) -> None:
        self.assertEqual(html_to_text("<p>caf&eacute;&nbsp;preto</p>"), "café preto")

    def test_plain_text_passes_through(self) -> None:
        self.assertEqual(html_to_text("Sem etiquetas nenhumas"), "Sem etiquetas nenhumas")

    def test_empty_input(self) -> None:
        self.assertEqual(html_to_text(""), "")


class StripQuotedReplyTests(unittest.TestCase):
    def test_cuts_at_portuguese_outlook_separator(self) -> None:
        body = "A minha pergunta.\n\nDe: Loja <apoio@loja.pt>\nEnviada: 5 de agosto\n\nAntigo"
        self.assertEqual(strip_quoted_reply(body), "A minha pergunta.")

    def test_cuts_at_original_message_separator(self) -> None:
        body = "Obrigada!\n\n-----Mensagem original-----\nTexto antigo"
        self.assertEqual(strip_quoted_reply(body), "Obrigada!")

    def test_cuts_at_wrote_line(self) -> None:
        body = "Ainda nao chegou.\n\nEm 5 de agosto, Loja escreveu:\n> texto antigo"
        self.assertEqual(strip_quoted_reply(body), "Ainda nao chegou.")

    def test_drops_trailing_quoted_lines(self) -> None:
        self.assertEqual(strip_quoted_reply("Nova pergunta\n> antiga\n> antiga"), "Nova pergunta")

    def test_keeps_a_message_with_no_quote(self) -> None:
        self.assertEqual(strip_quoted_reply("Uma pergunta simples"), "Uma pergunta simples")

    def test_never_returns_empty_for_a_non_empty_body(self) -> None:
        """A message that is nothing but a quote is still better than nothing."""
        self.assertTrue(strip_quoted_reply("-----Mensagem original-----\nSo citacao"))


class SanitizeHtmlTests(unittest.TestCase):
    def test_keeps_allowed_tags(self) -> None:
        cleaned = sanitize_html("<p>Ola<br>mundo</p>")
        self.assertEqual(cleaned, "<p>Ola<br>mundo</p>")

    def test_keeps_lists_and_emphasis(self) -> None:
        cleaned = sanitize_html("<ul><li><strong>Um</strong></li></ul>")
        self.assertEqual(cleaned, "<ul><li><strong>Um</strong></li></ul>")

    def test_drops_script_tags_and_escapes_their_content(self) -> None:
        cleaned = sanitize_html("<p>Ola</p><script>alert(1)</script>")
        self.assertNotIn("<script", cleaned)
        self.assertIn("&lt;", sanitize_html("<p>a &lt; b</p>"))

    def test_drops_all_attributes(self) -> None:
        """No href, no style, no onerror -- nothing for an injection to ride on."""
        cleaned = sanitize_html('<p onclick="steal()" style="x">Ola</p>')
        self.assertEqual(cleaned, "<p>Ola</p>")

    def test_drops_anchor_tags_but_keeps_the_words(self) -> None:
        cleaned = sanitize_html('<p>Ver <a href="javascript:x">aqui</a></p>')
        self.assertNotIn("javascript", cleaned)
        self.assertIn("aqui", cleaned)

    def test_closes_unbalanced_tags(self) -> None:
        self.assertEqual(sanitize_html("<p>Sem fecho"), "<p>Sem fecho</p>")

    def test_wraps_bare_text(self) -> None:
        self.assertEqual(sanitize_html("Apenas texto"), "<p>Apenas texto</p>")

    def test_empty_input(self) -> None:
        self.assertEqual(sanitize_html(""), "")


class EscalationTests(unittest.TestCase):
    def test_detects_a_plain_marker(self) -> None:
        reason = detect_escalation("ESCALATE: Base de conhecimento nao refere pagamentos.")
        self.assertEqual(reason, "Base de conhecimento nao refere pagamentos.")

    def test_detects_a_markdown_decorated_marker(self) -> None:
        self.assertEqual(detect_escalation("**ESCALATE:** Falta informacao."), "Falta informacao.")

    def test_detects_a_reason_on_the_next_line(self) -> None:
        self.assertEqual(detect_escalation("ESCALATE:\nFalta informacao."), "Falta informacao.")

    def test_supplies_a_default_reason(self) -> None:
        self.assertTrue(detect_escalation("ESCALATE:"))

    def test_a_real_reply_is_not_an_escalation(self) -> None:
        self.assertIsNone(detect_escalation("<p>As entregas demoram 2 a 3 dias uteis.</p>"))

    def test_empty_text_is_not_an_escalation(self) -> None:
        self.assertIsNone(detect_escalation(""))


class TruncateTests(unittest.TestCase):
    def test_leaves_short_text_alone(self) -> None:
        self.assertEqual(truncate("curto", 20), "curto")

    def test_appends_a_suffix(self) -> None:
        self.assertTrue(truncate("a" * 50, 10).endswith("..."))
        self.assertEqual(len(truncate("a" * 50, 10)), 10)

    def test_rejects_a_non_positive_limit(self) -> None:
        with self.assertRaises(ValueError):
            truncate("texto", 0)


if __name__ == "__main__":
    unittest.main()
