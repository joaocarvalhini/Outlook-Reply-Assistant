#!/usr/bin/env python3
"""Compara o que o assistente escreveria hoje com o que o lojista respondeu.

    python medir_deriva.py                     repassa os "rascunhar" gravados
    python medir_deriva.py --incluir-escalados também tenta os que ficaram "escalar"
    python medir_deriva.py -n 15                só os 15 mais recentes
    python medir_deriva.py --so-numero          não mostra o texto, só a tabela

Não escreve nada. Para cada email, **gera o rascunho outra vez com o código de
hoje** (como o reprocessar.py), vai buscar à caixa a resposta que o lojista
realmente enviou nessa conversa depois do email do cliente, e põe as duas lado
a lado.

Com --incluir-escalados inclui também os emails que na altura escalaram: se o
código de hoje já os resolveria (contexto do fio, Shopify, correções ao
prompt), mostra o que o assistente escreveria a par do que o lojista respondeu
de verdade — mesmo esses nunca tendo passado por um rascunho real.

Regenerar em vez de usar o corpo gravado é deliberado: o registo local guarda o
texto de quando o email foi processado, que pode ser de antes da última
correção ao prompt ou à base de conhecimento. Comparar código antigo contra a
resposta real não diz nada sobre a qualidade do assistente agora.

Cautela adicional: a resposta "real" é a próxima mensagem da loja na mesma
conversa, mas num processo de devolução com várias trocas, essa mensagem pode
estar a responder a uma pergunta diferente da que gerou o rascunho, não a
esta. Uma semelhança baixa pode ser isso, não um rascunho mau — por isso é
preciso ler, o número é só para ordenar por onde começar.

O número de semelhança (SequenceMatcher, 0-100%) é só uma bússola — dá jeito
para ordenar e ver os piores primeiro, não é uma nota de qualidade. Um rascunho
pode ter 40% de semelhança de caracteres e estar certo (o lojista escreveu por
outras palavras a mesma coisa), ou ter 80% e estar errado (mudou só a parte que
importava). Ler é obrigatório; o número só ajuda a decidir por onde começar.

Referência do projeto (comentário em registar(), nunca antes medido): acima de
60% editado, o rascunho é ruído.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from difflib import SequenceMatcher

import anthropic

import assistente as a


def resposta_real(graph: a.Graph, msg: dict, aviso: str) -> str | None:
    """A primeira resposta da loja, na mesma conversa, depois deste email."""
    dados = graph._pedir(
        "GET",
        f"{graph.base}/messages",
        params={
            "$filter": f"conversationId eq '{msg['conversation_id']}'",
            "$select": "from,receivedDateTime,body",
            "$top": "25",
        },
    )
    candidatas = [
        m
        for m in dados.get("value", [])
        if m.get("receivedDateTime", "") > msg["recebido"]
        and a.e_da_loja(
            str((m.get("from") or {}).get("emailAddress", {}).get("address", "")),
            msg["_caixa"],
        )
    ]
    if not candidatas:
        return None
    candidatas.sort(key=lambda m: m.get("receivedDateTime", ""))
    corpo = (candidatas[0].get("body") or {}).get("content", "")
    texto = a.cortar_citacao(a.para_texto(corpo))
    # Um caso desta sessão teve um rascunho criado manualmente por mim, fora do
    # DRY_RUN, só para o cliente ver a qualidade — não é o lojista a responder,
    # é o próprio texto do assistente a aparecer como se fosse a resposta real.
    if aviso and aviso in texto:
        return None
    return texto


def buscar_email(graph: a.Graph, message_id: str) -> dict | None:
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


def semelhanca(a_: str, b_: str) -> float:
    return SequenceMatcher(None, a_.strip().lower(), b_.strip().lower()).ratio() * 100


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compara rascunhos com respostas reais")
    p.add_argument("-n", type=int, default=0, help="limita aos N mais recentes")
    p.add_argument("--so-numero", action="store_true", help="não mostra o texto")
    p.add_argument(
        "--incluir-escalados", action="store_true",
        help="tenta também gerar rascunho para emails que ficaram 'escalar', "
             "não só os que já foram 'rascunhar'",
    )
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)
    acoes = "('rascunhar','escalar')" if args.incluir_escalados else "('rascunhar')"
    linhas = con.execute(
        f"SELECT message_id, assunto FROM processados "
        f"WHERE acao IN {acoes} AND conversation_id != '' ORDER BY em DESC"
    ).fetchall()
    if args.n:
        linhas = linhas[: args.n]
    if not linhas:
        sys.exit("Nenhum rascunho no registo.")

    graph = a.Graph(cfg)
    shopify = a.Shopify(cfg)
    cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
    prompt = a.construir_prompt(cfg)
    bloqueados = a.carregar_blocklist(cfg.blocklist)

    resultados = []
    sem_resposta = 0
    ja_nao_rascunha = 0

    for message_id, assunto in linhas:
        msg = buscar_email(graph, message_id)
        if msg is None:
            continue
        msg["_caixa"] = cfg.mailbox

        # A resposta real é procurada antes de gastar uma chamada ao modelo:
        # sem ela não há com que comparar.
        try:
            real = resposta_real(graph, msg, cfg.aviso)
        except Exception:
            real = None
        if not real:
            sem_resposta += 1
            continue

        if a.triar(msg, cfg, bloqueados) or (
            graph.detalhe(msg, cfg.max_body) and a.triar_cabecalhos(msg)
        ):
            continue

        historico = ""
        try:
            historico = a.resumir_historico(
                graph.historico(msg, cfg.fio_mensagens, cfg.fio_chars), cfg.mailbox
            )
        except Exception:
            pass

        dados = ""
        numero = a.extrair_numero_encomenda(msg["assunto"], msg["corpo"]) or (
            a.extrair_numero_encomenda("", historico) if historico else None
        )
        if numero:
            try:
                encomenda = shopify.encomenda(numero, msg["de"])
                if encomenda:
                    dados = a.resumir_encomenda(encomenda)
            except Exception:
                pass

        try:
            decisao = a.decidir(cliente, cfg, prompt, msg, dados, historico)
        except Exception:
            continue
        acao, rascunho = decisao["acao"], decisao["corpo"]
        if acao != "rascunhar" or not rascunho.strip():
            ja_nao_rascunha += 1
            continue

        resultados.append((assunto, rascunho, real, semelhanca(rascunho, real)))

    resultados.sort(key=lambda r: r[3])

    print(f"\n{len(resultados)} rascunho(s) com resposta real para comparar "
          f"({sem_resposta} sem resposta ainda visível na caixa, "
          f"{ja_nao_rascunha} deixaram de rascunhar com o código de hoje)\n")
    print(f"{'semelhança':>10}  assunto")
    print("─" * 70)
    for assunto, _r, _real, sem in resultados:
        print(f"{sem:9.0f}%  {assunto[:60]}")

    if resultados:
        valores = sorted(r[3] for r in resultados)
        print(f"\nmediana: {valores[len(valores)//2]:.0f}%   "
              f"abaixo de 40%: {sum(1 for v in valores if v < 40)}/{len(valores)}")

    if not args.so_numero:
        for assunto, rascunho, real, sem in resultados:
            print(f"\n{'═' * 70}\n{assunto}  ({sem:.0f}% semelhante)\n{'═' * 70}")
            print(f"\n[RASCUNHO DO ASSISTENTE]\n{rascunho}")
            print(f"\n[RESPOSTA REAL DO LOJISTA]\n{real[:1200]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
