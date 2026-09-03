"""Testes do assistente.

Sem dependências: corre na biblioteca padrão.

    python -m unittest test_assistente -v

Cobre as três coisas que se partem em silêncio: as regras de triagem (a camada
que decide o que nunca custa dinheiro), o tratamento de texto nas duas fronteiras
não confiáveis, e o registo local — incluindo a chave em que assenta.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from aprender import (
    LIMIAR_DIVERGENCIA,
    compor_pergunta,
    LIMIAR_REESCRITA,
    REGUA,
    agrupar,
    classificar,
    formatar_mensagem,
    mesmo_padrao,
    texto_acrescentado,
)

# O que o modelo devolve ao --perguntar: os campos, não a mensagem montada.
RESPOSTA_PERGUNTA = {
    "abertura": "ok",
    "casos": [{"titulo": "t", "cliente": "c", "assistente": "a",
               "enviaste": "e", "pergunta": "p"}],
    "fecho": "f",
}
from aquecer import (
    CHAVE_ULTIMO_AQUECIMENTO,
    MINUTOS_ATE_AQUECER,
    deve_aquecer,
    minutos_desde_ultima_chamada,
)
from exportar import anonimizar, anonimizar_endereco, palpitar
from medir_deriva import comparar_gravado, fechar_ciclo
from verificar_kb import analisar_base

from assistente import (
    CATEGORIAS,
    DOMINIOS_BASE,
    FALHAS_SEGUIDAS_PARA_ALERTA,
    PASSAGENS_ENTRE_AVISOS,
    deve_alertar,
    falhas_seguidas,
    Config,
    Correspondencia,
    Graph,
    _com_retentativa,
    agora,
    compromissos_do_fio,
    decidir,
    emails_iguais,
    gravar_compromisso,
    nota_anexos_ignorados,
    resolver_encomenda,
    resumir_compromissos,
    abrir_db,
    carregar_blocklist,
    cortar_citacao,
    cursor_atual,
    cursor_seguro,
    custo_estimado,
    desembrulhar_formulario_contacto,
    desembrulhar_formulario_devolucao,
    desembrulhar_formularios,
    ETIQUETAS,
    ETIQUETA_URGENTE,
    etiquetas,
    extrair_numero_encomenda,
    extrair_numeros_encomenda,
    gravar_meta,
    ja_processado,
    ler_meta,
    para_html,
    partir_mensagem,
    para_texto,
    processar,
    registar,
    resumir_encomenda,
    resumir_historico,
    saudacao,
    selecionar_anexos_de_imagem,
    verificar_restricao_diaria,
    sem_lixo_apos_assinatura,
    triar,
    triar_cabecalhos,
)

CAIXA = "apoio@loja.pt"


def cfg(**over: object) -> Config:
    base: dict[str, object] = {
        "api_key": "x", "tenant_id": "x", "client_id": "x", "client_secret": "x",
        "shopify_store": "x.myshopify.com", "shopify_client_id": "x",
        "shopify_client_secret": "x",
        "mailbox": CAIXA, "modelo": "claude-sonnet-5",
        "knowledge_dir": Path("knowledge"), "blocklist": Path("blocklist.txt"),
        "db": Path("t.db"), "max_body": 4000, "dry_run": True,
        "fio_mensagens": 8, "fio_chars": 400, "resolver_identidade": True,
        "pre_rascunhos": True, "registo_compromissos": True,
        "respostas_parciais": True, "processar_imagens": True,
        "empresa": "A Loja", "assinatura": "Equipa",
        "cat_rascunho": "IA-Rascunhado", "cat_humano": "Precisa de humano",
        "aviso": "--- rascunho automático ---",
        "outra_caixa_verificacao": "",
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

    def test_formulario_contacto_shopify_passa(self) -> None:
        """mailer@shopify.com com o assunto do formulário não é bloqueado —

        é um cliente real disfarçado de notificação (visto em produção,
        18/08/2026): o replyTo já aponta para o cliente, só falta não
        descartar a mensagem antes de a poder desembrulhar.
        """
        motivo = triar(
            msg(de="mailer@shopify.com",
                assunto="Nova mensagem de cliente em 17/08/2026 às 14:45"),
            cfg(), BLOQUEADOS,
        )
        self.assertIsNone(motivo)

    def test_formulario_contacto_shopify_em_ingles_passa(self) -> None:
        """4 casos reais entre 29/08 e 01/09/2026 ficaram bloqueados como
        dominio-bloqueado:shopify.com por o assunto vir em inglês -- o
        padrão só reconhecia "Nova mensagem de cliente". Este teste é
        literalmente um dos assuntos reais encontrados na base de produção."""
        motivo = triar(
            msg(de="mailer@shopify.com",
                assunto="New customer message on August 29, 2026 at 1:35 pm"),
            cfg(), BLOQUEADOS,
        )
        self.assertIsNone(motivo)

    def test_mailer_shopify_com_outro_assunto_continua_bloqueado(self) -> None:
        """A exceção é só para o formulário de contacto, não para mailer@ em geral."""
        motivo = triar(
            msg(de="mailer@shopify.com", assunto="A tua fatura mensal"),
            cfg(), BLOQUEADOS,
        )
        self.assertTrue(motivo.startswith("dominio-bloqueado"))

    def test_outro_local_shopify_com_assunto_de_formulario_continua_bloqueado(self) -> None:
        """Só mailer@shopify.com tem a exceção, não qualquer remetente do domínio."""
        motivo = triar(
            msg(de="pedidos@shopify.com",
                assunto="Nova mensagem de cliente em 17/08/2026 às 14:45"),
            cfg(), BLOQUEADOS,
        )
        self.assertTrue(motivo.startswith("dominio-bloqueado"))

    def test_formulario_devolucao_formspree_passa(self) -> None:
        """noreply@formspree.io com o assunto do formulário de devolução não é
        bloqueado como remetente automático -- é uma submissão real do
        formulário de devolução do site, disfarçada de notificação (visto em
        produção, 22/08/2026: todas as submissões estavam a ser descartadas
        desde sempre, "noreply" apanhava-as em _ROBOS)."""
        motivo = triar(
            msg(de="noreply@formspree.io", assunto="New submission from Devolução site"),
            cfg(), BLOQUEADOS,
        )
        self.assertIsNone(motivo)

    def test_formspree_com_outro_assunto_continua_bloqueado(self) -> None:
        """A exceção é só para o formulário de devolução, não para o Formspree em geral."""
        motivo = triar(
            msg(de="noreply@formspree.io", assunto="The Missing Step in Your AI-Generated Form"),
            cfg(), BLOQUEADOS,
        )
        self.assertTrue(motivo.startswith("remetente-automatico"))

    def test_outro_local_formspree_com_assunto_de_formulario_continua_bloqueado(self) -> None:
        """Só noreply@formspree.io tem a exceção, não qualquer remetente do domínio."""
        motivo = triar(
            msg(de="newsletter@formspree.io", assunto="New submission from Devolução site"),
            cfg(), BLOQUEADOS,
        )
        self.assertTrue(motivo.startswith("remetente-automatico"))

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

    def test_feedback_id_bloqueia_fora_do_formulario_de_contacto(self) -> None:
        """feedback-id continua a ser sinal de bulk mail para o resto do correio."""
        motivo = triar_cabecalhos(msg(cabecalhos=[("Feedback-ID", "1:2:3:SendGrid")]))
        self.assertEqual(motivo, "cabecalho-massa:feedback-id")

    def test_feedback_id_nao_bloqueia_formulario_de_contacto(self) -> None:
        """A Shopify carimba feedback-id em tudo o que reencaminha, incluindo o
        formulário de contacto -- não é bulk mail aqui (visto em produção,
        20/08/2026: um cliente real ficava descartado em silêncio).

        veio_do_formulario_contacto=True simula o que processar() calcula
        antes de desembrulhar_formulario_contacto() substituir msg["de"] --
        a esta altura, no fluxo real, msg["de"] já não é
        "mailer@shopify.com"."""
        motivo = triar_cabecalhos(
            msg(cabecalhos=[("Feedback-ID", "1:2:3:SendGrid")]),
            veio_do_formulario_contacto=True,
        )
        self.assertIsNone(motivo)

    def test_outro_cabecalho_massa_continua_a_bloquear_formulario_de_contacto(self) -> None:
        """A exceção é só para feedback-id -- outros sinais de bulk mail continuam
        a bloquear o formulário de contacto na mesma."""
        motivo = triar_cabecalhos(
            msg(cabecalhos=[("List-Unsubscribe", "<https://x>")]),
            veio_do_formulario_contacto=True,
        )
        self.assertEqual(motivo, "cabecalho-massa:list-unsubscribe")

    def test_list_unsubscribe_bloqueia_fora_do_formulario_de_devolucao(self) -> None:
        """list-unsubscribe continua a ser sinal de bulk mail para o resto do correio."""
        motivo = triar_cabecalhos(msg(cabecalhos=[("List-Unsubscribe", "<https://x>")]))
        self.assertEqual(motivo, "cabecalho-massa:list-unsubscribe")

    def test_list_unsubscribe_nao_bloqueia_formulario_de_devolucao(self) -> None:
        """O Formspree carimba list-unsubscribe em tudo o que envia, incluindo
        as submissões do formulário de devolução do site -- não é bulk mail
        aqui (visto em produção, 22/08/2026: todas as submissões deste
        formulário estavam a ser descartadas desde sempre)."""
        motivo = triar_cabecalhos(
            msg(cabecalhos=[("List-Unsubscribe", "<https://x>")]),
            veio_do_formulario_devolucao=True,
        )
        self.assertIsNone(motivo)

    def test_outro_cabecalho_massa_continua_a_bloquear_formulario_de_devolucao(self) -> None:
        """A exceção é só para list-unsubscribe -- outros sinais de bulk mail
        continuam a bloquear o formulário de devolução na mesma."""
        motivo = triar_cabecalhos(
            msg(cabecalhos=[("Feedback-ID", "1:2:3:SendGrid")]),
            veio_do_formulario_devolucao=True,
        )
        self.assertEqual(motivo, "cabecalho-massa:feedback-id")

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


class FormularioContactoShopify(unittest.TestCase):
    """Mensagens de cliente reencaminhadas pelo Shopify como mailer@shopify.com.

    O corpo real (visto ao vivo em produção, 18/08/2026, via Graph) é o texto
    já achatado pelo para_texto()/cortar_citacao() do detalhe do email — daí
    o formato "Rótulo:\\n\\nValor" usado nos casos abaixo, não HTML.
    """

    CORPO_REAL = (
        "Recebeu uma nova mensagem do formulário de contacto da sua loja "
        "online.\n\nCódigo do país:\n\nPT\n\nName:\n\nPatrícia Duarte\n\n"
        "E-mail:\n\nduartepatricia2005@hotmail.com\n\nPhone:\n\n915541819"
        "\n\nCorpo:\n\nBoa tarde, já tinha enviado email mas não recebi "
        "mais resposta.\n\nWebsite:\n\n"
    )

    def test_extrai_remetente_real_e_corpo(self) -> None:
        m = msg(de="mailer@shopify.com", nome="tripat3s (Shopify)",
                corpo=self.CORPO_REAL)
        self.assertTrue(desembrulhar_formulario_contacto(m))
        self.assertEqual(m["de"], "duartepatricia2005@hotmail.com")
        self.assertEqual(m["nome"], "Patrícia Duarte")
        self.assertIn("já tinha enviado email", m["corpo"])
        # O campo "Website" do formulário (sempre vazio nos casos reais) não
        # deve ficar pendurado no fim do texto que vai para o modelo.
        self.assertNotIn("Website", m["corpo"])

    def test_corpo_sem_marcador_do_formulario_nao_mexe_na_mensagem(self) -> None:
        """Uma notificação genuína do Shopify não deve ser confundida com isto."""
        original = msg(de="mailer@shopify.com", corpo="Aviso qualquer da plataforma.")
        m = dict(original)
        self.assertFalse(desembrulhar_formulario_contacto(m))
        self.assertEqual(m, original)

    def test_corpo_sem_email_valido_nao_mexe_na_mensagem(self) -> None:
        original = msg(
            de="mailer@shopify.com",
            corpo="Recebeu uma nova mensagem do formulário de contacto da "
                  "sua loja online.\n\nName:\n\nAlguém\n\nCorpo:\n\nOlá.",
        )
        m = dict(original)
        self.assertFalse(desembrulhar_formulario_contacto(m))
        self.assertEqual(m, original)

    def test_versao_inglesa_do_formulario_tambem_se_reconhece(self) -> None:
        """4 casos reais entre 29/08 e 01/09/2026 (assunto "New customer
        message on...") foram descartados como dominio-bloqueado:shopify.com
        antes sequer de chegar aqui -- o assunto só reconhecia português (ver
        _PADRAO_FORMULARIO_CONTACTO). O formato exato do corpo em inglês não
        foi confirmado contra um caso real (os filtros do Graph nesta caixa
        não devolveram um exemplo); isto é a tentativa razoável, calcada no
        formato português conhecido, com "Email"/"Message" em vez de
        "E-mail"/"Corpo"."""
        m = msg(
            de="mailer@shopify.com", nome="tripat3s (Shopify)",
            corpo="You received a new message from your online store's "
                  "contact form.\n\nName:\n\nJohn Doe\n\nEmail:\n\n"
                  "john@example.com\n\nMessage:\n\nHi, still waiting on my "
                  "order.\n\nWebsite:\n\n",
        )
        self.assertTrue(desembrulhar_formulario_contacto(m))
        self.assertEqual(m["de"], "john@example.com")
        self.assertIn("still waiting", m["corpo"])
        self.assertNotIn("Website", m["corpo"])


class FormularioDevolucaoFormspree(unittest.TestCase):
    """Submissões do formulário de devolução do site, reencaminhadas pelo
    Formspree como noreply@formspree.io.

    O corpo real (visto ao vivo em produção, 22/08/2026, via Graph) é uma
    lista plana de "campo\\n\\nvalor\\n\\n" por cada campo do formulário --
    já achatada pelo para_texto() do detalhe do email.
    """

    CORPO_REAL = (
        "You've received a new form submission. --\n\n"
        "New form submission on Devolução site\n\n"
        "Someone just submitted a form on www.tripat3s.com/. "
        "Here's what they had to say:\n\n"
        "numero_pedido\n#21990\n\n"
        "email\nalexandramatiaszz@gmail.com\n\n"
        "nome\nAlexandra Coelho Matias\n\n"
        "telefone\n917003115\n\n"
        "produto\nFone P9 + capa fone\n\n"
        "motivo_principal\nnao_gostei\n\n"
        "onde_erro\nproduto\n\n"
        "descricao\nO som dos fones ouve-se muito no exterior, os fones não "
        "abafam o som e de um lado ouve-se mais do que no outro.\n\n"
        "detalhe_motivo\nA ligação por Bluetooth faz conflito com outros "
        "dispositivos. Não corresponde à realidade descrita no anúncio.\n\n"
        "foto_1\nimage (1).jpg\n\n"
        "foto_2\nimage (2).jpg\n\n"
    )

    def test_extrai_remetente_real_e_campos(self) -> None:
        m = msg(de="noreply@formspree.io", nome="Formspree", corpo=self.CORPO_REAL)
        self.assertTrue(desembrulhar_formulario_devolucao(m))
        self.assertEqual(m["de"], "alexandramatiaszz@gmail.com")
        self.assertEqual(m["nome"], "Alexandra Coelho Matias")
        self.assertIn("#21990", m["corpo"])
        self.assertIn("917003115", m["corpo"])
        self.assertIn("não abafam o som", m["corpo"])
        # Os nomes de ficheiro das fotos não interessam ao modelo -- as fotos
        # em si chegam como anexos, apanhados por processar_imagens().
        self.assertNotIn("image (1).jpg", m["corpo"])

    def test_corpo_sem_email_valido_nao_mexe_na_mensagem(self) -> None:
        original = msg(
            de="noreply@formspree.io",
            corpo="New form submission on Devolução site\n\nnome\nAlguém\n\n",
        )
        m = dict(original)
        self.assertFalse(desembrulhar_formulario_devolucao(m))
        self.assertEqual(m, original)


class DesembrulharFormularios(unittest.TestCase):
    """A função partilhada por processar() e pelas ferramentas offline
    (medir_deriva.py, reprocessar.py, eval.py) -- ver Finding L-2: sem ela,
    essas ferramentas descartavam submissões de formulário que a produção
    processa corretamente, porque nunca calculavam os flags que
    triar_cabecalhos() precisa para as exceções de feedback-id/list-unsubscribe.
    """

    def test_email_normal_nao_e_tocado(self) -> None:
        original = msg()
        m = dict(original)
        contacto, devolucao, motivo = desembrulhar_formularios(m)
        self.assertEqual((contacto, devolucao, motivo), (False, False, None))
        self.assertEqual(m, original)

    def test_formulario_de_contacto_reconhecido(self) -> None:
        m = msg(de="mailer@shopify.com", corpo=FormularioContactoShopify.CORPO_REAL)
        contacto, devolucao, motivo = desembrulhar_formularios(m)
        self.assertEqual((contacto, devolucao, motivo), (True, False, None))
        self.assertEqual(m["de"], "duartepatricia2005@hotmail.com")

    def test_formulario_de_devolucao_reconhecido(self) -> None:
        # eh_formulario_devolucao() exige também que o assunto bata com o
        # padrão do Formspree -- ao contrário do de contacto, que só olha
        # para o remetente.
        m = msg(
            de="noreply@formspree.io",
            assunto="New submission from Devolução site",
            corpo=FormularioDevolucaoFormspree.CORPO_REAL,
        )
        contacto, devolucao, motivo = desembrulhar_formularios(m)
        self.assertEqual((contacto, devolucao, motivo), (False, True, None))
        self.assertEqual(m["de"], "alexandramatiaszz@gmail.com")

    def test_contacto_com_cara_de_formulario_mas_corpo_nao_bate_certo(self) -> None:
        """Remetente mailer@shopify.com sem o corpo do formulário: descarta,
        e os dois flags voltam a False -- a mensagem nunca chega a
        triar_cabecalhos(), por isso não faz sentido dizer que "veio" de lá."""
        m = msg(de="mailer@shopify.com", corpo="Aviso qualquer da plataforma.")
        self.assertEqual(
            desembrulhar_formularios(m),
            (False, False, "formulario-contacto-nao-reconhecido"),
        )

    def test_devolucao_com_cara_de_formulario_mas_corpo_nao_bate_certo(self) -> None:
        m = msg(
            de="noreply@formspree.io",
            assunto="New submission from Devolução site",
            corpo="New form submission on Devolução site\n\nnome\nAlguém\n\n",
        )
        self.assertEqual(
            desembrulhar_formularios(m),
            (False, False, "formulario-devolucao-nao-reconhecido"),
        )


class _RespostaFalsa:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}


class RetentativaHttp(unittest.TestCase):
    """Graph._pedir() (só GET) e Shopify._procurar() partilham isto -- ver
    Finding M-2. Um 429/5xx é transitório e vale a pena repetir; um 4xx
    permanente (token inválido, pedido mal formado) sai já na primeira, sem
    disfarçar o erro real atrás de tentativas inúteis.
    """

    def setUp(self) -> None:
        # As esperas reais (1s, 2s, ...) tornariam esta classe a mais lenta da
        # suite de longe; o que se testa é a lógica de decisão, não o relógio.
        self._sleep = patch("assistente.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_sucede_depois_de_um_503(self) -> None:
        respostas = iter([_RespostaFalsa(503), _RespostaFalsa(200)])
        r = _com_retentativa(lambda: next(respostas))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._sleep.call_count, 1)

    def test_esgota_as_tentativas_e_devolve_o_ultimo_erro(self) -> None:
        chamadas = []

        def pedido() -> _RespostaFalsa:
            chamadas.append(1)
            return _RespostaFalsa(429)

        r = _com_retentativa(pedido, tentativas=3)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(chamadas), 3)

    def test_erro_permanente_nao_repete(self) -> None:
        chamadas = []

        def pedido() -> _RespostaFalsa:
            chamadas.append(1)
            return _RespostaFalsa(404)

        r = _com_retentativa(pedido, tentativas=3)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(chamadas), 1)
        self._sleep.assert_not_called()

    def test_respeita_retry_after_do_429(self) -> None:
        respostas = iter([_RespostaFalsa(429, {"retry-after": "7"}), _RespostaFalsa(200)])
        r = _com_retentativa(lambda: next(respostas))
        self.assertEqual(r.status_code, 200)
        self._sleep.assert_called_once_with(7.0)


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

    def test_corta_citacao_do_gmail(self) -> None:
        """O nome vem primeiro, não "Em"/"On" -- é assim que o Gmail cita.

        Achado num email real de cliente: sem esta regra, a resposta anterior da
        própria loja ficava colada à reclamação nova, e ia parar ao modelo.
        """
        corpo = (
            "Ainda não recebi nada, já passou o prazo.\n\n"
            "Atenciosamente,\nCristina Dias\n\n"
            "tripat3s tripat3s <info@tripat3s.com> escreveu em seg., "
            "10/08/2026 às 19:30 :\n\n"
            "Boa tarde Cristina,\n\nSim, o prazo indicado já foi ultrapassado."
        )
        cortado = cortar_citacao(corpo)
        self.assertIn("Ainda não recebi nada", cortado)
        self.assertNotIn("Boa tarde Cristina", cortado)

    def test_corta_citacao_em_preview_achatado(self) -> None:
        """O bodyPreview do Graph vem numa linha só e sem o <email>.

        Sem isto, uma resposta de quatro palavras trazia colada a mensagem
        inteira que estava a citar, e gastava o orçamento do fio a repetir o
        que já está noutras linhas do histórico.
        """
        preview = (
            "Por mim tudo bem tripat3s tripat3s escreveu em qui., "
            "6/08/2026 às 03:30 : Boa noite, Lamentamos que o problema se mantenha."
        )
        cortado = cortar_citacao(preview)
        self.assertIn("Por mim tudo bem", cortado)
        self.assertNotIn("Lamentamos", cortado)

    def test_nao_corta_escreveu_em_texto_normal(self) -> None:
        # "escreveu em" só marca citação quando vem seguido de data
        texto = "O meu filho escreveu em papel que gostou muito dos fones."
        self.assertEqual(cortar_citacao(texto), texto)

    def test_corta_citacao_achatada_em_ingles(self) -> None:
        cortado = cortar_citacao("Hi, John Smith wrote on Thu, 6/08/2026 at 10:00 : Hello")
        self.assertIn("Hi,", cortado)
        self.assertNotIn("Hello", cortado)

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


class LixoAposAssinatura(unittest.TestCase):
    """Rede de segurança contra um glitch de geração visto em produção:
    "tripat3sascamentoaao_confirmar" em vez de só "tripat3s"."""

    def test_texto_limpo_fica_igual(self) -> None:
        texto = "Com os melhores cumprimentos,\ntripat3s"
        self.assertEqual(sem_lixo_apos_assinatura(texto, "tripat3s"), texto)

    def test_corta_lixo_colado_sem_espaco(self) -> None:
        texto = "Com os melhores cumprimentos,\ntripat3sascamentoaao_confirmar"
        self.assertEqual(
            sem_lixo_apos_assinatura(texto, "tripat3s"),
            "Com os melhores cumprimentos,\ntripat3s",
        )

    def test_assinatura_ausente_fica_igual(self) -> None:
        texto = "Com os melhores cumprimentos,\nEquipa"
        self.assertEqual(sem_lixo_apos_assinatura(texto, "tripat3s"), texto)

    def test_usa_a_ultima_ocorrencia(self) -> None:
        texto = "tripat3s vende fones.\n\nCom os melhores cumprimentos,\ntripat3slixo"
        self.assertTrue(sem_lixo_apos_assinatura(texto, "tripat3s").endswith("tripat3s"))

    def test_texto_ou_assinatura_vazios(self) -> None:
        self.assertEqual(sem_lixo_apos_assinatura("", "tripat3s"), "")
        self.assertEqual(sem_lixo_apos_assinatura("texto", ""), "texto")


class Saudacao(unittest.TestCase):
    """Regra do lojista, 28/08/2026: "Olá" substitui bom dia/boa tarde/boa
    noite -- confirmado a partir de edições reais em produção."""

    def test_e_sempre_ola(self) -> None:
        self.assertEqual(saudacao(), "Olá")

    def test_nao_depende_da_hora(self) -> None:
        # saudacao() já não aceita argumento nenhum -- não há mais uma hora
        # para variar. Chamar sem argumentos várias vezes dá sempre o mesmo.
        self.assertEqual(saudacao(), saudacao())


class NumeroDeEncomenda(unittest.TestCase):
    def test_no_assunto(self) -> None:
        self.assertEqual(
            extrair_numero_encomenda("Encomenda #21910", "sem número aqui"), "21910"
        )

    def test_no_corpo_com_ordinal(self) -> None:
        self.assertEqual(
            extrair_numero_encomenda("Dúvida", "a minha encomenda n.º 21910 não chegou"),
            "21910",
        )

    def test_palavra_encomenda_sem_hash(self) -> None:
        self.assertEqual(
            extrair_numero_encomenda("Estado", "estado da encomenda 21910, por favor"),
            "21910",
        )

    def test_sem_numero(self) -> None:
        self.assertIsNone(extrair_numero_encomenda("Dúvida", "quero saber sobre o produto"))

    def test_numero_curto_nao_conta(self) -> None:
        # com menos de 4 dígitos é demasiado fácil confundir com outra coisa
        # (código postal, quantidade); mais vale não extrair do que extrair mal
        self.assertIsNone(extrair_numero_encomenda("Dúvida", "tenho 42 anos"))

    def test_dois_numeros_no_assunto(self) -> None:
        """Visto em produção, 22/08/2026: cliente com duas devoluções em curso
        mencionou as duas encomendas no assunto, só a primeira era resolvida."""
        self.assertEqual(
            extrair_numeros_encomenda("Pedido de devolução – Encomendas #21039 e #20852", ""),
            ["21039", "20852"],
        )

    def test_um_numero_so_devolve_lista_de_um(self) -> None:
        self.assertEqual(
            extrair_numeros_encomenda("Encomenda #21910", "sem número aqui"), ["21910"]
        )

    def test_numero_repetido_nao_duplica(self) -> None:
        self.assertEqual(
            extrair_numeros_encomenda("Encomenda #21910", "a encomenda 21910 já chegou?"),
            ["21910"],
        )

    def test_sem_numero_devolve_lista_vazia(self) -> None:
        self.assertEqual(extrair_numeros_encomenda("Dúvida", "sem número nenhum"), [])


class ResumoDeEncomenda(unittest.TestCase):
    def test_inclui_rastreio_quando_existe(self) -> None:
        encomenda = {
            "name": "#21910", "created_at": "2026-08-09T10:00:00Z",
            "financial_status": "paid", "fulfillment_status": "fulfilled",
            "fulfillments": [{"tracking_number": "RR123", "tracking_url": "https://t.pt/RR123"}],
        }
        resumo = resumir_encomenda(encomenda)
        self.assertIn("#21910", resumo)
        self.assertIn("pago", resumo)
        self.assertIn("expedida", resumo)
        self.assertIn("RR123", resumo)

    def test_sem_expedicao_nao_inventa_rastreio(self) -> None:
        encomenda = {
            "name": "#21911", "created_at": "2026-08-10T10:00:00Z",
            "financial_status": "pending", "fulfillment_status": None,
            "fulfillments": [],
        }
        resumo = resumir_encomenda(encomenda)
        self.assertIn("ainda não expedida", resumo)
        self.assertIn("pagamento pendente", resumo)
        self.assertNotIn("rastreio", resumo.lower().split("\n")[-1])

    def test_encomenda_cancelada(self) -> None:
        encomenda = {
            "name": "#21912", "created_at": "2026-08-11T10:00:00Z",
            "cancelled_at": "2026-08-12T10:00:00Z",
            "financial_status": "refunded", "fulfillment_status": None,
            "fulfillments": [],
        }
        resumo = resumir_encomenda(encomenda)
        self.assertIn("Cancelada em", resumo)

    def test_inclui_estado_do_envio_quando_a_shopify_o_devolve(self) -> None:
        """A Shopify já devolve isto no fulfillment; só faltava ler.

        Achado ao auditar o pipeline, 18/08/2026: o código só lia
        tracking_number e tracking_url, e descartava shipment_status e
        tracking_company que já vinham na mesma resposta.
        """
        encomenda = {
            "name": "#21920", "created_at": "2026-08-13T10:00:00Z",
            "financial_status": "paid", "fulfillment_status": "fulfilled",
            "fulfillments": [{
                "tracking_number": "RR456", "tracking_url": "https://t.pt/RR456",
                "tracking_company": "CTT", "shipment_status": "in_transit",
            }],
        }
        resumo = resumir_encomenda(encomenda)
        self.assertIn("CTT", resumo)
        self.assertIn("em trânsito", resumo)

    def test_sem_estado_do_envio_nao_inventa_nada(self) -> None:
        """Nem toda transportadora está reconhecida pela Shopify -- sem o
        campo, o resumo continua só com o que já existia."""
        encomenda = {
            "name": "#21921", "created_at": "2026-08-13T10:00:00Z",
            "financial_status": "paid", "fulfillment_status": "fulfilled",
            "fulfillments": [{"tracking_number": "RR789", "tracking_url": "https://t.pt/RR789"}],
        }
        resumo = resumir_encomenda(encomenda)
        self.assertNotIn("Transportadora", resumo)
        self.assertNotIn("Estado do envio", resumo)


class AnexosDeImagem(unittest.TestCase):
    def _anexo(self, **over: object) -> dict:
        base = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "AAMk-anexo-1", "name": "foto.jpg", "contentType": "image/jpeg",
            "size": 500_000, "isInline": False,
        }
        base.update(over)
        return base

    def test_imagem_valida_e_selecionada(self) -> None:
        imagens, ignorados = selecionar_anexos_de_imagem([self._anexo()])
        self.assertEqual(len(imagens), 1)
        self.assertEqual(ignorados, [])

    def test_imagem_inline_e_ignorada_em_silencio(self) -> None:
        """O logótipo da assinatura não é prova nenhuma do cliente."""
        imagens, ignorados = selecionar_anexos_de_imagem([self._anexo(isInline=True)])
        self.assertEqual(imagens, [])
        self.assertEqual(ignorados, [], "inline não deve aparecer nem como ignorado")

    def test_tipo_nao_suportado_fica_ignorado(self) -> None:
        anexo = self._anexo(name="fatura.pdf", contentType="application/pdf")
        imagens, ignorados = selecionar_anexos_de_imagem([anexo])
        self.assertEqual(imagens, [])
        self.assertEqual(len(ignorados), 1)

    def test_imagem_grande_demais_fica_ignorada(self) -> None:
        anexo = self._anexo(size=20_000_000)
        imagens, ignorados = selecionar_anexos_de_imagem([anexo])
        self.assertEqual(imagens, [])
        self.assertEqual(len(ignorados), 1)

    def test_item_attachment_nao_e_foto_do_cliente(self) -> None:
        """Um email encaminhado como anexo, não uma fotografia."""
        anexo = self._anexo(**{"@odata.type": "#microsoft.graph.itemAttachment"})
        imagens, ignorados = selecionar_anexos_de_imagem([anexo])
        self.assertEqual(imagens, [])
        self.assertEqual(ignorados, [])

    def test_limite_de_imagens_por_email(self) -> None:
        anexos = [self._anexo(id=f"a{i}", name=f"foto{i}.jpg") for i in range(10)]
        imagens, _ = selecionar_anexos_de_imagem(anexos)
        self.assertEqual(len(imagens), 4)

    def test_nota_vazia_sem_ignorados(self) -> None:
        self.assertEqual(nota_anexos_ignorados([]), "")

    def test_nota_menciona_nome_e_quantidade(self) -> None:
        nota = nota_anexos_ignorados([self._anexo(name="relatorio.pdf")])
        self.assertIn("relatorio.pdf", nota)
        self.assertIn("1", nota)

    def test_video_pede_fotos_em_vez_de_reenviar(self) -> None:
        """Visto em produção, 22/08/2026: pedir para reenviar 'noutro formato'
        engana o cliente -- nenhum formato de vídeo chega a ser visto."""
        nota = nota_anexos_ignorados(
            [self._anexo(name="problema.mov", contentType="video/quicktime")]
        )
        self.assertIn("fotografias ou capturas de ecrã", nota)

    def test_video_instrui_a_nao_pedir_reenvio(self) -> None:
        nota = nota_anexos_ignorados(
            [self._anexo(name="video.mp4", contentType="video/mp4")]
        )
        self.assertIn("não peças para reenviar", nota)

    def test_video_e_outro_ficheiro_tem_as_duas_notas(self) -> None:
        nota = nota_anexos_ignorados([
            self._anexo(name="problema.mov", contentType="video/quicktime"),
            self._anexo(name="fatura.pdf", contentType="application/pdf"),
        ])
        self.assertIn("fotografias ou capturas de ecrã", nota)
        self.assertIn("fatura.pdf", nota)


class ClienteFalso:
    """Só regista o que decidir() lhe pede, para testar a forma da mensagem
    sem gastar créditos nem depender da rede. O que o modelo decide perante
    uma imagem a sério é testado pelo eval, com fixtures em eval/fixtures/.

    Aceita um dicionário só (a mesma resposta em toda a chamada -- serve
    quando "acao" nunca é "escalar", e por isso decidir() só chama uma vez) ou
    uma lista (uma resposta por chamada, pela ordem: núcleo primeiro, dossiê
    a seguir se escalar).
    """

    def __init__(self, resposta: dict | list[dict], rebenta: bool = False) -> None:
        self.pedidos: list[dict] = []
        self._respostas = resposta if isinstance(resposta, list) else None
        self._resposta_unica = None if isinstance(resposta, list) else resposta
        self._rebenta = rebenta
        self.messages = self

    def create(self, **kwargs: object) -> object:
        self.pedidos.append(kwargs)
        if self._rebenta:
            raise RuntimeError("Anthropic 529: sobrecarregado")
        if self._respostas is not None:
            resposta = self._respostas[len(self.pedidos) - 1]
        else:
            resposta = self._resposta_unica
        bloco = type("Bloco", (), {"type": "text", "text": json.dumps(resposta)})()
        return type("Resposta", (), {"content": [bloco]})()


class DecidirComImagens(unittest.TestCase):
    _DECISAO_SIMPLES = {"acao": "saltar", "motivo": "x", "corpo": "", "categoria": "OUTRO"}

    def test_sem_imagens_envia_string_simples(self) -> None:
        cliente = ClienteFalso(self._DECISAO_SIMPLES)
        decidir(cliente, cfg(), "prompt", msg())
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIsInstance(conteudo, str)

    def test_com_imagens_envia_bloco_de_texto_mais_imagem(self) -> None:
        cliente = ClienteFalso(self._DECISAO_SIMPLES)
        imagens = ({"media_type": "image/png", "data": "dadosbase64"},)
        decidir(cliente, cfg(), "prompt", msg(), imagens=imagens)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIsInstance(conteudo, list)
        self.assertEqual(conteudo[0]["type"], "text")
        self.assertEqual(conteudo[1], {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "dadosbase64"},
        })

    def test_nota_de_anexos_ignorados_entra_no_pedido(self) -> None:
        cliente = ClienteFalso(self._DECISAO_SIMPLES)
        decidir(cliente, cfg(), "prompt", msg(), nota_anexos="\n\nFicheiro não processado.")
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("Ficheiro não processado", conteudo)


class HistoricoDoFio(unittest.TestCase):
    def test_marca_quem_falou(self) -> None:
        saida = resumir_historico(
            [
                {"de": "cliente@gmail.com", "em": "2026-08-13 14:01", "texto": "já tem novidade?"},
                {"de": CAIXA, "em": "2026-08-13 20:00", "texto": "seguem 2 novas unidades"},
            ],
            CAIXA,
        )
        self.assertIn("CLIENTE: já tem novidade?", saida)
        self.assertIn("LOJA: seguem 2 novas unidades", saida)

    def test_remetente_vazio_conta_como_loja(self) -> None:
        saida = resumir_historico(
            [{"de": "", "em": "2026-08-13 20:00", "texto": "resposta da loja"}], CAIXA
        )
        self.assertIn("LOJA:", saida)

    def test_nome_distinto_do_exchange_conta_como_loja(self) -> None:
        """Achado num fio real: a resposta da loja aparecia como CLIENTE.

        O Graph devolve por vezes o X.500 do Exchange em vez do SMTP nas
        mensagens enviadas pela própria caixa. Sem isto, o modelo podia
        atribuir ao cliente compromissos que a loja assumiu.
        """
        saida = resumir_historico(
            [{
                "de": "/o=exchangelabs/ou=exchange administrative group/cn=abc",
                "em": "2026-08-12 14:32",
                "texto": "Alô! Amanhã :)",
            }],
            CAIXA,
        )
        self.assertIn("LOJA: Alô! Amanhã", saida)
        self.assertNotIn("CLIENTE", saida)

    def test_colega_do_mesmo_dominio_conta_como_loja(self) -> None:
        saida = resumir_historico(
            [{"de": f"geral@{CAIXA.partition('@')[2]}", "em": "2026-08-13 10:00",
              "texto": "seguimos amanhã"}],
            CAIXA,
        )
        self.assertIn("LOJA:", saida)

    def test_cliente_continua_a_ser_cliente(self) -> None:
        saida = resumir_historico(
            [{"de": "pessoa@gmail.com", "em": "2026-08-13 10:00", "texto": "olá"}], CAIXA
        )
        self.assertIn("CLIENTE:", saida)

    def test_fio_vazio(self) -> None:
        self.assertEqual(resumir_historico([], CAIXA), "")

    def test_ignora_mensagens_sem_texto(self) -> None:
        saida = resumir_historico(
            [
                {"de": "cliente@gmail.com", "em": "2026-08-13 14:01", "texto": "   "},
                {"de": "cliente@gmail.com", "em": "2026-08-13 15:00", "texto": "obrigado"},
            ],
            CAIXA,
        )
        self.assertEqual(len(saida.strip().split("\n")), 1)

    def test_achata_quebras_de_linha(self) -> None:
        saida = resumir_historico(
            [{"de": "c@x.pt", "em": "2026-08-13 14:01", "texto": "olá\n\nboa tarde"}], CAIXA
        )
        self.assertNotIn("\n\n", saida)
        self.assertIn("olá boa tarde", saida)


class ShopifyFalsa:
    """Dublê da Shopify. Não há rede nos testes."""

    def __init__(self, por_numero=None, por_email=None, rebenta=False, data_entrega=None):
        self._numero = por_numero or []
        self._email = por_email or []
        self._rebenta = rebenta
        self._data_entrega = data_entrega
        self.chamadas = []

    def por_numero(self, numero):
        self.chamadas.append(("numero", numero))
        if self._rebenta:
            raise RuntimeError("Shopify 500")
        return list(self._numero)

    def por_email(self, email):
        self.chamadas.append(("email", email))
        if self._rebenta:
            raise RuntimeError("Shopify 500")
        return list(self._email)

    def encomenda(self, numero, email_remetente):
        """Modo de compatibilidade (ENABLE_ORDER_IDENTITY_RESOLUTION=false):
        espelha Shopify.encomenda() -- número mais email exato da compra,
        sobre os mesmos resultados de por_numero()."""
        self.chamadas.append(("encomenda", numero, email_remetente))
        if self._rebenta:
            raise RuntimeError("Shopify 500")
        for enc in self._numero:
            if emails_iguais(enc.get("email"), email_remetente) or emails_iguais(
                enc.get("contact_email"), email_remetente
            ):
                return enc
        return None

    def data_entrega(self, order_id, fulfillment_id):
        self.chamadas.append(("data_entrega", order_id, fulfillment_id))
        if self._rebenta:
            raise RuntimeError("Shopify 500")
        return self._data_entrega


def encomenda_falsa(**over):
    base = {
        "name": "#21910",
        "email": "cliente@gmail.com",
        "created_at": "2026-08-10T10:00:00Z",
        "financial_status": "paid",
        "fulfillment_status": "fulfilled",
        "fulfillments": [],
        "customer": {"first_name": "Marta", "last_name": "Pinho", "phone": None},
        "shipping_address": {"zip": "2620-537", "phone": None},
    }
    base.update(over)
    return base


class ResolucaoDeIdentidade(unittest.TestCase):
    """A regra mais cara de violar: nunca mostrar a encomenda de outra pessoa."""

    def test_numero_e_email_da_compra_e_exata(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", "21910")
        self.assertEqual(r.confianca, "exata")
        self.assertTrue(r.pode_revelar)

    def test_numero_com_email_diferente_sem_indicios_nao_revela(self) -> None:
        # O caso perigoso: o número não é segredo, qualquer um o pode citar.
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        r = resolver_encomenda(s, msg(de="outra.pessoa@gmail.com"), "", "21910")
        self.assertEqual(r.confianca, "media")
        self.assertFalse(r.pode_revelar)

    def test_numero_com_email_diferente_mas_nome_completo_bate(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        m = msg(de="marta.p@outro.pt", nome="Marta Pinho")
        r = resolver_encomenda(s, m, "", "21910")
        self.assertEqual(r.confianca, "alta")
        self.assertTrue(r.pode_revelar)
        self.assertIn("nome_completo_do_remetente", r.razoes)

    def test_primeiro_nome_sozinho_nao_chega(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        m = msg(de="marta@outro.pt", nome="Marta")
        r = resolver_encomenda(s, m, "", "21910")
        self.assertFalse(r.pode_revelar)

    def test_codigo_postal_no_texto_conta_como_indicio(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        m = msg(de="outro@gmail.com", nome="X", corpo="moro no 2620-537, é essa")
        r = resolver_encomenda(s, m, "", "21910")
        self.assertEqual(r.confianca, "alta")
        self.assertIn("codigo_postal_no_texto", r.razoes)

    def test_telefone_no_texto_conta_como_indicio(self) -> None:
        enc = encomenda_falsa(customer={"first_name": "A", "last_name": "B",
                                        "phone": "+351 912345678"})
        s = ShopifyFalsa(por_numero=[enc])
        m = msg(de="outro@gmail.com", nome="X", corpo="o meu contacto e 912345678")
        r = resolver_encomenda(s, m, "", "21910")
        self.assertEqual(r.confianca, "alta")

    def test_varios_candidatos_nunca_escolhe(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa(), encomenda_falsa(name="#21911")])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", "21910")
        self.assertIsNone(r.encomenda)
        self.assertFalse(r.pode_revelar)
        self.assertIn("varios_candidatos", r.razoes)

    def test_varios_candidatos_com_email_a_bater_leva_opcoes(self) -> None:
        """O email do remetente confirma que são todas dele -- diferente do
        caso sem nenhum email a bater, aqui há uma lista segura (número +
        data) para o cliente escolher, em vez do silêncio total."""
        s = ShopifyFalsa(por_numero=[
            encomenda_falsa(name="#21910", created_at="2026-08-10T10:00:00Z"),
            encomenda_falsa(name="#21910", created_at="2026-08-20T10:00:00Z"),
        ])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", "21910")
        self.assertFalse(r.pode_revelar)
        self.assertEqual(r.opcoes, (("#21910", "2026-08-10"), ("#21910", "2026-08-20")))

    def test_varios_candidatos_sem_email_a_bater_nao_leva_opcoes(self) -> None:
        s = ShopifyFalsa(por_numero=[
            encomenda_falsa(name="#21910", email="a@a.pt"),
            encomenda_falsa(name="#21910", email="b@b.pt"),
        ])
        r = resolver_encomenda(s, msg(de="curioso@gmail.com"), "", "21910")
        self.assertFalse(r.pode_revelar)
        self.assertEqual(r.opcoes, ())

    def test_sem_numero_mas_email_com_uma_encomenda(self) -> None:
        # Capacidade nova: antes disto, sem número nunca se procurava.
        s = ShopifyFalsa(por_email=[encomenda_falsa()])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", None)
        self.assertEqual(r.confianca, "alta")
        self.assertTrue(r.pode_revelar)

    def test_sem_numero_e_email_com_varias_encomendas_escala(self) -> None:
        s = ShopifyFalsa(por_email=[encomenda_falsa(), encomenda_falsa(name="#22000")])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", None)
        self.assertIsNone(r.encomenda)
        self.assertFalse(r.pode_revelar)
        # O email já é a prova de identidade (é o que revela quando há só uma
        # correspondência) -- ter mais do que uma leva às opções, não ao
        # silêncio total do "media" ou do número sem email a bater.
        self.assertEqual(r.opcoes, (("#21910", "2026-08-10"), ("#22000", "2026-08-10")))

    def test_sem_numero_e_email_desconhecido(self) -> None:
        s = ShopifyFalsa()
        r = resolver_encomenda(s, msg(de="ninguem@gmail.com"), "", None)
        self.assertIsNone(r.encomenda)
        self.assertEqual(r.confianca, "nenhuma")

    def test_numero_sem_correspondencia(self) -> None:
        s = ShopifyFalsa(por_numero=[])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", "99999")
        self.assertIsNone(r.encomenda)
        self.assertIn("numero_sem_correspondencia", r.razoes)

    def test_contact_email_tambem_conta_como_exato(self) -> None:
        enc = encomenda_falsa(email="", contact_email="cliente@gmail.com")
        s = ShopifyFalsa(por_numero=[enc])
        r = resolver_encomenda(s, msg(de="cliente@gmail.com"), "", "21910")
        self.assertEqual(r.confianca, "exata")

    def test_numero_vindo_do_historico_do_fio(self) -> None:
        s = ShopifyFalsa(por_numero=[encomenda_falsa()])
        m = msg(de="cliente@gmail.com", corpo="e quando envia?")
        r = resolver_encomenda(s, m, "[LOJA] sobre a encomenda 21910", "21910")
        self.assertTrue(r.pode_revelar)


class EmailsIguais(unittest.TestCase):
    def test_ignora_maiusculas_e_espacos(self) -> None:
        self.assertTrue(emails_iguais(" Cliente@Gmail.com ", "cliente@gmail.com"))

    def test_vazio_nunca_e_igual(self) -> None:
        self.assertFalse(emails_iguais("", ""))
        self.assertFalse(emails_iguais(None, "a@b.pt"))


class Taxonomia(unittest.TestCase):
    def test_categorias_sao_unicas_e_maiusculas(self) -> None:
        self.assertEqual(len(CATEGORIAS), len(set(CATEGORIAS)))
        for c in CATEGORIAS:
            self.assertEqual(c, c.upper())

    def test_outro_existe_como_escape(self) -> None:
        self.assertIn("OUTRO", CATEGORIAS)


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


class CursorSeguro(unittest.TestCase):
    """Uma falha a meio do lote não pode deixar o cursor à frente dela.

    O registar() de uma mensagem posterior empurrava o cursor para diante da
    que falhou, e a passagem seguinte -- que só pede o que veio depois do
    cursor -- nunca mais a via: sem rascunho, sem categoria e sem registo.
    """

    INICIAL = "2026-08-06T08:00:00Z"

    def test_sem_falhas_avanca_ate_a_ultima(self) -> None:
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T10:00:00Z", "rascunhado"),
            ("2026-08-06T10:01:00Z", "escalado"),
            ("2026-08-06T10:02:00Z", "saltado"),
        ])
        self.assertEqual(seguro, "2026-08-06T10:02:00Z")

    def test_falha_a_meio_para_antes_dela(self) -> None:
        """O caso que perdia o email: falha às 10:01, sucesso às 10:02."""
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T10:00:00Z", "rascunhado"),
            ("2026-08-06T10:01:00Z", "falhado"),
            ("2026-08-06T10:02:00Z", "escalado"),
        ])
        # Tem de ficar ANTES das 10:01, para essa voltar a aparecer.
        self.assertEqual(seguro, "2026-08-06T10:00:00Z")
        self.assertLess(seguro, "2026-08-06T10:01:00Z")

    def test_falha_na_primeira_nao_avanca_nada(self) -> None:
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T10:00:00Z", "falhado"),
            ("2026-08-06T10:01:00Z", "rascunhado"),
        ])
        self.assertEqual(seguro, self.INICIAL)

    def test_duas_falhas_para_na_mais_antiga(self) -> None:
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T10:00:00Z", "escalado"),
            ("2026-08-06T10:01:00Z", "falhado"),
            ("2026-08-06T10:02:00Z", "falhado"),
        ])
        self.assertEqual(seguro, "2026-08-06T10:00:00Z")

    def test_repetido_conta_como_tratado(self) -> None:
        """Já está no registo; passar à frente dele não perde nada."""
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T10:00:00Z", "repetido"),
            ("2026-08-06T10:01:00Z", "rascunhado"),
        ])
        self.assertEqual(seguro, "2026-08-06T10:01:00Z")

    def test_lote_vazio_deixa_o_cursor_quieto(self) -> None:
        self.assertEqual(cursor_seguro(self.INICIAL, []), self.INICIAL)

    def test_nunca_recua_para_antes_do_cursor_inicial(self) -> None:
        """Defesa contra ordem inesperada do Graph: uma mensagem mais antiga
        que o cursor não o pode puxar para trás."""
        seguro = cursor_seguro(self.INICIAL, [
            ("2026-08-06T07:00:00Z", "saltado"),
            ("2026-08-06T09:00:00Z", "rascunhado"),
        ])
        self.assertEqual(seguro, "2026-08-06T09:00:00Z")


class GraphFalso:
    """Dublê de Graph. Cada método aceita uma exceção para levantar em vez do
    resultado normal -- o que chega mais perto de como cada chamada real
    falha (RuntimeError com o código HTTP no texto). self.chamadas regista
    tudo o que foi invocado, na ordem, para os testes verificarem o que
    processar() decidiu chamar.
    """

    # Estático de verdade em Graph; delega em vez de duplicar a conversão.
    _converter = staticmethod(Graph._converter)

    def __init__(
        self,
        resultado: Exception | None = None,
        detalhe: Exception | None = None,
        anexos: list[dict] | Exception | None = None,
        conteudo_anexo: bytes | Exception = b"",
        historico: list[dict] | Exception | None = None,
        rascunho_id: str = "AAMk-fake",
        criar_rascunho: Exception | None = None,
        marcar: Exception | None = None,
        detalhe_rascunho: dict | None | Exception = "omisso",
        pedir_respostas: dict[str, dict] | None = None,
    ) -> None:
        # `resultado`: usado só por verificar_restricao_diaria() via _pedir()
        # -- None simula sucesso (200, a caixa foi lida, o cenário mau).
        self._resultado = resultado
        self._detalhe = detalhe
        self._anexos = anexos if anexos is not None else []
        self._conteudo_anexo = conteudo_anexo
        self._historico = historico if historico is not None else []
        self._rascunho_id = rascunho_id
        self._erro_criar_rascunho = criar_rascunho
        self._erro_marcar = marcar
        # "omisso" (sentinela) em vez de None, porque None é uma resposta
        # válida de detalhe_rascunho() (mensagem apagada).
        self._detalhe_rascunho = detalhe_rascunho
        self._pedir_respostas = pedir_respostas or {}
        # Só usado para montar URLs em chamadas que o dublê não intercepta
        # diretamente (buscar_email(), resposta_real() em medir_deriva.py) --
        # o conteúdo não importa, _pedir() decide a resposta pelo $filter.
        self.base = "https://graph.example/v1.0/me"
        self.chamadas: list[tuple[object, ...]] = []

    def _pedir(self, metodo: str, url: str, **kw: object) -> dict:
        self.chamadas.append(("_pedir", metodo))
        if self._resultado is not None:
            raise self._resultado
        # Deixa configurar respostas diferentes por chave (ex.: um message_id
        # ou conversation_id), procurada dentro do $filter do pedido -- usado
        # por comparar_gravado(), que faz duas procuras diferentes na mesma
        # chamada (por internetMessageId, depois por conversationId).
        filtro = str((kw.get("params") or {}).get("$filter", ""))
        for chave, resposta in self._pedir_respostas.items():
            if chave in filtro:
                return resposta
        return {"value": [{"id": "x"}]}

    def detalhe(self, msg: dict, max_body: int) -> dict:
        self.chamadas.append(("detalhe", msg["message_id"]))
        if isinstance(self._detalhe, Exception):
            raise self._detalhe
        msg.setdefault("corpo", "")
        msg.setdefault("cabecalhos", [])
        return msg

    def anexos(self, msg: dict) -> list[dict]:
        self.chamadas.append(("anexos", msg["message_id"]))
        if isinstance(self._anexos, Exception):
            raise self._anexos
        return list(self._anexos)

    def conteudo_anexo(self, msg: dict, anexo_id: str) -> bytes:
        self.chamadas.append(("conteudo_anexo", anexo_id))
        if isinstance(self._conteudo_anexo, Exception):
            raise self._conteudo_anexo
        return self._conteudo_anexo

    def historico(self, msg: dict, quantas: int, max_chars: int) -> list[dict]:
        self.chamadas.append(("historico", msg["message_id"]))
        if isinstance(self._historico, Exception):
            raise self._historico
        return list(self._historico)

    def criar_rascunho(self, message_id: str, corpo_html: str) -> str:
        self.chamadas.append(("criar_rascunho", message_id, corpo_html))
        if self._erro_criar_rascunho is not None:
            raise self._erro_criar_rascunho
        return self._rascunho_id

    def marcar(self, msg: dict, *categorias: str) -> None:
        # Uma entrada por categoria, para os testes antigos (que esperam
        # ("marcar", id, "Precisa de humano")) continuarem a ler-se bem quando
        # a chamada real leva também o tipo de caso e a urgência.
        for categoria in categorias:
            self.chamadas.append(("marcar", msg["message_id"], categoria))
        if self._erro_marcar is not None:
            raise self._erro_marcar

    def detalhe_rascunho(self, rascunho_id: str) -> dict | None:
        self.chamadas.append(("detalhe_rascunho", rascunho_id))
        if isinstance(self._detalhe_rascunho, Exception):
            raise self._detalhe_rascunho
        if self._detalhe_rascunho == "omisso":
            return None
        return self._detalhe_rascunho


class VerificarRestricaoDiaria(unittest.TestCase):
    """P0-2: a única defesa automática contra a política de acesso do
    Exchange ser removida. Ver Finding P0-2 em docs/07-roadmap/improvements.md.
    """

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def test_sem_outra_caixa_configurada_nao_faz_nada(self) -> None:
        c = cfg(outra_caixa_verificacao="")
        g = GraphFalso(RuntimeError("Graph 403: negado"))
        verificar_restricao_diaria(self.con, c, g)
        self.assertEqual(len(g.chamadas), 0)

    def test_403_confirma_a_restricao_e_grava_a_data(self) -> None:
        c = cfg(outra_caixa_verificacao="colega@tripat3s.com")
        g = GraphFalso(RuntimeError("Graph 403: negado"))
        verificar_restricao_diaria(self.con, c, g)
        self.assertEqual(len(g.chamadas), 1)
        self.assertEqual(ler_meta(self.con, "ultima-verificacao-seguranca"), agora()[:10])

    def test_404_nao_prova_nada_mas_nao_repete_no_mesmo_dia(self) -> None:
        c = cfg(outra_caixa_verificacao="pessoa-que-ja-saiu@tripat3s.com")
        g = GraphFalso(RuntimeError("Graph 404: não encontrado"))
        verificar_restricao_diaria(self.con, c, g)
        self.assertEqual(ler_meta(self.con, "ultima-verificacao-seguranca"), agora()[:10])

    def test_erro_diferente_nao_grava_data_tenta_outra_vez_depois(self) -> None:
        c = cfg(outra_caixa_verificacao="colega@tripat3s.com")
        g = GraphFalso(RuntimeError("Graph 500: falha do servidor"))
        verificar_restricao_diaria(self.con, c, g)
        self.assertEqual(ler_meta(self.con, "ultima-verificacao-seguranca"), "")

    def test_ja_verificado_hoje_nao_chama_o_graph_outra_vez(self) -> None:
        c = cfg(outra_caixa_verificacao="colega@tripat3s.com")
        gravar_meta(self.con, "ultima-verificacao-seguranca", agora()[:10])
        g = GraphFalso(RuntimeError("Graph 403: negado"))
        verificar_restricao_diaria(self.con, c, g)
        self.assertEqual(len(g.chamadas), 0)

    def test_sucesso_a_ler_a_outra_caixa_e_um_alarme_de_seguranca(self) -> None:
        """O cenário que a verificação existe para apanhar: a política de
        acesso deixou de restringir, e a aplicação leu uma caixa que não é a
        sua."""
        c = cfg(outra_caixa_verificacao="colega@tripat3s.com")
        g = GraphFalso(None)
        with self.assertRaises(SystemExit) as ctx:
            verificar_restricao_diaria(self.con, c, g)
        self.assertIn("ALERTA DE SEGURANÇA", str(ctx.exception))
        # De propósito, não fica gravado como "verificado" -- não seria
        # verdade, e mascararia o alarme se alguém corrigir a política e a
        # passagem seguinte relançar a verificação no mesmo dia.
        self.assertEqual(ler_meta(self.con, "ultima-verificacao-seguranca"), "")


class RegistoDeCompromissos(unittest.TestCase):
    """Sobrevive fora da janela do fio: é o problema real que resolve."""

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def test_sem_compromissos_devolve_vazio(self) -> None:
        self.assertEqual(compromissos_do_fio(self.con, "conv-1"), [])
        self.assertEqual(resumir_compromissos([]), "")

    def test_grava_e_relê(self) -> None:
        gravar_compromisso(self.con, "conv-1", "substituicao",
                           "enviar novo par de fones", "pendente", "")
        compromissos = compromissos_do_fio(self.con, "conv-1")
        self.assertEqual(len(compromissos), 1)
        self.assertEqual(compromissos[0]["tipo"], "substituicao")
        self.assertIn("enviar novo par", compromissos[0]["descricao"])

    def test_sem_data_nunca_inventa_uma(self) -> None:
        gravar_compromisso(self.con, "conv-1", "envio", "vai seguir", "pendente", "")
        c = compromissos_do_fio(self.con, "conv-1")[0]
        self.assertEqual(c["data"], "")
        self.assertIn("sem data confirmada", resumir_compromissos([c]))

    def test_atualizar_o_mesmo_tipo_substitui_nao_duplica(self) -> None:
        gravar_compromisso(self.con, "conv-1", "reembolso", "a processar",
                           "pendente", "")
        gravar_compromisso(self.con, "conv-1", "reembolso", "já emitido",
                           "concluido", "")
        compromissos = self.con.execute(
            "SELECT COUNT(*) FROM compromissos WHERE conversation_id='conv-1'"
        ).fetchone()[0]
        self.assertEqual(compromissos, 1)

    def test_concluido_nao_aparece_como_pendente(self) -> None:
        # compromissos_do_fio só devolve pendentes: um caso fechado não deve
        # continuar a aparecer como algo por cumprir.
        gravar_compromisso(self.con, "conv-1", "reembolso", "feito",
                           "concluido", "")
        self.assertEqual(compromissos_do_fio(self.con, "conv-1"), [])

    def test_conversas_diferentes_nao_se_misturam(self) -> None:
        gravar_compromisso(self.con, "conv-1", "envio", "A", "pendente", "")
        gravar_compromisso(self.con, "conv-2", "envio", "B", "pendente", "")
        self.assertEqual(len(compromissos_do_fio(self.con, "conv-1")), 1)
        self.assertEqual(compromissos_do_fio(self.con, "conv-1")[0]["descricao"], "A")

    def test_tipo_nenhum_nunca_se_grava(self) -> None:
        gravar_compromisso(self.con, "conv-1", "nenhum", "x", "pendente", "")
        self.assertEqual(compromissos_do_fio(self.con, "conv-1"), [])

    def test_conversation_id_vazio_nao_grava(self) -> None:
        gravar_compromisso(self.con, "", "envio", "x", "pendente", "")
        n = self.con.execute("SELECT COUNT(*) FROM compromissos").fetchone()[0]
        self.assertEqual(n, 0)

    def test_data_confirmada_aparece_no_resumo(self) -> None:
        gravar_compromisso(self.con, "conv-1", "envio", "duas unidades novas",
                           "pendente", "2026-08-20")
        resumo = resumir_compromissos(compromissos_do_fio(self.con, "conv-1"))
        self.assertIn("2026-08-20", resumo)


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


class Processar(unittest.TestCase):
    """O caminho completo de um email -- Finding H-2. Concentra o maior
    número de ramos do sistema (10 pontos de retorno) e, até aqui, não tinha
    um único teste: nem test_assistente.py a importava, nem o eval.py a
    exercita (chama decidir() diretamente, com dados_encomenda já pronto).

    Os dublês (GraphFalso, ShopifyFalsa, ClienteFalso) já existiam para
    testar peças isoladas -- aqui ligam-se as três ao mesmo tempo, tal como
    processar() as usa a sério.
    """

    _SALTAR = {"acao": "saltar", "motivo": "não é cliente", "corpo": "", "categoria": "OUTRO"}
    _RASCUNHAR = {
        "acao": "rascunhar", "motivo": "sabia responder",
        "corpo": "Boa tarde,\n\nAs entregas demoram 24 a 48 horas.\n\nCom os melhores cumprimentos,\nA Loja",
        "categoria": "OUTRO",
    }
    _RASCUNHAR_PARCIAL = {**_RASCUNHAR, "por_responder": "pediu também um desconto por fidelidade"}
    _RASCUNHAR_CORPO_VAZIO = {"acao": "rascunhar", "motivo": "x", "corpo": "   ",
                              "categoria": "JULGAMENTO_HUMANO"}
    # Escalar traz a resposta de retenção no próprio "corpo": desde que o
    # dossiê foi removido, é uma chamada só e é este o texto que vira rascunho.
    _ESCALAR = {
        "acao": "escalar", "motivo": "pede cancelamento",
        "corpo": "Boa tarde,\n\nVamos verificar se conseguimos cancelar.",
        "categoria": "ACAO_SOBRE_ENCOMENDA",
    }
    # Sem nada seguro a dizer: o corpo fica vazio e não se cria rascunho.
    _ESCALAR_SEM_RESPOSTA = {
        "acao": "escalar", "motivo": "não está na base", "corpo": "",
        "categoria": "LACUNA_DE_CONHECIMENTO",
        "lacuna_tema": "prazo Madeira", "lacuna_em_falta": "se é diferente do continente",
    }
    _ESCALAR_URGENTE = {**_ESCALAR, "urgencia": "sim", "categoria": "JULGAMENTO_HUMANO"}

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)
        self.bloqueados = BLOQUEADOS

    def _correr(self, m, c, cliente, graph=None, shopify=None):
        graph = graph if graph is not None else GraphFalso()
        shopify = shopify if shopify is not None else ShopifyFalsa()
        return processar(m, c, graph, shopify, self.con, cliente, "prompt", self.bloqueados), graph, shopify

    def _linha(self, message_id: str) -> dict:
        cols = ("message_id", "acao", "motivo", "corpo", "categoria",
                "por_responder", "lacuna_tema", "rascunho_id", "urgencia")
        linha = self.con.execute(
            f"SELECT {', '.join(cols)} FROM processados WHERE message_id = ?", (message_id,)
        ).fetchone()
        return dict(zip(cols, linha)) if linha else {}

    # --- Antes de chegar ao modelo -------------------------------------

    def test_repetido_nao_toca_em_nada(self) -> None:
        m = msg()
        registar(self.con, m, "saltar", "já visto", "")
        resultado, graph, shopify = self._correr(m, cfg(), ClienteFalso(self._SALTAR))
        self.assertEqual(resultado, "repetido")
        self.assertEqual(graph.chamadas, [])
        self.assertEqual(shopify.chamadas, [])

    def test_saltado_pela_triagem_nunca_chega_ao_detalhe(self) -> None:
        m = msg(de="newsletter@algumaloja.com")
        resultado, graph, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR))
        self.assertEqual(resultado, "saltado")
        self.assertEqual(graph.chamadas, [])  # nunca chegou a pedir detalhe()
        self.assertEqual(self._linha(m["message_id"])["motivo"], "remetente-automatico:newsletter")

    def test_mensagem_apagada_a_meio_e_saltada_sem_derrubar(self) -> None:
        """O bug de produção de 26/08/2026 (ver Finding C-1 / cursor_seguro):
        o Graph devolve 404 ao pedir o detalhe porque a mensagem já não está
        lá. Salta-se só esta, sem propagar a exceção."""
        m = msg()
        graph = GraphFalso(detalhe=RuntimeError("Graph 404: ErrorItemNotFound"))
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR), graph=graph)
        self.assertEqual(resultado, "saltado")
        self.assertEqual(self._linha(m["message_id"])["motivo"],
                          "mensagem-desapareceu-antes-do-detalhe")

    def test_erro_no_detalhe_que_nao_e_404_propaga(self) -> None:
        """Só o 404 tem tratamento especial. Um 500 (ou qualquer outro) tem
        de continuar a propagar -- é um erro genuíno, não "a mensagem já não
        está lá"."""
        m = msg()
        graph = GraphFalso(detalhe=RuntimeError("Graph 500: falha do servidor"))
        with self.assertRaises(RuntimeError):
            self._correr(m, cfg(), ClienteFalso(self._SALTAR), graph=graph)

    def test_formulario_de_contacto_nao_reconhecido_e_saltado(self) -> None:
        m = msg(
            de="mailer@shopify.com", nome="tripat3s (Shopify)",
            assunto="Nova mensagem de cliente da loja",
            corpo="Aviso qualquer da plataforma, não é o formulário.",
        )
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR))
        self.assertEqual(resultado, "saltado")
        self.assertEqual(self._linha(m["message_id"])["motivo"],
                          "formulario-contacto-nao-reconhecido")

    def test_saltado_pelos_cabecalhos_depois_do_detalhe(self) -> None:
        """Diferente da triagem inicial: só se decide depois de ir buscar o
        detalhe (cabeçalhos não vêm na listagem)."""
        m = msg(cabecalhos=[("Precedence", "bulk")])
        resultado, graph, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR))
        self.assertEqual(resultado, "saltado")
        self.assertEqual(len(graph.chamadas), 1)  # chegou a pedir detalhe()
        self.assertEqual(self._linha(m["message_id"])["motivo"], "precedence-massa")

    # --- Anexos e histórico: falhas isoladas não impedem a decisão ------

    def test_erro_a_ir_buscar_anexos_nao_impede_a_decisao(self) -> None:
        m = msg(tem_anexos=True)
        graph = GraphFalso(anexos=RuntimeError("Graph 500"))
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR), graph=graph)
        self.assertEqual(resultado, "saltado")  # decidiu na mesma, sem imagem nenhuma

    def test_anexo_de_imagem_valido_chega_ao_pedido_do_modelo(self) -> None:
        m = msg(tem_anexos=True)
        graph = GraphFalso(
            anexos=[{
                "id": "a1", "contentType": "image/png", "size": 100,
                "@odata.type": "#microsoft.graph.fileAttachment", "isInline": False,
            }],
            conteudo_anexo=b"bytes-da-imagem",
        )
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, graph=graph)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIsInstance(conteudo, list)
        self.assertEqual(conteudo[1]["type"], "image")

    def test_erro_a_ir_buscar_historico_nao_impede_a_decisao(self) -> None:
        m = msg(conversation_id="conv-1")
        graph = GraphFalso(historico=RuntimeError("Graph 500"))
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR), graph=graph)
        self.assertEqual(resultado, "saltado")

    def test_historico_do_fio_chega_ao_pedido_do_modelo(self) -> None:
        m = msg(conversation_id="conv-1")
        graph = GraphFalso(historico=[
            {"de": "cliente@gmail.com", "em": "2026-08-01 10:00", "texto": "sabe quando envia?"},
        ])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, graph=graph)
        self.assertIn("sabe quando envia?", cliente.pedidos[0]["messages"][0]["content"])

    def test_compromisso_registado_chega_ao_pedido_do_modelo(self) -> None:
        gravar_compromisso(self.con, "conv-1", "reembolso", "39,90€ prometidos", "pendente", "")
        m = msg(conversation_id="conv-1")
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(registo_compromissos=True), cliente)
        self.assertIn("39,90€ prometidos", cliente.pedidos[0]["messages"][0]["content"])

    # --- Identidade e dados da encomenda ---------------------------------

    def test_modo_compatibilidade_encontra_por_numero_mais_email(self) -> None:
        m = msg(corpo="Boa tarde, a encomenda 21910 ainda não chegou.")
        shopify = ShopifyFalsa(por_numero=[encomenda_falsa(name="#21910", email="cliente@gmail.com")])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(resolver_identidade=False), cliente, shopify=shopify)
        self.assertIn("#21910", cliente.pedidos[0]["messages"][0]["content"])

    def test_resolucao_por_niveis_dados_chegam_ao_pedido(self) -> None:
        m = msg(corpo="Boa tarde, a encomenda 21910 ainda não chegou.")
        shopify = ShopifyFalsa(por_numero=[encomenda_falsa(name="#21910", email="cliente@gmail.com")])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(resolver_identidade=True), cliente, shopify=shopify)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("#21910", conteudo)
        self.assertIn("Dados da encomenda", conteudo)

    def test_erro_na_shopify_nao_impede_a_decisao(self) -> None:
        m = msg(corpo="Encomenda 21910, ainda não chegou.")
        shopify = ShopifyFalsa(rebenta=True)
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR), shopify=shopify)
        self.assertEqual(resultado, "saltado")  # decidiu na mesma, sem dados

    def test_confianca_media_avisa_sem_revelar_dados(self) -> None:
        """Existe encomenda com o número, mas o email não bate certo e não há
        outro indício -- não se revela nada, só se avisa."""
        m = msg(de="curioso@gmail.com", corpo="Encomenda 21910, qual o estado?")
        shopify = ShopifyFalsa(por_numero=[encomenda_falsa(name="#21910", email="dona@gmail.com")])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, shopify=shopify)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("não foi possível confirmar", conteudo)
        self.assertIn("IDENTIDADE_NAO_VERIFICADA", conteudo)
        self.assertNotIn("2026-08-10", conteudo)  # created_at da encomenda_falsa -- não vaza

    def test_numero_sem_correspondencia_avisa_dados_em_falta(self) -> None:
        m = msg(corpo="Encomenda 30402, ainda não chegou.")
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, shopify=ShopifyFalsa(por_numero=[]))
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("DADOS_ENCOMENDA_EM_FALTA", conteudo)

    def test_varias_encomendas_do_mesmo_email_pede_para_especificar_sem_escalar(self) -> None:
        """Diferente de 'media' ou de um número sem correspondência: aqui o
        email de quem escreveu bate com as duas encomendas -- a identidade
        já está provada, só falta saber qual delas. Não é suposto forçar
        IDENTIDADE_NAO_VERIFICADA nem esconder o número e a data, que já não
        são segredo (ver Correspondencia.opcoes)."""
        m = msg(de="cliente@gmail.com", corpo="Encomenda 21910, qual o estado?")
        shopify = ShopifyFalsa(por_numero=[
            encomenda_falsa(name="#21910", email="cliente@gmail.com"),
            encomenda_falsa(name="#21910", email="cliente@gmail.com",
                             created_at="2026-08-20T10:00:00Z"),
        ])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, shopify=shopify)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertNotIn("IDENTIDADE_NAO_VERIFICADA", conteudo)
        self.assertIn("#21910 (2026-08-10)", conteudo)
        self.assertIn("#21910 (2026-08-20)", conteudo)
        self.assertIn("perguntando a qual das encomendas", conteudo)
        # A garantia de segurança que continua a valer: nenhum outro dado
        # da encomenda (o nome da cliente é "Marta Pinho" na fixture).
        self.assertNotIn("Marta", conteudo)
        self.assertNotIn("2620-537", conteudo)

    def test_numero_com_varios_candidatos_sem_email_a_bater_continua_a_escalar(self) -> None:
        """O caso genuinamente inseguro: o número existe em mais do que uma
        encomenda e nenhuma tem o email de quem escreveu -- aqui não há
        titularidade provada nenhuma, e o tratamento continua a ser o
        silêncio total, sem opções."""
        m = msg(de="curioso@gmail.com", corpo="Encomenda 21910, qual o estado?")
        shopify = ShopifyFalsa(por_numero=[
            encomenda_falsa(name="#21910", email="outra-pessoa@gmail.com"),
            encomenda_falsa(name="#21910", email="mais-outra@gmail.com"),
        ])
        cliente = ClienteFalso(self._SALTAR)
        self._correr(m, cfg(), cliente, shopify=shopify)
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("IDENTIDADE_NAO_VERIFICADA", conteudo)
        self.assertIn("2 encomendas possíveis", conteudo)
        self.assertNotIn("2026-08-10", conteudo)

    # --- Aplicação da decisão: rascunhar ---------------------------------

    def test_rascunhar_em_dry_run_nao_chama_o_graph_para_escrever(self) -> None:
        m = msg()
        resultado, graph, _ = self._correr(m, cfg(dry_run=True), ClienteFalso(self._RASCUNHAR))
        self.assertEqual(resultado, "rascunhado")
        self.assertNotIn("criar_rascunho", [c[0] for c in graph.chamadas])
        self.assertNotIn("marcar", [c[0] for c in graph.chamadas])
        self.assertEqual(self._linha(m["message_id"])["acao"], "rascunhar")

    def test_rascunhar_fora_de_dry_run_cria_rascunho_e_marca(self) -> None:
        m = msg()
        resultado, graph, _ = self._correr(m, cfg(dry_run=False), ClienteFalso(self._RASCUNHAR))
        self.assertEqual(resultado, "rascunhado")
        nomes = [c[0] for c in graph.chamadas]
        self.assertEqual(nomes.count("criar_rascunho"), 1)
        self.assertEqual([c for c in graph.chamadas if c[0] == "marcar"],
                          [("marcar", m["message_id"], "IA-Rascunhado")])
        self.assertEqual(self._linha(m["message_id"])["rascunho_id"], "AAMk-fake")

    def test_erro_a_criar_rascunho_nao_derruba_a_passagem(self) -> None:
        """Bug encontrado ao ligar o rascunho_id ao registo: sem try/except
        aqui, uma falha do Graph nesta chamada propagava por processar() e
        main() acima, derrubando a passagem inteira -- não só este email, mas
        todos os que viriam a seguir no mesmo lote."""
        m = msg()
        graph = GraphFalso(criar_rascunho=RuntimeError("Graph 500: instável"))
        resultado, graph, _ = self._correr(
            m, cfg(dry_run=False), ClienteFalso(self._RASCUNHAR), graph=graph
        )
        self.assertEqual(resultado, "rascunhado")
        linha = self._linha(m["message_id"])
        self.assertEqual(linha["acao"], "rascunhar")
        self.assertEqual(linha["rascunho_id"], "")
        # Sem rascunho criado, marcar "IA-Rascunhado" seria enganador -- por
        # isso não se tenta.
        self.assertNotIn("marcar", [c[0] for c in graph.chamadas])

    def test_erro_a_marcar_rascunho_nao_derruba_a_passagem(self) -> None:
        m = msg()
        graph = GraphFalso(marcar=RuntimeError("Graph 500: instável"))
        resultado, _, _ = self._correr(
            m, cfg(dry_run=False), ClienteFalso(self._RASCUNHAR), graph=graph
        )
        self.assertEqual(resultado, "rascunhado")
        self.assertEqual(self._linha(m["message_id"])["rascunho_id"], "AAMk-fake")

    def test_rascunho_parcial_marca_as_duas_categorias(self) -> None:
        m = msg()
        resultado, graph, _ = self._correr(
            m, cfg(dry_run=False, respostas_parciais=True), ClienteFalso(self._RASCUNHAR_PARCIAL)
        )
        self.assertEqual(resultado, "rascunhado-parcial")
        marcas = [c[2] for c in graph.chamadas if c[0] == "marcar"]
        self.assertEqual(marcas, ["IA-Rascunhado", "Precisa de humano"])
        self.assertTrue(self._linha(m["message_id"])["por_responder"])

    def test_rascunhar_com_corpo_vazio_e_rebaixado_a_escalar(self) -> None:
        """O modelo escolheu "rascunhar" mas não escreveu nada -- não pode
        sair como se fosse uma resposta. Vira escalação, categoria OUTRO."""
        m = msg()
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._RASCUNHAR_CORPO_VAZIO))
        self.assertEqual(resultado, "escalado")
        linha = self._linha(m["message_id"])
        self.assertEqual(linha["acao"], "escalar")
        self.assertEqual(linha["categoria"], "OUTRO")
        self.assertEqual(linha["motivo"], "modelo escolheu rascunhar mas devolveu corpo vazio")

    # --- Aplicação da decisão: escalar -----------------------------------

    def test_escalar_sem_resposta_nao_cria_rascunho(self) -> None:
        """Sem nada seguro a dizer, o corpo vem vazio: fica só a marca."""
        cliente = ClienteFalso(self._ESCALAR_SEM_RESPOSTA)
        m = msg()
        resultado, graph, _ = self._correr(m, cfg(dry_run=False), cliente)
        self.assertEqual(resultado, "escalado")
        self.assertNotIn("criar_rascunho", [c[0] for c in graph.chamadas])
        self.assertEqual([c[2] for c in graph.chamadas if c[0] == "marcar"],
                          ["Precisa de humano", "Falta regra"])
        self.assertEqual(self._linha(m["message_id"])["lacuna_tema"], "prazo Madeira")

    def test_escalar_com_resposta_cria_rascunho(self) -> None:
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        resultado, graph, _ = self._correr(m, cfg(dry_run=False), cliente)
        self.assertEqual(resultado, "escalado")
        criados = [c for c in graph.chamadas if c[0] == "criar_rascunho"]
        self.assertEqual(len(criados), 1)
        self.assertIn("Vamos verificar se conseguimos", criados[0][2])
        self.assertEqual(self._linha(m["message_id"])["rascunho_id"], "AAMk-fake")

    def test_escalar_faz_uma_so_chamada_ao_modelo(self) -> None:
        """O dossiê era uma segunda chamada por cada caso escalado. Removido a
        01/09/2026: a resposta de retenção passou a sair na primeira."""
        cliente = ClienteFalso(self._ESCALAR)
        self._correr(msg(), cfg(dry_run=False), cliente)
        self.assertEqual(len(cliente.pedidos), 1)

    def test_corpo_do_escalado_fica_gravado(self) -> None:
        """Antes ficava em dossie_resposta e a medição de deriva, que compara
        pelo corpo, nunca chegava aos casos escalados."""
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        self._correr(m, cfg(dry_run=False), cliente)
        self.assertIn("Vamos verificar se conseguimos",
                      self._linha(m["message_id"])["corpo"])

    def test_urgente_acrescenta_a_etiqueta(self) -> None:
        cliente = ClienteFalso(self._ESCALAR_URGENTE)
        m = msg()
        _, graph, _ = self._correr(m, cfg(dry_run=False), cliente)
        self.assertEqual([c[2] for c in graph.chamadas if c[0] == "marcar"],
                          ["Precisa de humano", "Decisão", "Urgente"])
        self.assertEqual(self._linha(m["message_id"])["urgencia"], "sim")

    def test_erro_a_criar_rascunho_sugerido_nao_derruba_a_passagem(self) -> None:
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        graph = GraphFalso(criar_rascunho=RuntimeError("Graph 500: instável"))
        resultado, _, _ = self._correr(m, cfg(dry_run=False), cliente, graph=graph)
        self.assertEqual(resultado, "escalado")
        # O registo já tinha corrido antes da tentativa de rascunho -- fica
        # sem rascunho_id, mas não se perde a escalação em si.
        self.assertEqual(self._linha(m["message_id"])["rascunho_id"], "")

    def test_erro_a_marcar_precisa_de_humano_nao_derruba_a_passagem(self) -> None:
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        graph = GraphFalso(marcar=RuntimeError("Graph 500: instável"))
        resultado, _, _ = self._correr(m, cfg(dry_run=False), cliente, graph=graph)
        self.assertEqual(resultado, "escalado")
        self.assertEqual(self._linha(m["message_id"])["rascunho_id"], "AAMk-fake")

    def test_escalar_em_dry_run_nao_escreve_na_caixa(self) -> None:
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        resultado, graph, _ = self._correr(m, cfg(dry_run=True), cliente)
        self.assertEqual(resultado, "escalado")
        nomes = [c[0] for c in graph.chamadas]
        self.assertNotIn("criar_rascunho", nomes)
        self.assertNotIn("marcar", nomes)
        # Mas fica gravado -- dry_run só afeta a caixa, não o registo.
        self.assertEqual(self._linha(m["message_id"])["acao"], "escalar")

    def test_pre_rascunhos_desligado_nao_prepara_texto(self) -> None:
        cliente = ClienteFalso(self._ESCALAR)
        m = msg()
        _, graph, _ = self._correr(m, cfg(dry_run=False, pre_rascunhos=False), cliente)
        self.assertNotIn("criar_rascunho", [c[0] for c in graph.chamadas])
        self.assertEqual(self._linha(m["message_id"])["corpo"], "")

    def test_saltar_do_modelo_e_registado(self) -> None:
        m = msg(corpo="Reserve já o seu stand na feira 2027")
        resultado, _, _ = self._correr(m, cfg(), ClienteFalso(self._SALTAR))
        self.assertEqual(resultado, "saltado")
        self.assertEqual(self._linha(m["message_id"])["acao"], "saltar")

    def test_erro_no_modelo_devolve_falhado_sem_registar(self) -> None:
        m = msg()
        cliente = ClienteFalso(self._SALTAR, rebenta=True)
        resultado, _, _ = self._correr(m, cfg(), cliente)
        self.assertEqual(resultado, "falhado")
        self.assertFalse(ja_processado(self.con, m["message_id"]))


class FecharCiclo(unittest.TestCase):
    """medir_deriva.py --fechar-ciclo -- fecho de ciclo do draft pelo id,
    sem chamar o Claude. A parte que decide se um rascunho foi aceite tal e
    qual, editado, ou apagado, é a única com lógica a proteger; o resto do
    ficheiro só imprime."""

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def _semear(self, message_id: str, corpo: str, rascunho_id: str, **over) -> None:
        m = msg(message_id=message_id)
        registar(self.con, m, "rascunhar", "sabia responder", corpo,
                 rascunho_id=rascunho_id, **over)

    def _resultado(self, message_id: str) -> tuple[str, object]:
        linha = self.con.execute(
            "SELECT resultado_estado, resultado_semelhanca FROM processados "
            "WHERE message_id = ?", (message_id,)
        ).fetchone()
        return linha

    def test_rascunho_apagado(self) -> None:
        self._semear("<a@x>", "Boa tarde,\n\nO seu artigo já foi enviado.", "D1")
        graph = GraphFalso(detalhe_rascunho=None)
        fechar_ciclo(graph, self.con, 0)
        estado, sem = self._resultado("<a@x>")
        self.assertEqual(estado, "apagado")
        self.assertIsNone(sem)

    def test_rascunho_ainda_pendente(self) -> None:
        self._semear("<a@x>", "Boa tarde,\n\nO seu artigo já foi enviado.", "D1")
        graph = GraphFalso(detalhe_rascunho={"sentDateTime": None, "body": {"content": ""}})
        fechar_ciclo(graph, self.con, 0)
        estado, sem = self._resultado("<a@x>")
        self.assertEqual(estado, "pendente")
        self.assertIsNone(sem)

    def test_rascunho_enviado_tal_e_qual(self) -> None:
        corpo = "Boa tarde,\n\nO seu artigo já foi enviado.\n\nCumprimentos,\nA Loja"
        self._semear("<a@x>", corpo, "D1")
        graph = GraphFalso(detalhe_rascunho={
            "sentDateTime": "2026-08-27T10:00:00Z",
            "body": {"content": f"<html><body><p>{corpo}</p></body></html>"},
        })
        fechar_ciclo(graph, self.con, 0)
        estado, sem = self._resultado("<a@x>")
        self.assertEqual(estado, "enviado-tal-e-qual")
        self.assertGreaterEqual(sem, 90.0)

    def test_rascunho_enviado_editado(self) -> None:
        self._semear("<a@x>", "Boa tarde,\n\nO seu artigo já foi enviado.", "D1")
        graph = GraphFalso(detalhe_rascunho={
            "sentDateTime": "2026-08-27T10:00:00Z",
            "body": {"content": "<p>Boa tarde. Afinal ainda não foi enviado, "
                                 "peço desculpa pelo erro, vamos verificar já.</p>"},
        })
        fechar_ciclo(graph, self.con, 0)
        estado, sem = self._resultado("<a@x>")
        self.assertEqual(estado, "enviado-editado")
        self.assertLess(sem, 90.0)

    def test_sem_rascunho_id_nunca_e_verificado(self) -> None:
        self._semear("<a@x>", "texto", "")  # rascunho_id vazio: nunca criado
        graph = GraphFalso(detalhe_rascunho=None)
        fechar_ciclo(graph, self.con, 0)
        self.assertEqual(graph.chamadas, [])
        self.assertEqual(self._resultado("<a@x>"), (None, None))

    def test_ja_resolvido_nao_e_reverificado(self) -> None:
        self._semear("<a@x>", "texto", "D1")
        self.con.execute(
            "UPDATE processados SET resultado_estado = 'apagado' WHERE message_id = '<a@x>'"
        )
        self.con.commit()
        graph = GraphFalso(detalhe_rascunho={"sentDateTime": "2026-08-27T10:00:00Z", "body": {}})
        fechar_ciclo(graph, self.con, 0)
        self.assertEqual(graph.chamadas, [])  # não voltou a perguntar ao Graph

    def test_pendente_e_reverificado_na_proxima_corrida(self) -> None:
        """Diferente de 'apagado'/'enviado-*', que são estados finais --
        'pendente' significa que ainda pode mudar, por isso continua a ser
        candidato nas corridas seguintes."""
        self._semear("<a@x>", "texto", "D1")
        self.con.execute(
            "UPDATE processados SET resultado_estado = 'pendente' WHERE message_id = '<a@x>'"
        )
        self.con.commit()
        graph = GraphFalso(detalhe_rascunho=None)
        fechar_ciclo(graph, self.con, 0)
        self.assertEqual(len(graph.chamadas), 1)
        self.assertEqual(self._resultado("<a@x>")[0], "apagado")


class FalhasSeguidas(unittest.TestCase):
    """A deteção de paragem total. Sem isto, uma avaria que impede TODAS as
    respostas (saldo esgotado, chave inválida) saía com código 0 e o alerta
    do systemd nunca disparava -- visto em produção a 30/08/2026."""

    def test_passagem_que_produziu_algo_poe_a_zero(self) -> None:
        self.assertEqual(falhas_seguidas({"rascunhado": 1}, 5), 0)
        self.assertEqual(falhas_seguidas({"escalado": 1}, 5), 0)
        self.assertEqual(falhas_seguidas({"rascunhado-parcial": 1}, 5), 0)

    def test_produzir_algo_conta_mesmo_havendo_falhas(self) -> None:
        """Uma falha isolada no meio de um lote que correu bem não é avaria."""
        self.assertEqual(falhas_seguidas({"escalado": 2, "falhado": 1}, 2), 0)

    def test_so_falhas_incrementa(self) -> None:
        self.assertEqual(falhas_seguidas({"falhado": 1}, 0), 1)
        self.assertEqual(falhas_seguidas({"falhado": 1, "repetido": 1}, 1), 2)

    def test_lote_que_nem_chega_ao_modelo_nao_mexe_no_contador(self) -> None:
        """Só newsletters: a triagem descarta-as antes do modelo, por isso a
        passagem não diz nada sobre a saúde da API. Zerar aqui esconderia uma
        avaria a decorrer; incrementar inventaria uma que não se viu."""
        self.assertEqual(falhas_seguidas({"saltado": 3}, 2), 2)
        self.assertEqual(falhas_seguidas({"repetido": 1}, 2), 2)
        self.assertEqual(falhas_seguidas({}, 0), 0)

    def test_alerta_so_ao_cruzar_o_limite(self) -> None:
        self.assertFalse(deve_alertar(0))
        self.assertFalse(deve_alertar(FALHAS_SEGUIDAS_PARA_ALERTA - 1))
        self.assertTrue(deve_alertar(FALHAS_SEGUIDAS_PARA_ALERTA))

    def test_nao_repete_o_alerta_a_cada_passagem(self) -> None:
        """Uma avaria de horas não deve encher o telemóvel de quem a recebe."""
        seguintes = range(FALHAS_SEGUIDAS_PARA_ALERTA + 1, PASSAGENS_ENTRE_AVISOS)
        self.assertFalse(any(deve_alertar(n) for n in seguintes))

    def test_repete_de_hora_a_hora(self) -> None:
        self.assertTrue(deve_alertar(PASSAGENS_ENTRE_AVISOS))
        self.assertTrue(deve_alertar(PASSAGENS_ENTRE_AVISOS * 2))

    def test_o_caso_real_de_30_08(self) -> None:
        """A forma exata das passagens do dia em que o saldo se esgotou:
        'vistos=2 repetido=1 falhado=1', repetida passagem após passagem."""
        contagem = {"repetido": 1, "falhado": 1}
        n = 0
        alertas = []
        for _ in range(5):
            n = falhas_seguidas(contagem, n)
            alertas.append(deve_alertar(n))
        self.assertEqual(alertas, [False, False, True, False, False])


class CustoEstimado(unittest.TestCase):
    """A aritmética de custo. Não é faturação -- serve para comparar antes e
    depois de uma alteração sem esperar pela fatura mensal."""

    def test_leitura_de_cache_custa_um_decimo_da_entrada(self) -> None:
        """É a razão de ser do TTL de 1 hora: ler o prefixo em vez de o
        reescrever."""
        so_leitura = custo_estimado("claude-sonnet-5", {"cache_leitura": 1_000_000})
        so_entrada = custo_estimado("claude-sonnet-5", {"entrada": 1_000_000})
        self.assertAlmostEqual(so_leitura, so_entrada * 0.1, places=6)

    def test_escrita_de_cache_custa_o_dobro_da_entrada(self) -> None:
        so_escrita = custo_estimado("claude-sonnet-5", {"cache_escrita": 1_000_000})
        so_entrada = custo_estimado("claude-sonnet-5", {"entrada": 1_000_000})
        self.assertAlmostEqual(so_escrita, so_entrada * 2.0, places=6)

    def test_soma_todas_as_componentes(self) -> None:
        c = custo_estimado("claude-sonnet-5", {
            "entrada": 1_000_000, "saida": 1_000_000,
            "cache_escrita": 1_000_000, "cache_leitura": 1_000_000,
        })
        # 3 (entrada) + 15 (saída) + 6 (escrita 2x) + 0,30 (leitura 0,1x)
        self.assertAlmostEqual(c, 24.30, places=4)

    def test_modelo_desconhecido_devolve_zero_em_vez_de_inventar(self) -> None:
        self.assertEqual(custo_estimado("modelo-que-nao-existe", {"entrada": 999}), 0.0)

    def test_uso_vazio_nao_rebenta(self) -> None:
        self.assertEqual(custo_estimado("claude-sonnet-5", {}), 0.0)

    def test_haiku_e_mais_barato_que_sonnet_no_mesmo_uso(self) -> None:
        uso = {"entrada": 100_000, "saida": 10_000}
        self.assertLess(
            custo_estimado("claude-haiku-4-5", uso),
            custo_estimado("claude-sonnet-5", uso),
        )


class RegistoDeCusto(unittest.TestCase):
    """O custo tem de chegar ao registo, senão não há como medir nada."""

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def _custo(self, message_id: str) -> tuple:
        return self.con.execute(
            "SELECT modelo, tokens_entrada, tokens_saida, tokens_cache_escrita, "
            "tokens_cache_leitura, chamadas_modelo, custo_estimado "
            "FROM processados WHERE message_id = ?", (message_id,)
        ).fetchone()

    def test_saltado_pela_triagem_fica_a_zero(self) -> None:
        """Um email descartado pela triagem não chega ao modelo -- zero é o
        valor certo, não um valor em falta."""
        m = msg(de="newsletter@x.com")
        registar(self.con, m, "saltar", "remetente-automatico:newsletter", "")
        modelo, entrada, saida, esc, leit, chamadas, custo = self._custo(m["message_id"])
        self.assertEqual((entrada, saida, esc, leit, chamadas), (0, 0, 0, 0, 0))
        self.assertEqual(custo, 0.0)

    def _latencia(self, message_id: str) -> int | None:
        return self.con.execute(
            "SELECT latencia_ms FROM processados WHERE message_id = ?", (message_id,)
        ).fetchone()[0]

    def test_latencia_da_chamada_fica_gravada(self) -> None:
        """Era a lacuna de observabilidade mais óbvia: sabia-se o que cada
        email custava em dinheiro, não em tempo."""
        m = msg()
        registar(self.con, m, "rascunhar", "sabia responder", "Olá,",
                 modelo="claude-sonnet-5",
                 uso={"entrada": 500, "saida": 300, "cache_escrita": 0,
                      "cache_leitura": 29_000, "chamadas": 1, "latencia_ms": 5340})
        self.assertEqual(self._latencia(m["message_id"]), 5340)

    def test_uso_sem_latencia_grava_zero_e_nao_rebenta(self) -> None:
        """Os registos anteriores a 03/09/2026 não têm o campo, e há
        chamadores (medir_deriva, reprocessar) que constroem o `uso` à mão."""
        m = msg()
        registar(self.con, m, "rascunhar", "x", "Olá,", modelo="claude-sonnet-5",
                 uso={"entrada": 1, "saida": 1, "cache_escrita": 0,
                      "cache_leitura": 0, "chamadas": 1})
        self.assertEqual(self._latencia(m["message_id"]), 0)

    def test_uso_reportado_pela_api_fica_gravado(self) -> None:
        m = msg()
        registar(self.con, m, "rascunhar", "sabia responder", "Olá,", modelo="claude-sonnet-5",
                 uso={"entrada": 500, "saida": 300, "cache_escrita": 0,
                      "cache_leitura": 29_000, "chamadas": 1})
        modelo, entrada, saida, esc, leit, chamadas, custo = self._custo(m["message_id"])
        self.assertEqual(modelo, "claude-sonnet-5")
        self.assertEqual((entrada, saida, esc, leit, chamadas), (500, 300, 0, 29_000, 1))
        self.assertGreater(custo, 0)

    def test_email_com_cache_quente_custa_menos_que_com_cache_fria(self) -> None:
        """A comparação que justifica o TTL de 1 hora, no próprio registo."""
        quente = msg(message_id="<quente@x>")
        registar(self.con, quente, "rascunhar", "x", "corpo", modelo="claude-sonnet-5",
                 uso={"entrada": 500, "saida": 300, "cache_escrita": 0,
                      "cache_leitura": 29_000, "chamadas": 1})
        fria = msg(message_id="<fria@x>")
        registar(self.con, fria, "rascunhar", "x", "corpo", modelo="claude-sonnet-5",
                 uso={"entrada": 500, "saida": 300, "cache_escrita": 29_000,
                      "cache_leitura": 0, "chamadas": 1})
        self.assertLess(self._custo("<quente@x>")[6], self._custo("<fria@x>")[6])

    def test_processar_grava_o_uso_que_o_cliente_reportou(self) -> None:
        """Ponta a ponta: o que a API reporta em usage tem de chegar ao
        registo por si só, sem ninguém o copiar à mão."""
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        cliente = ClienteFalso({"acao": "saltar", "motivo": "x", "corpo": "",
                                "categoria": "OUTRO"})
        m = msg()
        processar(m, cfg(), GraphFalso(), ShopifyFalsa(), self.con, cliente,
                  "prompt", BLOQUEADOS)
        modelo, _e, _s, _esc, _l, chamadas, _c = self._custo(m["message_id"])
        self.assertEqual(modelo, "claude-sonnet-5")
        self.assertEqual(chamadas, 1)  # o ClienteFalso não traz usage, mas contou a chamada


class CompararGravado(unittest.TestCase):
    """medir_deriva.py --comparar-gravado -- o corpo tal como foi escrito na
    altura, nunca regenerado, contra a resposta real. Não chama o Claude."""

    def setUp(self) -> None:
        pasta = TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.con = abrir_db(Path(pasta.name) / "t.db")
        self.addCleanup(self.con.close)

    def _semear(self, message_id: str, corpo: str, acao: str = "rascunhar") -> None:
        m = msg(message_id=message_id)
        registar(self.con, m, acao, "sabia responder", corpo)

    def _correr(self, graph, incluir_escalados=False, limite=0, so_numero=True) -> str:
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            comparar_gravado(graph, cfg(), self.con, incluir_escalados, limite, so_numero)
        return saida.getvalue()

    def _msg_falsa(self, message_id: str, conversation_id: str, recebido: str) -> dict:
        return {
            "id": "AAMk-1", "internetMessageId": message_id,
            "conversationId": conversation_id, "subject": "Duvida",
            "from": {"emailAddress": {"address": "cliente@gmail.com"}},
            "receivedDateTime": recebido,
        }

    def _resposta_loja(self, recebido: str, corpo_html: str) -> dict:
        return {"value": [{
            "from": {"emailAddress": {"address": CAIXA}},
            "receivedDateTime": recebido,
            "body": {"content": corpo_html},
        }]}

    def test_compara_corpo_gravado_com_resposta_real(self) -> None:
        self._semear("<a@x>", "Texto que a IA escreveu.")
        graph = GraphFalso(pedir_respostas={
            "<a@x>": {"value": [self._msg_falsa("<a@x>", "conv-1", "2026-08-20T10:00:00Z")]},
            "conv-1": self._resposta_loja(
                "2026-08-20T15:00:00Z", "<p>Texto que a IA escreveu.</p>"
            ),
        })
        saida = self._correr(graph)
        self.assertIn("1 email(is) com resposta real", saida)
        self.assertIn("100%", saida)

    def test_resposta_editada_da_semelhanca_baixa(self) -> None:
        self._semear("<a@x>", "Texto original da IA, bem diferente.")
        graph = GraphFalso(pedir_respostas={
            "<a@x>": {"value": [self._msg_falsa("<a@x>", "conv-1", "2026-08-20T10:00:00Z")]},
            "conv-1": self._resposta_loja(
                "2026-08-20T15:00:00Z", "<p>Resposta totalmente reescrita pelo lojista.</p>"
            ),
        })
        saida = self._correr(graph)
        self.assertNotIn("100%", saida)

    def test_mostra_o_texto_quando_nao_e_so_numero(self) -> None:
        self._semear("<a@x>", "Texto original.")
        graph = GraphFalso(pedir_respostas={
            "<a@x>": {"value": [self._msg_falsa("<a@x>", "conv-1", "2026-08-20T10:00:00Z")]},
            "conv-1": self._resposta_loja("2026-08-20T15:00:00Z", "<p>Texto original.</p>"),
        })
        saida = self._correr(graph, so_numero=False)
        self.assertIn("[O QUE A IA ESCREVEU NA ALTURA]", saida)
        self.assertIn("[O QUE FOI REALMENTE ENVIADO]", saida)

    def test_sem_resposta_ainda_visivel_nao_entra_na_comparacao(self) -> None:
        self._semear("<a@x>", "Texto.")
        graph = GraphFalso(pedir_respostas={
            "<a@x>": {"value": [self._msg_falsa("<a@x>", "conv-1", "2026-08-20T10:00:00Z")]},
            "conv-1": {"value": []},
        })
        saida = self._correr(graph)
        self.assertIn("0 email(is) com resposta real", saida)
        self.assertIn("1 sem resposta ainda visível", saida)

    def test_escalados_de_fora_por_omissao(self) -> None:
        self._semear("<a@x>", "Texto rascunhado.", acao="rascunhar")
        self._semear("<b@x>", "Resumo do dossiê.", acao="escalar")
        graph = GraphFalso(pedir_respostas={
            "<a@x>": {"value": [self._msg_falsa("<a@x>", "conv-1", "2026-08-20T10:00:00Z")]},
            "conv-1": self._resposta_loja("2026-08-20T15:00:00Z", "<p>Texto rascunhado.</p>"),
        })
        saida = self._correr(graph)
        self.assertIn("1 email(is) com resposta real", saida)

    def test_sem_nenhum_email_gravado_sai_com_erro(self) -> None:
        graph = GraphFalso()
        with self.assertRaises(SystemExit):
            comparar_gravado(graph, cfg(), self.con, False, 0, True)


class AnalisarBase(unittest.TestCase):
    """verificar_kb.py: só a parte que não precisa de rede -- montar o pedido
    e interpretar a resposta. Confirmar se o Claude acha mesmo boas
    contradições exige uma chamada real, fora do que testes automáticos
    cobrem."""

    def test_sem_contradicoes_devolve_lista_vazia(self) -> None:
        cliente = ClienteFalso({"contradicoes": []})
        self.assertEqual(analisar_base(cliente, cfg(), "# base\nregra unica"), [])

    def test_contradicoes_chegam_tal_e_qual(self) -> None:
        achado = {"contradicoes": [
            {"documentos": "a.md > X e b.md > Y", "descricao": "dizem coisas diferentes",
             "gravidade": "alta"},
        ]}
        cliente = ClienteFalso(achado)
        resultado = analisar_base(cliente, cfg(), "# base")
        self.assertEqual(resultado, achado["contradicoes"])

    def test_pedido_leva_a_base_inteira_no_conteudo(self) -> None:
        cliente = ClienteFalso({"contradicoes": []})
        analisar_base(cliente, cfg(), "REGRA-MUITO-ESPECIFICA-DE-TESTE")
        self.assertIn("REGRA-MUITO-ESPECIFICA-DE-TESTE",
                       cliente.pedidos[0]["messages"][0]["content"])


class Etiquetas(unittest.TestCase):
    """As etiquetas que o lojista vê na lista de mensagens do Outlook.

    Existem porque, sem elas, todos os casos escalados chegam com a mesma
    marca e não há por onde decidir qual abrir primeiro.
    """

    def test_traduz_a_categoria_para_o_que_ha_a_fazer(self) -> None:
        """"ACAO_SOBRE_ENCOMENDA" não diz nada a quem não escreveu o código."""
        self.assertEqual(etiquetas("ACAO_SOBRE_ENCOMENDA", "nao"),
                         ("Ação na encomenda",))
        self.assertEqual(etiquetas("COMPROMISSO_ANTERIOR", "nao"), ("Já prometido",))

    def test_urgente_acresce_a_do_tipo(self) -> None:
        self.assertEqual(etiquetas("JULGAMENTO_HUMANO", "sim"),
                         ("Decisão", "Urgente"))

    def test_urgente_aceita_o_que_o_modelo_costuma_devolver(self) -> None:
        """O campo é texto livre: o modelo pode responder "sim", "Alto" ou
        "ALTA". Só estes contam como urgente."""
        for valor in ("sim", "SIM", " Sim ", "alto", "alta"):
            self.assertIn(ETIQUETA_URGENTE, etiquetas("JULGAMENTO_HUMANO", valor),
                          f"{valor!r} devia contar como urgente")

    def test_qualquer_outra_coisa_nao_e_urgente(self) -> None:
        """Na dúvida, não urgente: uma etiqueta que aparece de mais deixa de
        querer dizer alguma coisa."""
        for valor in ("", "nao", "não", "medio", "baixo", "talvez", "true"):
            self.assertNotIn(ETIQUETA_URGENTE, etiquetas("JULGAMENTO_HUMANO", valor),
                             f"{valor!r} não devia contar como urgente")

    def test_outro_nao_gera_etiqueta(self) -> None:
        """Uma etiqueta a dizer "outro" não ajuda a decidir nada."""
        self.assertEqual(etiquetas("OUTRO", "nao"), ())

    def test_outro_urgente_leva_so_a_de_urgencia(self) -> None:
        self.assertEqual(etiquetas("OUTRO", "sim"), (ETIQUETA_URGENTE,))

    def test_categoria_desconhecida_nao_rebenta(self) -> None:
        self.assertEqual(etiquetas("CATEGORIA_QUE_NAO_EXISTE", "nao"), ())

    def test_todas_as_categorias_reais_tem_traducao(self) -> None:
        """Menos "OUTRO", que é deliberadamente omitida."""
        sem_traducao = [c for c in CATEGORIAS if c != "OUTRO" and c not in ETIQUETAS]
        self.assertEqual(sem_traducao, [])

    def test_nenhuma_etiqueta_e_longa_de_mais(self) -> None:
        """Numa linha do Outlook com três etiquetas, o que é comprido corta."""
        for nome in [*ETIQUETAS.values(), ETIQUETA_URGENTE]:
            self.assertLessEqual(len(nome), 20, nome)


class MarcarVarias(unittest.TestCase):
    """O PATCH das categorias manda a lista inteira, por isso marcar duas de
    cada vez a partir da lista original apagava a primeira."""

    class _Stub:
        base = "https://graph.example/v1.0/me"

        def __init__(self) -> None:
            self.pedidos: list[dict] = []

        def _pedir(self, metodo: str, url: str, **kw: object) -> dict:
            self.pedidos.append(dict(kw.get("json") or {}))  # type: ignore[arg-type]
            return {}

        marcar = Graph.marcar

    def test_varias_categorias_num_so_pedido(self) -> None:
        stub = self._Stub()
        m = {"id": "1", "categorias": []}
        stub.marcar(m, "Precisa de humano", "Decisão", "Urgente")
        self.assertEqual(len(stub.pedidos), 1)
        self.assertEqual(stub.pedidos[0]["categories"],
                         ["Precisa de humano", "Decisão", "Urgente"])

    def test_preserva_as_que_ja_la_estavam(self) -> None:
        stub = self._Stub()
        m = {"id": "1", "categorias": ["Etiqueta do lojista"]}
        stub.marcar(m, "Decisão")
        self.assertEqual(stub.pedidos[0]["categories"],
                         ["Etiqueta do lojista", "Decisão"])

    def test_chamadas_seguidas_nao_se_apagam(self) -> None:
        """O erro que a assinatura antiga tinha."""
        stub = self._Stub()
        m = {"id": "1", "categorias": []}
        stub.marcar(m, "Precisa de humano")
        stub.marcar(m, "Urgente")
        self.assertEqual(stub.pedidos[-1]["categories"],
                         ["Precisa de humano", "Urgente"])

    def test_nao_repete_o_que_ja_esta(self) -> None:
        stub = self._Stub()
        m = {"id": "1", "categorias": ["Decisão"]}
        stub.marcar(m, "Decisão")
        self.assertEqual(stub.pedidos, [])

    def test_ignora_vazias(self) -> None:
        stub = self._Stub()
        m = {"id": "1", "categorias": []}
        stub.marcar(m, "Decisão", "")
        self.assertEqual(stub.pedidos[0]["categories"], ["Decisão"])


class Webhook(unittest.TestCase):
    """O Discord recusa texto cru e corta a 2000 caracteres."""

    def test_texto_curto_fica_inteiro(self) -> None:
        self.assertEqual(partir_mensagem("Olá"), ["Olá"])

    def test_texto_vazio_nao_gera_pedacos(self) -> None:
        self.assertEqual(partir_mensagem(""), [])

    def test_corta_entre_paragrafos_e_nao_a_meio_de_um(self) -> None:
        """Um corte cego a 2000 parte uma frase e a mensagem seguinte começa
        em '…encomenda'."""
        blocos = ["A" * 900, "B" * 900, "C" * 900]
        pedacos = partir_mensagem("\n\n".join(blocos), limite=2000)
        self.assertEqual(len(pedacos), 2)
        for pedaco in pedacos:
            self.assertLessEqual(len(pedaco), 2000)
        # Nenhum bloco ficou partido ao meio.
        for bloco in blocos:
            self.assertTrue(any(bloco in p for p in pedacos))

    def test_linha_maior_que_o_limite_e_partida(self) -> None:
        """Sem isto, uma linha gigante ficava por enviar."""
        pedacos = partir_mensagem("X" * 5000, limite=2000)
        self.assertEqual([len(p) for p in pedacos], [2000, 2000, 1000])

    def test_nada_se_perde_no_corte(self) -> None:
        texto = "\n\n".join(f"parágrafo {i} " + "z" * 300 for i in range(20))
        self.assertEqual("".join(partir_mensagem(texto, limite=2000)).replace("\n", ""),
                         texto.replace("\n", ""))


class Aprender(unittest.TestCase):
    """As partes do aprender.py que decidem o que chega à lista do lojista.

    O valor da ferramenta está em não mostrar ruído: se agrupar mal, uma lista
    de quinze divergências não diz por onde começar, que é o problema que ela
    existe para resolver.
    """

    def test_extrai_so_o_que_foi_acrescentado(self) -> None:
        original = "Olá.\n\nVamos verificar internamente.\n\nCumprimentos"
        final = "Olá.\n\nVamos verificar internamente.\n\nLeva até 10 dias úteis.\n\nCumprimentos"
        self.assertIn("10 dias úteis", texto_acrescentado(original, final))

    def test_texto_igual_nao_acrescenta_nada(self) -> None:
        igual = "Olá.\n\nA sua encomenda foi expedida."
        self.assertEqual(texto_acrescentado(igual, igual).strip(), "")

    def test_reescritas_ficam_para_o_limiar(self) -> None:
        """O diff sozinho não distingue uma reescrita de um acrescento: o
        difflib pode devolver uma frase reescrita como inserção. Quem trata
        disso é o LIMIAR_REESCRITA, que abaixo de certa semelhança mostra o
        texto novo inteiro em vez do diff. Este teste trava a relação entre os
        dois limiares -- uma reescrita tem de ser sempre também uma
        divergência, senão nunca chega a ser olhada."""
        self.assertLess(LIMIAR_REESCRITA, LIMIAR_DIVERGENCIA)

    def test_texto_removido_nao_conta_como_acrescento(self) -> None:
        """Interessa o que a pessoa achou que faltava, não o que cortou."""
        original = "Olá.\n\nParágrafo a mais.\n\nCumprimentos"
        final = "Olá.\n\nCumprimentos"
        self.assertEqual(texto_acrescentado(original, final).strip(), "")

    def test_mesmo_padrao_apesar_de_nomes_diferentes(self) -> None:
        """Dois clientes, o mesmo acrescento: é um padrão, não dois casos."""
        self.assertTrue(mesmo_padrao(
            "Olá Maria, o reembolso leva até 10 dias úteis a chegar à conta.",
            "Olá João, o reembolso leva até 10 dias úteis a chegar à conta.",
        ))

    def test_assuntos_diferentes_nao_sao_o_mesmo_padrao(self) -> None:
        self.assertFalse(mesmo_padrao(
            "O reembolso leva até 10 dias úteis a chegar à conta.",
            "Pode enviar a capa para a morada do envelope da transportadora.",
        ))

    def test_agrupa_repeticoes_e_ordena_pelas_mais_vistas(self) -> None:
        casos = [
            {"acrescentado": "o reembolso leva até 10 dias úteis"},
            {"acrescentado": "pode enviar para a morada do envelope"},
            {"acrescentado": "o reembolso leva até 10 dias úteis a chegar"},
            {"acrescentado": "o reembolso leva até 10 dias uteis"},
        ]
        grupos = agrupar(casos)
        self.assertEqual([len(g) for g in grupos], [3, 1])

    def test_caso_unico_continua_a_aparecer(self) -> None:
        """Visto uma vez pode ser ruído, mas esconder não é a solução."""
        grupos = agrupar([{"acrescentado": "algo que só aconteceu uma vez"}])
        self.assertEqual(len(grupos), 1)

    def test_sem_casos_nao_ha_grupos(self) -> None:
        self.assertEqual(agrupar([]), [])

    def test_perguntar_sem_casos_nao_chama_o_modelo(self) -> None:
        cliente = ClienteFalso({"abertura": "", "casos": [], "fecho": ""})
        self.assertEqual(compor_pergunta(cliente, cfg(), []), "")
        self.assertEqual(cliente.pedidos, [])

    def test_pergunta_leva_o_par_completo(self) -> None:
        """Sem os três — o que o cliente perguntou, o que a IA escreveu, o que
        ele enviou — o lojista tem de abrir cada caso para perceber do que se
        trata, que é o trabalho que isto existe para lhe poupar."""
        cliente = ClienteFalso(RESPOSTA_PERGUNTA)
        compor_pergunta(cliente, cfg(), [{
            "assunto": "Re: Encomenda", "vezes": 2,
            "pergunta": "PERGUNTA-DO-CLIENTE",
            "escrito": "TEXTO-DA-IA",
            "enviado": "TEXTO-ENVIADO",
        }])
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        for esperado in ("PERGUNTA-DO-CLIENTE", "TEXTO-DA-IA", "TEXTO-ENVIADO"):
            self.assertIn(esperado, conteudo)

    def test_pergunta_diz_quantas_vezes_se_repetiu(self) -> None:
        cliente = ClienteFalso(RESPOSTA_PERGUNTA)
        compor_pergunta(cliente, cfg(), [{
            "assunto": "x", "vezes": 3, "pergunta": "p",
            "escrito": "e", "enviado": "n",
        }])
        self.assertIn("3x", cliente.pedidos[0]["messages"][0]["content"])

    def test_pergunta_aguenta_falta_do_email_original(self) -> None:
        """O Graph pode não devolver o email — a mensagem sai na mesma."""
        cliente = ClienteFalso(RESPOSTA_PERGUNTA)
        compor_pergunta(cliente, cfg(), [{
            "assunto": "x", "vezes": 1, "escrito": "e", "enviado": "n",
        }])
        self.assertIn("não disponível", cliente.pedidos[0]["messages"][0]["content"])

    def test_classificar_sem_grupos_nao_chama_o_modelo(self) -> None:
        cliente = ClienteFalso({"grupos": []})
        self.assertEqual(classificar(cliente, cfg(), "# base", []), [])
        self.assertEqual(cliente.pedidos, [])

    def test_classificar_devolve_o_veredito_por_indice(self) -> None:
        achado = {"grupos": [{"indice": 0, "veredito": "saliencia",
                              "onde": "Reembolso", "porque": "já lá está"}]}
        cliente = ClienteFalso(achado)
        saida = classificar(cliente, cfg(), "# base", [[{"acrescentado": "x"}]])
        self.assertEqual(saida, achado["grupos"])

    def test_classificar_leva_a_base_e_os_acrescentos(self) -> None:
        cliente = ClienteFalso({"grupos": []})
        classificar(cliente, cfg(), "REGRA-DE-TESTE",
                    [[{"acrescentado": "ACRESCENTO-DE-TESTE"}]])
        conteudo = cliente.pedidos[0]["messages"][0]["content"]
        self.assertIn("REGRA-DE-TESTE", conteudo)
        self.assertIn("ACRESCENTO-DE-TESTE", conteudo)


class FormatarMensagem(unittest.TestCase):
    """A formatação é código e não instrução ao modelo, para ser sempre a
    mesma. Um registo visual que oscila de dia para dia lê-se pior do que um
    registo feio mas constante."""

    @staticmethod
    def _dados(n: int = 1) -> dict:
        return {
            "abertura": "Três respostas que alteraste.",
            "casos": [{
                "titulo": f"Encomenda #{i}",
                "cliente": f"perguntou {i}",
                "assistente": f"escreveu {i}",
                "enviaste": f"enviaste {i}",
                "pergunta": f"porquê {i}?",
            } for i in range(1, n + 1)],
            "fecho": "Se foi pontual, basta dizeres.",
        }

    def test_cada_caso_e_um_paragrafo_fechado(self) -> None:
        """O partir_mensagem corta entre parágrafos. Com três casos a mensagem
        passa dos 2000 do Discord na mesma (só os links são ~960), mas assim
        parte-se entre casos e nunca a meio de um."""
        saida = formatar_mensagem(self._dados(3), [{"vezes": 1}] * 3)
        blocos = saida.split("\n\n")
        for i in (1, 2, 3):
            um_so = [b for b in blocos if b.startswith(f"{REGUA}\n**{i}** |")]
            self.assertEqual(len(um_so), 1, f"caso {i} não é um bloco fechado")

    def test_repeticao_so_aparece_quando_ha_repeticao(self) -> None:
        uma = formatar_mensagem(self._dados(1), [{"vezes": 1}])
        duas = formatar_mensagem(self._dados(1), [{"vezes": 2}])
        self.assertNotIn("visto", uma)
        self.assertIn("visto 2×", duas)

    def test_link_vai_pelo_codigo_e_so_quando_existe(self) -> None:
        com = formatar_mensagem(
            self._dados(1), [{"vezes": 1, "link": "https://outlook.example/a"}])
        self.assertIn("[abrir o email](https://outlook.example/a)", com)
        self.assertNotIn("abrir o email",
                         formatar_mensagem(self._dados(1), [{"vezes": 1}]))

    def test_quebra_de_linha_num_campo_nao_desalinha(self) -> None:
        dados = self._dados(1)
        dados["casos"][0]["cliente"] = "perguntou\nem duas linhas"
        saida = formatar_mensagem(dados, [{"vezes": 1}])
        self.assertIn("**O cliente** — perguntou em duas linhas", saida)

    def test_parentese_reto_num_campo_nao_parte_o_link(self) -> None:
        """"Re: [URGENTE] fone" fechava a sintaxe do link mascarado a meio."""
        dados = self._dados(1)
        dados["casos"][0]["titulo"] = "Re: [URGENTE] fone"
        saida = formatar_mensagem(
            dados, [{"vezes": 1, "link": "https://outlook.example/a"}])
        self.assertIn("**1** | Re: URGENTE fone", saida)
        self.assertEqual(saida.count("["), 1)

    def test_modelo_a_devolver_menos_casos_do_que_os_dados_nao_rebenta(self) -> None:
        """Se ele saltar um caso, sai a mensagem com os que devolveu -- e não
        um par trocado entre o texto de um caso e o link de outro."""
        saida = formatar_mensagem(self._dados(2), [
            {"vezes": 1, "link": "https://outlook.example/a"},
            {"vezes": 1, "link": "https://outlook.example/b"},
            {"vezes": 1, "link": "https://outlook.example/c"},
        ])
        self.assertIn("https://outlook.example/a", saida)
        self.assertIn("https://outlook.example/b", saida)
        self.assertNotIn("https://outlook.example/c", saida)

    def test_sem_casos_nao_ha_mensagem(self) -> None:
        self.assertEqual(formatar_mensagem({"abertura": "olá", "casos": [],
                                            "fecho": "adeus"}, []), "")
        self.assertEqual(formatar_mensagem({}, []), "")


class SuiteDeTestes(unittest.TestCase):
    """Guarda contra um modo de falha silencioso deste próprio ficheiro."""

    def test_nao_ha_classes_de_teste_com_nome_repetido(self) -> None:
        """Duas classes com o mesmo nome: a segunda apaga a primeira e os
        testes dela desaparecem sem erro nenhum. Aconteceu a 01/09/2026 --
        uma classe nova chamada HistoricoDoFio apagou 8 testes existentes, e
        só se notou porque o total desceu quando devia ter subido."""
        nomes = re.findall(r"^class (\w+)\(unittest\.TestCase\)",
                           Path(__file__).read_text(encoding="utf-8"), re.M)
        repetidos = {n for n in nomes if nomes.count(n) > 1}
        self.assertEqual(repetidos, set(), f"classes de teste repetidas: {repetidos}")


class GraphHistorico(unittest.TestCase):
    """O Graph.historico real, que o GraphFalso substitui por um dublê e por
    isso nunca era exercitado.

    Nome distinto de HistoricoDoFio de propósito: essa testa o
    resumir_historico(), e duas classes com o mesmo nome no mesmo módulo
    fazem a segunda apagar a primeira em silêncio.

    Existe porque o bodyPreview cortava tudo a 255 caracteres fixos, antes de
    o THREAD_CHARS se aplicar. Medido a 01/09/2026: 78 de 80 mensagens batiam
    no limite, e as 37 da própria loja batiam todas. Estes testes travam a
    volta atrás.
    """

    class _Stub:
        base = "https://graph.example/v1.0/me"

        def __init__(self, mensagens: list[dict]) -> None:
            self._mensagens = mensagens
            self.params: dict = {}

        def _pedir(self, metodo: str, url: str, **kw: object) -> dict:
            self.params = dict(kw.get("params") or {})  # type: ignore[arg-type]
            return {"value": self._mensagens}

        historico = Graph.historico

    @staticmethod
    def _msg(texto: str, mid: str = "<outra@x>", de: str = "cliente@example.com") -> dict:
        return {
            "internetMessageId": mid,
            "from": {"emailAddress": {"address": de}},
            "receivedDateTime": "2026-09-01T10:00:00Z",
            "body": {"content": texto},
        }

    def _correr(self, mensagens: list[dict], max_chars: int = 800) -> tuple:
        stub = self._Stub(mensagens)
        saida = stub.historico({"conversation_id": "c1", "message_id": "<eu@x>"},
                               8, max_chars)
        return stub, saida

    def test_pede_o_corpo_e_nao_o_bodypreview(self) -> None:
        """O bodyPreview vem cortado a 255 pelo Graph -- pedir isso era perder
        o fio a meio de uma frase, e nenhum THREAD_CHARS o recuperava."""
        stub, _ = self._correr([self._msg("olá")])
        selecionado = stub.params["$select"]
        self.assertIn("body", selecionado)
        self.assertNotIn("bodyPreview", selecionado)

    def test_le_o_texto_de_dentro_do_corpo(self) -> None:
        _, saida = self._correr([self._msg("<p>Temos duas opções para si</p>")])
        self.assertEqual(saida[0]["texto"], "Temos duas opções para si")

    def test_texto_longo_sobrevive_ao_limite_de_255(self) -> None:
        """O caso que motivou a mudança: uma mensagem da loja com opções."""
        longo = "A" * 600
        _, saida = self._correr([self._msg(longo)])
        self.assertEqual(len(saida[0]["texto"]), 600)

    def test_respeita_o_limite_pedido(self) -> None:
        _, saida = self._correr([self._msg("B" * 900)], max_chars=800)
        self.assertEqual(len(saida[0]["texto"]), 800)

    def test_corpo_em_falta_nao_rebenta(self) -> None:
        sem_corpo = self._msg("x")
        del sem_corpo["body"]
        _, saida = self._correr([sem_corpo])
        self.assertEqual(saida[0]["texto"], "")

    def test_exclui_a_propria_mensagem(self) -> None:
        _, saida = self._correr([
            self._msg("do fio", mid="<outra@x>"),
            self._msg("sou eu", mid="<eu@x>"),
        ])
        self.assertEqual([m["texto"] for m in saida], ["do fio"])


class Aquecer(unittest.TestCase):
    """A decisão de aquecer ou não é o que faz o aquecedor valer a pena: se
    aquecesse sempre, o custo dos ciclos comia a poupança das reescritas."""

    @staticmethod
    def _base(registos: tuple[tuple[str, int], ...],
              aquecido_em: str = "") -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE processados (em TEXT, chamadas_modelo INTEGER)")
        con.execute("CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT)")
        con.executemany("INSERT INTO processados VALUES (?, ?)", registos)
        if aquecido_em:
            con.execute("INSERT INTO meta VALUES (?, ?)",
                        (CHAVE_ULTIMO_AQUECIMENTO, aquecido_em))
        return con

    def test_silencio_longo_aquece(self) -> None:
        self.assertTrue(deve_aquecer(MINUTOS_ATE_AQUECER))
        self.assertTrue(deve_aquecer(MINUTOS_ATE_AQUECER + 30))

    def test_cache_ainda_quente_nao_aquece(self) -> None:
        self.assertFalse(deve_aquecer(0))
        self.assertFalse(deve_aquecer(MINUTOS_ATE_AQUECER - 1))

    def test_aquece_antes_de_expirar(self) -> None:
        """O TTL é de 60 minutos: aquecer só aos 60 já era tarde."""
        self.assertLess(MINUTOS_ATE_AQUECER, 60)

    def test_sem_registo_nenhum_aquece(self) -> None:
        """Primeiro arranque: não há por onde saber, e a cache está fria."""
        self.assertTrue(deve_aquecer(None))

    def test_conta_minutos_desde_a_ultima_chamada(self) -> None:
        con = self._base((("2026-08-31T10:00:00Z", 1),))
        agora = datetime(2026, 8, 31, 10, 45, 0)
        self.assertAlmostEqual(minutos_desde_ultima_chamada(con, agora), 45.0)

    def test_ignora_emails_que_nao_chegaram_ao_modelo(self) -> None:
        """Um email descartado pela triagem não lê a cache, logo não a renova."""
        con = self._base((
            ("2026-08-31T10:00:00Z", 1),
            ("2026-08-31T10:40:00Z", 0),
        ))
        agora = datetime(2026, 8, 31, 10, 45, 0)
        self.assertAlmostEqual(minutos_desde_ultima_chamada(con, agora), 45.0)

    def test_base_vazia_devolve_none(self) -> None:
        self.assertIsNone(
            minutos_desde_ultima_chamada(self._base(()), datetime(2026, 8, 31))
        )

    def test_aquecimento_anterior_conta_como_leitura(self) -> None:
        """Sem isto, um silêncio longo aquecia a cada passagem do temporizador
        em vez de a cada 40 minutos -- o custo dos ciclos comia a poupança."""
        con = self._base((("2026-08-31T10:00:00Z", 1),),
                         aquecido_em="2026-08-31T10:50:00Z")
        agora = datetime(2026, 8, 31, 11, 0, 0)
        self.assertAlmostEqual(minutos_desde_ultima_chamada(con, agora), 10.0)
        self.assertFalse(deve_aquecer(minutos_desde_ultima_chamada(con, agora)))

    def test_email_real_mais_recente_que_o_aquecimento_manda(self) -> None:
        con = self._base((("2026-08-31T10:50:00Z", 1),),
                         aquecido_em="2026-08-31T10:00:00Z")
        agora = datetime(2026, 8, 31, 11, 0, 0)
        self.assertAlmostEqual(minutos_desde_ultima_chamada(con, agora), 10.0)

    def test_so_aquecimento_sem_emails_conta(self) -> None:
        con = self._base((), aquecido_em="2026-08-31T10:30:00Z")
        agora = datetime(2026, 8, 31, 11, 0, 0)
        self.assertAlmostEqual(minutos_desde_ultima_chamada(con, agora), 30.0)

    def test_data_ilegivel_devolve_none(self) -> None:
        con = self._base((("nao-e-uma-data", 1),))
        self.assertIsNone(minutos_desde_ultima_chamada(con, datetime(2026, 8, 31)))


if __name__ == "__main__":
    unittest.main()
