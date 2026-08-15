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
import json
import os
import sys
from pathlib import Path

import assistente as a

ACOES = ("saltar", "escalar", "rascunhar")
CORRESPONDENCIA_DE_CLIENTE = ("escalar", "rascunhar")
ERRO = "erro"


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
            cliente: object | None, prompt: str) -> tuple[str, str, str]:
    """Devolve (obtido, etapa, detalhe)."""
    msg = construir_msg(caso, cfg.mailbox)

    motivo = a.triar(msg, cfg, bloqueados)
    if motivo:
        return "saltar", "triagem", motivo

    motivo = a.triar_cabecalhos(msg)
    if motivo:
        return "saltar", "cabeçalhos", motivo

    if cliente is None:
        return "passou", "triagem", "chegou ao modelo"

    try:
        d = a.decidir(
            cliente, cfg, prompt, msg,
            caso.get("dados_encomenda", ""), caso.get("historico", ""),
            caso.get("aviso_identidade", ""),
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
            marca = "PASS" if obtido == caso["expect"] else "FALHA"
            mostrado = obtido
        print(
            f"{marca}  {caso['id']:<32} esperado={caso['expect']:<10} "
            f"obtido={mostrado:<18} [{etapa}] {detalhe}"
        )
        if marca == "FALHA" and caso.get("note"):
            print(f"        nota: {caso['note']}")

    erros = [r for r in resultados if r[1][0] == ERRO]
    julgados = [
        r for r in resultados
        if r[1][0] != ERRO and not (so_triagem and r[1][0] == "passou")
    ]
    falhas = [r for r in julgados if r[1][0] != r[0]["expect"]]

    deviam_escalar = [r for r in julgados if r[0]["expect"] == "escalar"]
    escalaram = [r for r in julgados if r[1][0] == "escalar"]
    acertos = [r for r in deviam_escalar if r[1][0] == "escalar"]
    perdidos = [
        r for r in julgados
        if r[0]["expect"] in CORRESPONDENCIA_DE_CLIENTE and r[1][0] == "saltar"
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
    precisao = f"{len(acertos) / len(escalaram):.0%}" if escalaram else "n/a"
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
        if caso["expect"] not in ACOES:
            sys.exit(f"Caso {caso['id']!r}: expect inválido {caso['expect']!r}")
        if caso["id"] in vistos:
            sys.exit(f"Caso duplicado: {caso['id']!r}")
        vistos.add(caso["id"])

    bloqueados = a.carregar_blocklist(cfg.blocklist)
    cliente = prompt = None
    if not args.triagem:
        import anthropic

        cliente = anthropic.Anthropic(api_key=cfg.api_key)
        prompt = a.construir_prompt(cfg)

    etapa = "só triagem" if args.triagem else cfg.modelo
    print(f"{len(casos)} caso(s) · {etapa} · {args.casos} · caixa {cfg.mailbox}")

    resultados = [(c, avaliar(c, cfg, bloqueados, cliente, prompt or "")) for c in casos]
    return relatar(resultados, args.triagem)


if __name__ == "__main__":
    raise SystemExit(main())
