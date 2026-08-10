"""Testes do assistente.

Sem dependências: corre na biblioteca padrão.

    python -m unittest test_assistente -v

Cobre as três coisas que se partem em silêncio: as regras de triagem (a camada
que decide o que nunca custa dinheiro), o tratamento de texto nas duas fronteiras
não confiáveis, e o registo local — incluindo a chave em que assenta.
"""

from __future__ import annotations

import re
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from exportar import anonimizar, anonimizar_endereco, palpitar

from assistente import (
    DOMINIOS_BASE,
    Config,
    abrir_db,
    carregar_blocklist,
    cortar_citacao,
    cursor_atual,
    ja_processado,
    para_html,
    para_texto,
    registar,
    triar,
    triar_cabecalhos,
)

CAIXA = "apoio@loja.pt"


def cfg(**over: object) -> Config:
    base: dict[str, object] = {
        "api_key": "x", "tenant_id": "x", "client_id": "x", "client_secret": "x",
        "mailbox": CAIXA, "modelo": "claude-sonnet-5",
        "knowledge_dir": Path("knowledge"), "blocklist": Path("blocklist.txt"),
        "db": Path("t.db"), "max_body": 4000, "dry_run": True,
        "empresa": "A Loja", "assinatura": "Equipa",
        "cat_rascunho": "IA-Rascunhado", "cat_humano": "Precisa de humano",
        "aviso": "--- rascunho automático ---",
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


def msg(**over: object) -> dict:
    base: dict[str, object] = {
        "id": "AAMk-1",
        "message_id": "<abc@mail.gmail.com>",
        "conversation_id": "conv-1",
        "assunto": "Dúvida sobre uma encomenda",
        "de": "cliente@gmail.com",
        "nome": "Ana Silva",
        "para": [CAIXA],
        "cc": [],
        "recebido": "2026-08-06T10:00:00Z",
        "categorias": [],
        "cabecalhos": [],
        "corpo": "Boa tarde, quando chega a minha encomenda?",
    }
    base.update(over)
    return base


BLOQUEADOS = frozenset(DOMINIOS_BASE)


class Triagem(unittest.TestCase):
    def test_email_de_cliente_passa(self) -> None:
        self.assertIsNone(triar(msg(), cfg(), BLOQUEADOS))

    def test_ja_processado(self) -> None:
        self.assertEqual(
            triar(msg(categorias=["IA-Rascunhado"]), cfg(), BLOQUEADOS), "ja-processado"
        )
        self.assertEqual(
            triar(msg(categorias=["Precisa de humano"]), cfg(), BLOQUEADOS),
            "ja-processado",
        )

    def test_outras_categorias_nao_bloqueiam(self) -> None:
        self.assertIsNone(triar(msg(categorias=["Urgente"]), cfg(), BLOQUEADOS))

    def test_anti_ciclo_dominio_proprio(self) -> None:
        """Um email do nosso domínio é um colega ou o nosso próprio rascunho."""
        self.assertEqual(
            triar(msg(de="joao@loja.pt"), cfg(), BLOQUEADOS), "dominio-proprio"
        )

    def test_a_propria_caixa(self) -> None:
        self.assertEqual(triar(msg(de=CAIXA), cfg(), BLOQUEADOS), "a-propria-caixa")

    def test_remetentes_automaticos(self) -> None:
        for endereco in (
            "noreply@parceiro.com", "no-reply@parceiro.com", "NoReply@Parceiro.com",
            "notifications@app.io", "mailer-daemon@servidor.net",
            "bounces+1@campanha.co", "newsletter@revista.pt",
        ):
            with self.subTest(endereco=endereco):
                motivo = triar(msg(de=endereco.lower()), cfg(), BLOQUEADOS)
                self.assertIsNotNone(motivo)
                self.assertTrue(motivo.startswith("remetente-automatico"))

    def test_dominio_bloqueado(self) -> None:
        motivo = triar(msg(de="pedidos@shopify.com"), cfg(), BLOQUEADOS)
        self.assertTrue(motivo.startswith("dominio-bloqueado"))

    def test_subdominio_bloqueado(self) -> None:
        motivo = triar(msg(de="alertas@mail.stripe.com"), cfg(), BLOQUEADOS)
        self.assertTrue(motivo.startswith("dominio-bloqueado"))

    def test_dominio_parecido_nao_bloqueia(self) -> None:
        """notshopify.com não pode casar com shopify.com."""
        self.assertIsNone(triar(msg(de="ana@notshopify.com"), cfg(), BLOQUEADOS))

    def test_loja_nao_e_destinataria(self) -> None:
        self.assertEqual(
            triar(msg(para=["outra@empresa.pt"], cc=[]), cfg(), BLOQUEADOS),
            "nao-endereçado",
        )

    def test_loja_em_cc_passa(self) -> None:
        self.assertIsNone(
            triar(msg(para=["outra@empresa.pt"], cc=[CAIXA]), cfg(), BLOQUEADOS)
        )

    def test_sem_destinatarios_passa(self) -> None:
        """Entrega por Bcc não tem destinatários: decide o modelo, não a triagem."""
        self.assertIsNone(triar(msg(para=[], cc=[]), cfg(), BLOQUEADOS))

    def test_sem_remetente(self) -> None:
        self.assertEqual(triar(msg(de=""), cfg(), BLOQUEADOS), "sem-remetente")


class TriagemCabecalhos(unittest.TestCase):
    def test_mensagem_normal_passa(self) -> None:
        self.assertIsNone(triar_cabecalhos(msg(cabecalhos=[("Received", "mx.gmail.com")])))

    def test_list_unsubscribe(self) -> None:
        motivo = triar_cabecalhos(msg(cabecalhos=[("List-Unsubscribe", "<https://x>")]))
        self.assertTrue(motivo.startswith("cabecalho-massa"))

    def test_cabecalho_e_insensivel_a_maiusculas(self) -> None:
        self.assertIsNotNone(triar_cabecalhos(msg(cabecalhos=[("list-ID", "<news>")])))

    def test_precedence_massa(self) -> None:
        for valor in ("bulk", "list", "junk", "auto_reply", "Bulk"):
            with self.subTest(valor=valor):
                self.assertEqual(
                    triar_cabecalhos(msg(cabecalhos=[("Precedence", valor)])),
                    "precedence-massa",
                )

    def test_precedence_normal_passa(self) -> None:
        self.assertIsNone(triar_cabecalhos(msg(cabecalhos=[("Precedence", "normal")])))

    def test_auto_submitted(self) -> None:
        for valor in ("auto-generated", "auto-replied", "AUTO-NOTIFIED"):
            with self.subTest(valor=valor):
                self.assertEqual(
                    triar_cabecalhos(msg(cabecalhos=[("Auto-Submitted", valor)])),
                    "auto-submitted",
                )

    def test_auto_submitted_no_passa(self) -> None:
        """RFC 3834: `no` é o que uma mensagem escrita por uma pessoa diz."""
        self.assertIsNone(triar_cabecalhos(msg(cabecalhos=[("Auto-Submitted", "no")])))

    def test_corpo_vazio(self) -> None:
        self.assertEqual(triar_cabecalhos(msg(corpo="  \n ")), "corpo-vazio")


class Blocklist(unittest.TestCase):
    def test_ficheiro_em_falta_devolve_a_base(self) -> None:
        self.assertIn("shopify.com", carregar_blocklist(Path("nao-existe.txt")))

    def test_ficheiro_acrescenta_a_base(self) -> None:
        with TemporaryDirectory() as d:
            p = Path(d) / "b.txt"
            p.write_text("# nota\n@Fornecedor.PT\n\nplataforma.com  # inline\n", encoding="utf-8")
            dominios = carregar_blocklist(p)
        self.assertIn("fornecedor.pt", dominios)
        self.assertIn("plataforma.com", dominios)
        self.assertIn("stripe.com", dominios)


class Texto(unittest.TestCase):
    def test_extrai_paragrafos(self) -> None:
        t = para_texto("<p>Boa tarde</p><p>Quando chega?</p>")
        self.assertIn("Boa tarde", t)
        self.assertIn("Quando chega?", t)

    def test_ignora_script_e_style(self) -> None:
        self.assertEqual(para_texto("<style>p{}</style><p>Olá</p><script>x()</script>"), "Olá")

    def test_descodifica_entidades(self) -> None:
        self.assertEqual(para_texto("<p>caf&eacute;&nbsp;preto</p>"), "café preto")

    def test_texto_simples_passa(self) -> None:
        self.assertEqual(para_texto("Sem etiquetas"), "Sem etiquetas")

    def test_corta_separador_do_outlook(self) -> None:
        corpo = "A minha pergunta.\n\nDe: Loja <apoio@loja.pt>\nEnviada: 5 agosto\n\nAntigo"
        self.assertEqual(cortar_citacao(corpo), "A minha pergunta.")

    def test_corta_mensagem_original(self) -> None:
        self.assertEqual(
            cortar_citacao("Obrigada!\n\n-----Mensagem original-----\nAntigo"), "Obrigada!"
        )

    def test_corta_linha_escreveu(self) -> None:
        corpo = "Ainda não chegou.\n\nEm 5 de agosto, Loja escreveu:\n> antigo"
        self.assertEqual(cortar_citacao(corpo), "Ainda não chegou.")

    def test_corta_linhas_citadas_no_fim(self) -> None:
        self.assertEqual(cortar_citacao("Nova\n> antiga\n> antiga"), "Nova")

    def test_nunca_devolve_vazio(self) -> None:
        self.assertTrue(cortar_citacao("-----Mensagem original-----\nSó citação"))


class HtmlDeSaida(unittest.TestCase):
    """O corpo vem do modelo, que leu um email não confiável. É escapado, não filtrado."""

    def test_envolve_paragrafos(self) -> None:
        self.assertEqual(para_html("Olá\n\nAdeus"), "<p>Olá</p><p>Adeus</p>")

    def test_quebra_de_linha_simples(self) -> None:
        self.assertEqual(para_html("Uma\nDuas"), "<p>Uma<br>Duas</p>")

    def test_escapa_html(self) -> None:
        saida = para_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", saida)
        self.assertIn("&lt;script&gt;", saida)

    def test_escapa_e_comercial(self) -> None:
        self.assertEqual(para_html("Portes & taxas"), "<p>Portes &amp; taxas</p>")

    def test_texto_vazio(self) -> None:
        self.assertEqual(para_html("   "), "")


class Registo(unittest.TestCase):
    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        # LIFO: a ligação fecha antes de o Windows tentar apagar o ficheiro.
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def test_cursor_comeca_vazio(self) -> None:
        self.assertEqual(cursor_atual(self.con), "")

    def test_registar_e_reler(self) -> None:
        m = msg()
        self.assertFalse(ja_processado(self.con, m["message_id"]))
        registar(self.con, m, "rascunhar", "sabia responder", "Boa tarde,")
        self.assertTrue(ja_processado(self.con, m["message_id"]))
        self.assertEqual(cursor_atual(self.con), "2026-08-06T10:00:00Z")

    def test_a_chave_e_o_message_id_nao_o_id_do_graph(self) -> None:
        """O id do Graph muda quando alguém arruma o email; o Message-ID não."""
        registar(self.con, msg(), "saltar", "newsletter", "")
        movido = msg(id="AAMk-arquivo")
        self.assertNotEqual(movido["id"], msg()["id"])
        self.assertTrue(ja_processado(self.con, movido["message_id"]))

    def test_mensagem_diferente_nao_se_confunde(self) -> None:
        registar(self.con, msg(), "saltar", "newsletter", "")
        self.assertFalse(ja_processado(self.con, "<outro@mail.com>"))

    def test_cursor_nunca_anda_para_tras(self) -> None:
        registar(self.con, msg(recebido="2026-08-06T12:00:00Z"), "saltar", "x", "")
        registar(
            self.con,
            msg(message_id="<b@x>", recebido="2026-08-06T09:00:00Z"),
            "saltar", "x", "",
        )
        self.assertEqual(cursor_atual(self.con), "2026-08-06T12:00:00Z")

    def test_guarda_o_corpo_para_medir_deriva(self) -> None:
        registar(self.con, msg(), "rascunhar", "ok", "Boa tarde, as entregas...")
        linha = self.con.execute("SELECT acao, corpo FROM processados").fetchone()
        self.assertEqual(linha[0], "rascunhar")
        self.assertIn("entregas", linha[1])


class Anonimizacao(unittest.TestCase):
    """A anonimização do exportar.py.

    Se isto se partir, vazam dados de clientes para um ficheiro — e em silêncio,
    porque ninguém relê 200 emails à procura de um telefone que escapou.
    """

    def test_email_mantem_o_dominio(self) -> None:
        """O domínio é o que a triagem lê; sem ele os casos não testam nada."""
        self.assertEqual(
            anonimizar("escreve para ana.silva@gmail.com"),
            "escreve para <email>@gmail.com",
        )

    def test_telemovel(self) -> None:
        self.assertEqual(anonimizar("liga 912345678"), "liga <TELEFONE>")

    def test_fixo_com_espacos(self) -> None:
        """O formato em que as pessoas realmente escrevem números."""
        self.assertEqual(anonimizar("liga 21 234 5678"), "liga <TELEFONE>")

    def test_telefone_com_indicativo_e_separadores(self) -> None:
        for numero in ("+351 912 345 678", "96.123.4567", "213-456-789"):
            with self.subTest(numero=numero):
                self.assertNotIn("4", anonimizar(f"contacto {numero}"))

    def test_iban(self) -> None:
        self.assertEqual(
            anonimizar("IBAN PT50 0002 0123 1234 5678 9015 4"), "IBAN <IBAN>"
        )

    def test_codigo_postal(self) -> None:
        self.assertIn("<COD-POSTAL>", anonimizar("morada 2620-537 Ramada"))

    def test_numero_de_encomenda(self) -> None:
        self.assertEqual(
            anonimizar("a encomenda 1029384 não chegou"),
            "a encomenda <NUMERO> não chegou",
        )

    def test_nome_do_remetente_na_assinatura(self) -> None:
        limpo = anonimizar("Cumprimentos,\nAna Silva", "Ana Silva")
        self.assertNotIn("Ana", limpo)
        self.assertNotIn("Silva", limpo)

    def test_nao_troca_iniciais_curtas(self) -> None:
        """Partes com menos de 3 letras trocariam palavras por todo o lado."""
        self.assertIn("de", anonimizar("a encomenda de teste", "Ana de Sá"))

    def test_numeros_inofensivos_ficam(self) -> None:
        texto = "São 3 artigos, 25 euros, chegou dia 12"
        self.assertEqual(anonimizar(texto), texto)

    def test_nenhuma_sequencia_de_nove_digitos_sobrevive(self) -> None:
        """Rede de segurança: qualquer coisa com forma de telefone ou NIF."""
        for texto in (
            "liga 912345678", "liga 21 234 5678", "NIF 218250016",
            "+351 912 345 678", "96.123.4567",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(
                    re.search(r"(?:\d[\s.-]?){8}\d", anonimizar(texto)),
                    f"vazou um número em {texto!r}",
                )


class EnderecoAnonimizado(unittest.TestCase):
    def test_pessoa_e_tapada(self) -> None:
        self.assertEqual(anonimizar_endereco("ana.silva@gmail.com"), "<pessoa>@gmail.com")

    def test_generico_fica_intacto(self) -> None:
        """`noreply@` é categoria, não identidade: tapá-lo apagava o caso."""
        for endereco in ("noreply@shopify.com", "info@fornecedor.pt",
                         "newsletter@revista.pt"):
            with self.subTest(endereco=endereco):
                self.assertEqual(anonimizar_endereco(endereco), endereco)

    def test_endereco_sem_dominio(self) -> None:
        self.assertEqual(anonimizar_endereco("invalido"), "<endereco>")


class Palpite(unittest.TestCase):
    def test_estado_de_encomenda(self) -> None:
        for corpo in (
            "onde está a minha encomenda?",
            "ainda não chegou nada",
            "qual é o código de seguimento?",
        ):
            with self.subTest(corpo=corpo):
                self.assertEqual(palpitar("", corpo), "estado-encomenda")

    def test_devolucao(self) -> None:
        self.assertEqual(palpitar("", "quero devolver os fones"), "devolucao-garantia")

    def test_outro(self) -> None:
        self.assertEqual(palpitar("", "obrigado pelo bom serviço"), "outro")


if __name__ == "__main__":
    unittest.main()
