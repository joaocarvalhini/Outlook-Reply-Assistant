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
    CATEGORIAS,
    DOMINIOS_BASE,
    Config,
    Correspondencia,
    compromissos_do_fio,
    emails_iguais,
    gravar_compromisso,
    resolver_encomenda,
    resumir_compromissos,
    abrir_db,
    carregar_blocklist,
    cortar_citacao,
    cursor_atual,
    extrair_numero_encomenda,
    ja_processado,
    para_html,
    para_texto,
    registar,
    resumir_encomenda,
    resumir_historico,
    saudacao,
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
        "pre_dossies": True, "registo_compromissos": True,
        "respostas_parciais": True,
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
    """Regra do cliente: 8h-13h bom dia, 13h-20h boa tarde, resto boa noite."""

    def test_manha(self) -> None:
        for h in (8, 10, 12):
            self.assertEqual(saudacao(h), "Bom dia", f"hora {h}")

    def test_tarde(self) -> None:
        for h in (13, 16, 19):
            self.assertEqual(saudacao(h), "Boa tarde", f"hora {h}")

    def test_noite(self) -> None:
        for h in (20, 23, 0, 5, 7):
            self.assertEqual(saudacao(h), "Boa noite", f"hora {h}")

    def test_fronteiras(self) -> None:
        # As fronteiras exatas são as que o cliente ditou: às 8 já é bom dia,
        # às 13 já é boa tarde, às 20 já é boa noite.
        self.assertEqual(saudacao(7), "Boa noite")
        self.assertEqual(saudacao(8), "Bom dia")
        self.assertEqual(saudacao(12), "Bom dia")
        self.assertEqual(saudacao(13), "Boa tarde")
        self.assertEqual(saudacao(19), "Boa tarde")
        self.assertEqual(saudacao(20), "Boa noite")

    def test_cobre_as_24_horas(self) -> None:
        for h in range(24):
            self.assertIn(saudacao(h), {"Bom dia", "Boa tarde", "Boa noite"})


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

    def __init__(self, por_numero=None, por_email=None, rebenta=False):
        self._numero = por_numero or []
        self._email = por_email or []
        self._rebenta = rebenta
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


if __name__ == "__main__":
    unittest.main()
