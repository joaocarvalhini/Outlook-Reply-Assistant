#!/usr/bin/env python3
"""Banco de ensaio: mede o que o assistente decide, contra casos conhecidos.

    python eval.py                  tudo, precisa de chave e base de conhecimento
    python eval.py --triagem        só as regras determinísticas: grátis, instantâneo

Corre a triagem e o modelo reais contra emails fixos com resultado esperado. Não
toca na caixa de correio — o Graph não entra aqui — por isso é seguro correr
contra uma configuração de produção.

Saem três números e não valem o mesmo:

  clientes perdidos  casos que deviam gerar rascunho ou escalação e foram
                     descartados. Em produção não deixam rasto que alguém veja.
                     Alvo: zero. Qualquer valor acima reprova a execução.
  recall             dos casos que deviam escalar, quantos escalaram. Baixo
                     significa que o assistente respondeu ao que não sabia.
  precisão           dos que escalaram, quantos deviam. Baixa dá trabalho a mais
                     à equipa. Chato, seguro.

Uma falha técnica não é uma decisão: fica marcada como ERRO, fora da aritmética,
e reprova a execução. Sem isso, uma chave expirada daria "recall 100%" — todos os
casos por responder escalam, e escalar parece correto.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import assistente as a

ACOES = ("saltar", "escalar", "rascunhar")
CORRESPONDENCIA_DE_CLIENTE = ("escalar", "rascunhar")
ERRO = "erro"


def acoes_esperadas(caso: dict) -> tuple[str, ...]:
    """Um caso pode aceitar várias ações sem abdicar do contrato do corpo."""
    esperado = caso["expect"]
    return (esperado,) if isinstance(esperado, str) else tuple(esperado)


def normalizar_texto(texto: str) -> str:
    """Só para os novos contratos regex; as substrings antigas não mudam."""
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).split())

# Negação local: "não/nunca/nem" até 4 tokens antes ou dentro do match.
# "sem" só nega quando precede imediatamente o início do conceito.
_SEPARADOR = r"[\s,;:-]"
_NEGACAO_CURTA = re.compile(rf"\b(?:nao|nunca|nem)\b(?:{_SEPARADOR}+\S+){{0,4}}{_SEPARADOR}*$")
_SEM_IMEDIATO = re.compile(r"\bsem\s+$")
_ALGUMA_NEGACAO = re.compile(r"\b(?:nao|nunca|nem)\b")

# A confirmação só afeta matches depois do "se" (ou que o atravessam).
# Pontuação forte e adversativas, com ou sem vírgula, terminam esse âmbito.
_PEDIDO_DE_CONFIRMACAO = re.compile(r"\b(?:confirmar|confirmamos|confirme|verificar|verificamos|apurar)\s+(?:internamente\s+)?se\b")
_CONECTOR_ADVERSATIVO = r"mas|porem|contudo|todavia|no entanto"
_FRONTEIRA_DE_CLAUSULA = re.compile(rf"(?<=[.!?;])\s+|\b(?:{_CONECTOR_ADVERSATIVO})\b")
# Fecha o âmbito de um "confirmar/verificar se" antes do fim da cláusula
# quando surge uma nova declaração explícita ("e informamos que...").
_INICIO_NOVA_DECLARACAO = re.compile(
    r"\b(?:e|mas)\s+(?:informamos|informo|dizemos|digo|comunicamos|comunico"
    r"|confirmamos|confirmo|esclarecemos|esclareco|acrescentamos|acrescento)\s+que\b"
)
_PEDIDO_DE_DADO = re.compile(
    r"\b(?:pode(?:ria)?\s+(?:indicar|enviar|partilhar|confirmar|facultar|informar|dizer)"
    r"|indiquem?|enviem?|partilhem?|confirmem?|facultem?|informem?|digam?"
    r"|qual|quais|precisamos|necessitamos)\b"
)
# ", por favor," é uma interjeição -- não separa um pedido do seu alvo.
_INCISO_TRANSPARENTE = re.compile(r",\s*por favor\s*,")
# Conjunção causal ou existencial que abre uma declaração/justificação nova
# dentro do troço de uma enumeração ("...por favor, pois já temos..."), e
# cópula seguida de valor ("o número é 123") -- não de artigo+substantivo
# ("e a morada completa"), que continua a ser enumeração.
_MARCADOR_DECLARACAO_LOCAL = re.compile(r"\b(?:pois|porque|temos)\b|\be\s+\d")


def _segmentos(clausula: str) -> list[str]:
    """Divide uma cláusula nas fronteiras reais de nova declaração.

    Ver _INICIO_NOVA_DECLARACAO: "e/mas informamos que..." fecha aqui, não só
    como cálculo posterior de âmbito. Um match nunca atravessa esta fronteira
    -- corta-se a cláusula antes dela, para procurar padrões em cada troço.
    """
    cortes = sorted({0, len(clausula), *(m.start() for m in _INICIO_NOVA_DECLARACAO.finditer(clausula))})
    return [clausula[i:j] for i, j in zip(cortes, cortes[1:])]


def _ocorrencias_nao_negadas(normalizado: str, padrao: str):
    """Partilha apenas a deteção de negação entre afirmações e pedidos.

    Padrões explicitamente negativos ("não foi expedida") mantêm o sentido
    literal; a sua própria negação não os elimina.
    """
    padrao_ja_contem_negacao = bool(_ALGUMA_NEGACAO.search(padrao))
    for clausula in _FRONTEIRA_DE_CLAUSULA.split(normalizado):
        for segmento in _segmentos(clausula):
            for m in re.finditer(padrao, segmento):
                prefixo = segmento[:m.start()]
                if _NEGACAO_CURTA.search(prefixo) or _SEM_IMEDIATO.search(prefixo):
                    continue
                if not padrao_ja_contem_negacao and _ALGUMA_NEGACAO.search(m.group(0)):
                    continue
                yield segmento, m


def afirmacoes_diretas(normalizado: str, padrao: str) -> list[str]:
    """Matches não negados, fora de perguntas diretas e fora do âmbito de um
    "confirmar/verificar/apurar se".

    Um segmento que termina em "?" é uma pergunta direta -- não afirma nada.
    Uma nova declaração explícita já teve o seu próprio segmento (ver
    _segmentos), por isso um "confirmar se" só neutraliza matches no mesmo
    segmento. Heurística, não parser.
    """
    resultado = []
    for segmento, m in _ocorrencias_nao_negadas(normalizado, padrao):
        if segmento.rstrip().endswith("?"):
            continue
        sob_confirmacao = any(
            p.end() <= m.end() for p in _PEDIDO_DE_CONFIRMACAO.finditer(segmento)
        )
        if not sob_confirmacao:
            resultado.append(m.group(0))
    return resultado


def _inicio_do_segmento(segmento: str, pos: int) -> int:
    """Início do troço do segmento, delimitado por vírgulas reais, que
    contém `pos`.

    Uma vírgula só separa pedidos distintos quando o troço que abre introduz
    uma declaração nova (_MARCADOR_DECLARACAO_LOCAL) antes da vírgula
    seguinte -- "indique X, o Y é 123" corta, "indique X, Y e Z" (enumeração
    nominal) não. Um marcador sem vírgula nenhuma antes (".., por favor, pois
    já temos...") corta na mesma, no seu próprio fim. Um inciso transparente
    (", por favor,") nunca conta como vírgula real.
    """
    incisos = [(t.start(), t.end()) for t in _INCISO_TRANSPARENTE.finditer(segmento)]
    virgulas = [v.start() for v in re.finditer(",", segmento)
                if not any(i <= v.start() < f for i, f in incisos)]
    inicio = 0
    for i, v in enumerate(virgulas):
        if v >= pos:
            break
        fim_do_troco = virgulas[i + 1] if i + 1 < len(virgulas) else len(segmento)
        if _MARCADOR_DECLARACAO_LOCAL.search(segmento, v + 1, fim_do_troco):
            inicio = v + 1
    for m in _MARCADOR_DECLARACAO_LOCAL.finditer(segmento, inicio, pos):
        inicio = m.end()
    return inicio


def pedidos_de_dados(normalizado: str, padrao: str) -> list[str]:
    """Matches com um pedido/pergunta não negado, ligado ao próprio match.

    O padrão descreve o dado; "indique", "confirme se", "pode indicar", etc.
    identificam o pedido, mas só quando estão no mesmo troço (delimitado por
    vírgulas reais, ver _inicio_do_segmento) do match -- um pedido sobre
    outra coisa não o valida. Mencionar o dado como facto, por si só, não
    basta. "qual/quais" só conta dentro de uma pergunta direta (o segmento
    termina em "?").
    """
    resultado = []
    for segmento, m in _ocorrencias_nao_negadas(normalizado, padrao):
        inicio = _inicio_do_segmento(segmento, m.start())
        interrogativa = segmento.rstrip().endswith("?")
        valido = False
        for p in _PEDIDO_DE_DADO.finditer(segmento, inicio, m.start()):
            if _NEGACAO_CURTA.search(segmento[:p.start()]):
                continue
            if p.group(0) in ("qual", "quais") and not interrogativa:
                continue
            valido = True
            break
        if valido:
            resultado.append(m.group(0))
    return resultado

_TIPO_POR_EXTENSAO = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def carregar_imagens(nomes: list[str], pasta: Path) -> tuple[dict, ...]:
    """Lê fixtures de eval/fixtures/ e devolve-as no formato que decidir()
    espera. Casos sem "imagens" continuam a testar exatamente como antes —
    isto só entra em jogo quando o caso pede uma fotografia."""
    imagens = []
    for nome in nomes:
        caminho = pasta / nome
        tipo = _TIPO_POR_EXTENSAO.get(caminho.suffix.lower())
        if tipo is None:
            sys.exit(f"Fixture de imagem com extensão não suportada: {nome}")
        dados = base64.standard_b64encode(caminho.read_bytes()).decode("ascii")
        imagens.append({"media_type": tipo, "data": dados})
    return tuple(imagens)


def construir_msg(caso: dict, caixa: str) -> dict:
    """Monta a mensagem tal como o Graph a teria entregue.

    Os endereços aceitam {mailbox} e {domain}. Sem isso, um caso que afirma "o
    remetente é um colega" só valeria para a loja contra a qual os casos foram
    escritos, e deixaria calado de testar seja o que for para qualquer outra.
    """
    dominio = caixa.partition("@")[2]
    email = caso["email"]

    def resolver(v: object) -> str:
        return str(v).format(mailbox=caixa, domain=dominio).lower()

    return {
        "id": f"eval-{caso['id']}",
        "message_id": f"<{caso['id']}@eval.local>",
        "conversation_id": f"conv-{caso['id']}",
        "assunto": email.get("subject", ""),
        "de": resolver(email.get("from", "")),
        "nome": email.get("from_name", ""),
        "para": [resolver(x) for x in email.get("to", [])],
        "cc": [resolver(x) for x in email.get("cc", [])],
        "recebido": "2026-08-06T10:00:00Z",
        "categorias": email.get("categories", []),
        "cabecalhos": [(str(k), str(v)) for k, v in email.get("headers", [])],
        "corpo": email.get("body", ""),
    }


def avaliar(caso: dict, cfg: a.Config, bloqueados: frozenset[str],
            cliente: object | None, prompt: str,
            pasta_fixtures: Path = Path("eval/fixtures")) -> tuple[str, str, str]:
    """Devolve (obtido, etapa, detalhe)."""
    msg = construir_msg(caso, cfg.mailbox)

    motivo = a.triar(msg, cfg, bloqueados)
    if motivo:
        return "saltar", "triagem", motivo

    # Sem isto, um caso que simule o formulário de devolução (Formspree, com
    # list-unsubscribe carimbado) era descartado aqui como bulk mail, e o
    # corpo que chegava ao modelo nos casos que passassem era o dump em bruto
    # do formulário, não o texto reformatado que a produção usa.
    veio_contacto, veio_devolucao, motivo = a.desembrulhar_formularios(msg)
    if motivo:
        return "saltar", "triagem", motivo

    motivo = a.triar_cabecalhos(msg, veio_contacto, veio_devolucao)
    if motivo:
        return "saltar", "cabeçalhos", motivo

    if cliente is None:
        return "passou", "triagem", "chegou ao modelo"

    imagens = carregar_imagens(caso.get("imagens", []), pasta_fixtures)
    try:
        d = a.decidir(
            cliente, cfg, prompt, msg,
            caso.get("dados_encomenda", ""), caso.get("historico", ""),
            caso.get("aviso_identidade", ""), caso.get("compromissos", ""),
            imagens, caso.get("nota_anexos", ""),
        )
    except Exception as exc:
        return ERRO, "modelo", f"{type(exc).__name__}: {exc}"[:110]

    if d["acao"] == "rascunhar" and not d["corpo"].strip():
        return "escalar", "modelo", "escolheu rascunhar sem corpo"

    # Um caso pode exigir uma categoria concreta: é assim que se testa a
    # taxonomia, e não só a ação.
    esperada = caso.get("expect_categoria")
    if esperada and d["categoria"] != esperada:
        # Devolve um valor que nunca casa com o esperado, para reprovar mesmo
        # quando a ação está certa: a categoria alimenta as métricas e uma
        # categoria errada estraga-as em silêncio.
        return "categoria-errada", "modelo", f"deu {d['categoria']}, esperava {esperada}"

    # Um caso escalado pode exigir que NÃO se escreva resposta nenhuma. É uma
    # fronteira de segurança: escrever a um cliente cuja identidade não está
    # confirmada, ou sobre um caso que não se percebe, é pior do que ficar
    # calado. Substituiu o antigo "expect_sem_dossie" quando o dossiê saiu.
    corpo = d.get("corpo", "").strip()
    if caso.get("expect_sem_corpo") and corpo:
        return "corpo-indevido", "modelo", "escreveu resposta quando não devia"

    # O inverso, e o mais comum: escalar não é ficar calado. Um pedido concreto
    # sobre uma encomenda leva sempre pelo menos a resposta de retenção, para
    # quem revê não ter de a escrever de raiz.
    if caso.get("expect_corpo") and not corpo:
        return "sem-resposta-de-retencao", "modelo", "escalou sem escrever nada ao cliente"

    # Resposta parcial: um caso pode exigir que o rascunho deixe registado o
    # que ficou por responder. Sem isto, um rascunho parcial passava por
    # completo e alguém enviava-o como se respondesse ao email todo.
    parcial = d.get("por_responder", "").strip()
    if caso.get("expect_parcial") and not parcial:
        return ("parcial-em-falta", "modelo",
                "respondeu sem assinalar o que ficou por responder")
    if caso.get("expect_sem_parcial") and parcial:
        return ("parcial-indevido", "modelo",
                f"assinalou '{parcial[:50]}' quando respondeu ao email todo")

    # Texto obrigatório ou proibido no corpo — ex.: um link que a base de
    # conhecimento manda incluir sempre (ou nunca, consoante o caso). A ação
    # e a categoria por si só não apanham uma resposta que "decide bem" mas
    # esquece um facto literal que tinha de vir junto.
    em_falta = [t for t in caso.get("expect_texto_contem", ()) if t not in corpo]
    if em_falta:
        return ("texto-em-falta", "modelo", f"corpo não contém {em_falta!r}")
    indevido = [t for t in caso.get("expect_texto_nao_contem", ()) if t in corpo]
    if indevido:
        return ("texto-indevido", "modelo", f"corpo contém {indevido!r} quando não devia")

    # Regex normalizadas: afirmações e pedidos são contratos distintos.
    # Só expect_texto_pedido_regex aceita pedir/confirmar dados em vez de
    # afirmar factos. Os campos literais antigos mantêm-se inalterados.
    normalizado = normalizar_texto(corpo)
    for padrao in caso.get("expect_texto_regex", ()):
        if not afirmacoes_diretas(normalizado, padrao):
            return "texto-em-falta", "modelo", f"corpo não afirma /{padrao}/"
    for padrao in caso.get("expect_texto_pedido_regex", ()):
        if not pedidos_de_dados(normalizado, padrao):
            return "texto-em-falta", "modelo", f"corpo não pede /{padrao}/"
    for padrao in caso.get("expect_texto_nao_regex", ()):
        achados = afirmacoes_diretas(normalizado, padrao)
        if achados:
            return "texto-indevido", "modelo", f"corpo afirma {achados!r} quando não devia"

    compromisso = d.get("compromisso_tipo", "")
    if "expect_compromisso" in caso and compromisso != caso["expect_compromisso"]:
        return ("compromisso-errado", "modelo",
                f"deu {compromisso or '(vazio)'}, esperava {caso['expect_compromisso']}")
    if caso.get("expect_sem_data_de_compromisso") and d.get("compromisso_data", "").strip():
        return ("data-inventada", "modelo",
                f"inventou data '{d['compromisso_data']}' sem ela estar confirmada")

    detalhe = f"[{d['categoria']}] {d['motivo']}"
    return d["acao"], "modelo", detalhe[:80]


def relatar(resultados: list[tuple[dict, tuple[str, str, str]]], so_triagem: bool) -> int:
    print()
    for caso, (obtido, etapa, detalhe) in resultados:
        if so_triagem and obtido == "passou":
            marca, mostrado = "----", "chegou ao modelo"
        elif obtido == ERRO:
            marca, mostrado = "ERRO", "sem veredito"
        else:
            marca = "PASS" if obtido in acoes_esperadas(caso) else "FALHA"
            mostrado = obtido
        esperado = "|".join(acoes_esperadas(caso))
        print(
            f"{marca}  {caso['id']:<32} esperado={esperado:<10} "
            f"obtido={mostrado:<18} [{etapa}] {detalhe}"
        )
        if marca == "FALHA" and caso.get("note"):
            print(f"        nota: {caso['note']}")

    erros = [r for r in resultados if r[1][0] == ERRO]
    julgados = [
        r for r in resultados
        if r[1][0] != ERRO and not (so_triagem and r[1][0] == "passou")
    ]
    falhas = [r for r in julgados if r[1][0] not in acoes_esperadas(r[0])]

    deviam_escalar = [r for r in julgados if r[0]["expect"] == "escalar"]
    escalaram = [r for r in julgados if r[1][0] == "escalar"]
    acertos = [r for r in deviam_escalar if r[1][0] == "escalar"]
    escalacoes_aceites = [r for r in escalaram if "escalar" in acoes_esperadas(r[0])]
    perdidos = [
        r for r in julgados
        if set(acoes_esperadas(r[0])) <= set(CORRESPONDENCIA_DE_CLIENTE)
        and r[1][0] == "saltar"
    ]

    largura = 24
    print()
    print(f"{len(julgados) - len(falhas)}/{len(julgados)} casos corretos")
    adiados = len(resultados) - len(julgados) - len(erros)
    if so_triagem and adiados:
        print(f"{adiados} casos passaram a triagem (não avaliados nesta etapa)")
    if erros:
        print(f"{'ERROS TÉCNICOS:':<{largura}}{len(erros)}  ->  resultados não são de confiança")
    if perdidos:
        nomes = ", ".join(r[0]["id"] for r in perdidos)
        print(f"{'CLIENTES PERDIDOS:':<{largura}}{len(perdidos)}  ->  {nomes}")
    else:
        print(f"{'clientes perdidos:':<{largura}}0")
    recall = f"{len(acertos) / len(deviam_escalar):.0%}" if deviam_escalar else "n/a"
    precisao = f"{len(escalacoes_aceites) / len(escalaram):.0%}" if escalaram else "n/a"
    print(f"{'recall de escalação:':<{largura}}{recall}")
    print(f"{'precisão de escalação:':<{largura}}{precisao}")
    print()

    return 1 if falhas or perdidos or erros else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Banco de ensaio do assistente")
    p.add_argument("--casos", type=Path, default=Path("eval/casos.json"))
    p.add_argument("--triagem", action="store_true", help="só as regras determinísticas")
    p.add_argument("--caixa", help="sobrepõe MAILBOX; os casos interpolam-na")
    args = p.parse_args(argv)
    a.saida_utf8()

    # Nenhuma etapa do ensaio toca no Graph nem na Shopify, por isso exigir
    # estas credenciais bloquearia uma execução que tem tudo o que precisa.
    for nome in (
        "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
        "SHOPIFY_STORE", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
    ):
        os.environ.setdefault(nome, "nao-usado-pelo-eval")
    os.environ.setdefault("MAILBOX", "apoio@loja.pt")
    if args.caixa:
        os.environ["MAILBOX"] = args.caixa
    if args.triagem:
        os.environ.setdefault("ANTHROPIC_API_KEY", "nao-usado-na-triagem")

    cfg = a.carregar_config(True)
    try:
        casos = json.loads(args.casos.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"Casos: {exc}")

    vistos: set[str] = set()
    for caso in casos:
        if (not isinstance(caso["expect"], (str, list))
                or not acoes_esperadas(caso)
                or any(acao not in ACOES for acao in acoes_esperadas(caso))):
            sys.exit(f"Caso {caso['id']!r}: expect inválido {caso['expect']!r}")
        for campo in ("expect_texto_regex", "expect_texto_pedido_regex", "expect_texto_nao_regex"):
            for padrao in caso.get(campo, ()):
                try:
                    re.compile(padrao)
                except re.error as exc:
                    sys.exit(f"Caso {caso['id']!r}: {campo} inválido: {exc}")
        if caso["id"] in vistos:
            sys.exit(f"Caso duplicado: {caso['id']!r}")
        vistos.add(caso["id"])

    bloqueados = a.carregar_blocklist(cfg.blocklist)
    cliente = prompt = None
    if not args.triagem:
        import anthropic

        cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
        prompt = a.construir_prompt(cfg)

    etapa = "só triagem" if args.triagem else cfg.modelo
    print(f"{len(casos)} caso(s) · {etapa} · {args.casos} · caixa {cfg.mailbox}")

    pasta_fixtures = args.casos.parent / "fixtures"
    resultados = [
        (c, avaliar(c, cfg, bloqueados, cliente, prompt or "", pasta_fixtures))
        for c in casos
    ]
    return relatar(resultados, args.triagem)


if __name__ == "__main__":
    raise SystemExit(main())
