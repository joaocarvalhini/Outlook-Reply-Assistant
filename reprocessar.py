#!/usr/bin/env python3
"""Reprocessa decisões já tomadas, com o código de hoje. Não escreve nada.

    python reprocessar.py --acao escalar        os escalados, todos
    python reprocessar.py --acao escalar -n 20  só os 20 mais recentes
    python reprocessar.py --detalhe             mostra o corpo dos rascunhos novos

Serve para responder a uma pergunta concreta: uma alteração ao prompt, à base de
conhecimento ou uma integração nova mudou alguma coisa nos casos reais que já
passaram por aqui? Sem isto, a única forma de saber é esperar por emails novos.

Vai buscar o email original à caixa pelo internetMessageId — o registo local
guarda a decisão, não o corpo do email — e volta a correr a passagem inteira:
triagem, consulta à Shopify e modelo. Nunca cria rascunhos nem marca categorias.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter

import anthropic

import assistente as a


def buscar_email(graph: a.Graph, message_id: str) -> dict | None:
    """Vai buscar a mensagem à caixa pelo Message-ID, esteja em que pasta estiver."""
    dados = graph._pedir(
        "GET",
        f"{graph.base}/messages",
        params={
            "$filter": f"internetMessageId eq '{message_id}'",
            "$select": a.CAMPOS_LISTA,
        },
    )
    valores = dados.get("value", [])
    return graph._converter(valores[0]) if valores else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reprocessa decisões passadas")
    p.add_argument("--acao", default="escalar", choices=("escalar", "rascunhar", "saltar"))
    p.add_argument("-n", type=int, default=0, help="limita aos N mais recentes")
    p.add_argument("--detalhe", action="store_true", help="mostra o corpo dos rascunhos")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)
    linhas = con.execute(
        "SELECT message_id, assunto, motivo FROM processados WHERE acao = ? ORDER BY em DESC",
        (args.acao,),
    ).fetchall()
    if args.n:
        linhas = linhas[: args.n]
    if not linhas:
        sys.exit(f"Nenhuma decisão {args.acao!r} no registo.")

    graph = a.Graph(cfg)
    shopify = a.Shopify(cfg)
    cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
    prompt = a.construir_prompt(cfg)
    bloqueados = a.carregar_blocklist(cfg.blocklist)

    print(f"\n{len(linhas)} decisão(ões) {args.acao!r} · {cfg.modelo}\n")
    contagem: Counter[str] = Counter()
    mudaram = []

    for message_id, assunto, motivo_antigo in linhas:
        msg = buscar_email(graph, message_id)
        if msg is None:
            contagem["desaparecido"] += 1
            print(f"  --   {assunto[:44]:44}  já não está na caixa")
            continue

        if a.triar(msg, cfg, bloqueados):
            contagem["saltar"] += 1
            continue
        graph.detalhe(msg, cfg.max_body)
        # Sem isto, um email do formulário de devolução (noreply@formspree.io,
        # com list-unsubscribe carimbado por cima) era descartado aqui como se
        # fosse bulk mail -- exatamente o bug de produção de 22/08/2026 que a
        # exceção em triar_cabecalhos() existe para evitar.
        veio_contacto, veio_devolucao, motivo_formulario = a.desembrulhar_formularios(msg)
        if motivo_formulario or a.triar_cabecalhos(msg, veio_contacto, veio_devolucao):
            contagem["saltar"] += 1
            continue

        historico = ""
        if msg["conversation_id"]:
            try:
                historico = a.resumir_historico(
                    graph.historico(msg, cfg.fio_mensagens, cfg.fio_chars), cfg.mailbox
                )
            except Exception:
                historico = ""

        dados = ""
        numero = a.extrair_numero_encomenda(msg["assunto"], msg["corpo"]) or (
            a.extrair_numero_encomenda("", historico) if historico else None
        )
        if numero:
            try:
                encomenda = shopify.encomenda(numero, msg["de"])
            except Exception as exc:
                encomenda = None
                contagem["erro-shopify"] += 1
                print(f"  erro Shopify em {numero}: {type(exc).__name__}")
            if encomenda:
                dados = a.resumir_encomenda(encomenda)

        try:
            decisao = a.decidir(cliente, cfg, prompt, msg, dados, historico)
        except Exception as exc:
            contagem["erro-modelo"] += 1
            print(f"  ERRO  {assunto[:44]:44}  {type(exc).__name__}")
            continue

        acao, motivo, corpo = decisao["acao"], decisao["motivo"], decisao["corpo"]
        contagem[acao] += 1
        # marcadores: fio recuperado, nº encontrado, dados vieram da Shopify
        marca = (
            ("fio " if historico else "    ")
            + ("nº " if numero else "   ")
            + ("shopify " if dados else "        ")
        )
        mudou = acao != args.acao
        if mudou:
            mudaram.append((assunto, motivo_antigo, acao, motivo, corpo))
        print(f"  {'MUDOU' if mudou else '  =  '} {assunto[:44]:44} {marca} {acao}")

    print(f"\n{'─' * 70}")
    for k, v in contagem.most_common():
        print(f"{v:5}  {k}")
    processados = sum(v for k, v in contagem.items() if k in ("rascunhar", "escalar", "saltar"))
    if processados:
        print(f"\n{len(mudaram)}/{processados} mudaram de decisão")

    if args.detalhe and mudaram:
        for assunto, antigo, acao, motivo, corpo in mudaram:
            print(f"\n{'═' * 70}\n{assunto}\n{'═' * 70}")
            print(f"antes:  escalar — {antigo}")
            print(f"agora:  {acao} — {motivo}\n")
            if corpo:
                print(corpo)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
