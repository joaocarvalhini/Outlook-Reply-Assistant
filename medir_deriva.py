#!/usr/bin/env python3
"""Compara o que o assistente escreveria hoje com o que o lojista respondeu.

    python medir_deriva.py                     repassa os "rascunhar" gravados
    python medir_deriva.py --incluir-escalados também tenta os que ficaram "escalar"
    python medir_deriva.py -n 15                só os 15 mais recentes
    python medir_deriva.py --so-numero          não mostra o texto, só a tabela
    python medir_deriva.py --pasta deleteditems -n 30
                                                 fonte alternativa: conversas
                                                 reais da pasta indicada, que
                                                 nunca passaram pelo assistente

Não escreve nada. Para cada email, **gera o rascunho outra vez com o código de
hoje** (como o reprocessar.py), vai buscar à caixa a resposta que o lojista
realmente enviou nessa conversa depois do email do cliente, e põe as duas lado
a lado.

Com --incluir-escalados inclui também os emails que na altura escalaram: se o
código de hoje já os resolveria (contexto do fio, Shopify, correções ao
prompt), mostra o que o assistente escreveria a par do que o lojista respondeu
de verdade — mesmo esses nunca tendo passado por um rascunho real.

Com --pasta, a fonte dos casos deixa de ser o registo local (que só tem os
emails que o assistente já viu, uma amostra pequena da fase de testes) e
passa a ser qualquer pasta do Graph, usando a mesma procura de pares
pergunta-resposta do casos_antigos.py — um universo muito maior de conversas
reais, incluindo as que nunca chegaram a passar pelo assistente. Ao contrário
do casos_antigos.py, isto chama o Claude para cada caso, por isso gasta
créditos: usa -n para controlar quantos.

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

    python medir_deriva.py --fechar-ciclo        verifica pelo id do rascunho
    python medir_deriva.py --fechar-ciclo -n 30  só os 30 mais recentes

--fechar-ciclo é um modo à parte, mais preciso e mais barato do que o resto
deste ficheiro: em vez de procurar heuristicamente "a próxima resposta da
loja na conversa" (que pode ser sobre outra coisa, numa devolução com várias
trocas), pergunta ao Graph pelo próprio id do rascunho criado -- o mesmo id
que se mantém depois de alguém o enviar, só passando a ter sentDateTime
preenchido. Não chama o Claude, não gasta créditos: só lê o Graph. O
resultado fica gravado (resultado_estado, resultado_semelhanca), para
metricas.py os poder mostrar sem repetir estas chamadas.

Só funciona para rascunhos criados depois de 27/08/2026 (quando o rascunho_id
passou a ser gravado -- Finding "fecho de ciclo do draft"); registos mais
antigos ficam de fora, sem alternativa possível.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from difflib import SequenceMatcher

import anthropic

import assistente as a
import casos_antigos as ca

_LIMIAR_TAL_E_QUAL = 90.0


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


def fechar_ciclo(graph: a.Graph, con: sqlite3.Connection, limite: int) -> None:
    """Verifica, pelo id do próprio rascunho, se cada um foi enviado tal e
    qual, editado, apagado sem ser enviado, ou continua pendente -- e grava
    o resultado. Só lê o Graph; não chama o Claude."""
    linhas = con.execute(
        "SELECT message_id, corpo, rascunho_id FROM processados "
        "WHERE rascunho_id != '' "
        "  AND COALESCE(resultado_estado, '') NOT IN "
        "      ('enviado-tal-e-qual', 'enviado-editado', 'apagado') "
        "ORDER BY em DESC"
    ).fetchall()
    if limite:
        linhas = linhas[:limite]
    if not linhas:
        print("\nNenhum rascunho por verificar: ou nenhum tem rascunho_id ainda "
              "(só passou a ser gravado a partir de 27/08/2026), ou todos já têm "
              "um resultado final.\n")
        return

    contagem: Counter[str] = Counter()
    for message_id, corpo_original, rascunho_id in linhas:
        detalhe = graph.detalhe_rascunho(rascunho_id)
        semelhante: float | None = None
        if detalhe is None:
            estado = "apagado"
        elif not detalhe.get("sentDateTime"):
            estado = "pendente"
        else:
            corpo_final = a.cortar_citacao(
                a.para_texto((detalhe.get("body") or {}).get("content", ""))
            )
            semelhante = semelhanca(corpo_original or "", corpo_final)
            estado = "enviado-tal-e-qual" if semelhante >= _LIMIAR_TAL_E_QUAL else "enviado-editado"
        contagem[estado] += 1
        con.execute(
            "UPDATE processados SET resultado_estado = ?, resultado_semelhanca = ?, "
            "resultado_medido_em = ? WHERE message_id = ?",
            (estado, semelhante, a.agora(), message_id),
        )
    con.commit()

    print(f"\n{len(linhas)} rascunho(s) verificado(s) pelo id\n")
    for estado, n in contagem.most_common():
        print(f"  {estado:<20} {n}")
    pendentes = contagem["pendente"]
    if pendentes:
        print(f"\n{pendentes} continuam pendentes (ainda na pasta de rascunhos, "
              "nem enviados nem apagados) -- ficam para a próxima verificação.")
    print()


def casos_do_registo(graph: a.Graph, cfg: a.Config, con: sqlite3.Connection,
                      incluir_escalados: bool, limite: int) -> tuple[list[tuple[dict, str]], int]:
    """(msg, resposta_real) a partir do que o assistente já processou."""
    acoes = "('rascunhar','escalar')" if incluir_escalados else "('rascunhar')"
    linhas = con.execute(
        f"SELECT message_id, assunto FROM processados "
        f"WHERE acao IN {acoes} AND conversation_id != '' ORDER BY em DESC"
    ).fetchall()
    if limite:
        linhas = linhas[:limite]

    casos = []
    sem_resposta = 0
    for message_id, _assunto in linhas:
        msg = buscar_email(graph, message_id)
        if msg is None:
            continue
        msg["_caixa"] = cfg.mailbox
        try:
            real = resposta_real(graph, msg, cfg.aviso)
        except Exception:
            real = None
        if not real:
            sem_resposta += 1
            continue
        casos.append((msg, real))
    return casos, sem_resposta


def casos_da_pasta(graph: a.Graph, cfg: a.Config, bloqueados: frozenset[str],
                    pasta: str, limite: int) -> tuple[list[tuple[dict, str]], int]:
    """(msg, resposta_real) a partir de conversas reais de qualquer pasta,
    mesmo as que nunca passaram pelo assistente. Mesma procura de pares do
    casos_antigos.py, mas devolvendo os pares em vez de os imprimir.
    """
    mensagens = ca.listar_pasta(graph, pasta)
    pares = ca.encontrar_pares(graph, mensagens, cfg, bloqueados, limite or 20)

    casos = []
    for cliente_msg, loja_msg in pares:
        graph.detalhe(cliente_msg, cfg.max_body)
        graph.detalhe(loja_msg, cfg.max_body)
        casos.append((cliente_msg, loja_msg["corpo"]))
    return casos, 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compara rascunhos com respostas reais")
    p.add_argument("-n", type=int, default=0, help="limita aos N mais recentes")
    p.add_argument("--so-numero", action="store_true", help="não mostra o texto")
    p.add_argument(
        "--incluir-escalados", action="store_true",
        help="tenta também gerar rascunho para emails que ficaram 'escalar', "
             "não só os que já foram 'rascunhar'",
    )
    p.add_argument(
        "--pasta",
        help="fonte alternativa ao registo local: uma pasta do Graph (ex.: "
             "deleteditems). Chama o Claude para cada caso, gasta créditos.",
    )
    p.add_argument(
        "--fechar-ciclo", action="store_true",
        help="verifica pelo id do rascunho se foi enviado tal e qual, editado, "
             "ou apagado; grava o resultado. Só lê o Graph, não gasta créditos.",
    )
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    graph = a.Graph(cfg)

    if args.fechar_ciclo:
        fechar_ciclo(graph, sqlite3.connect(cfg.db), args.n)
        return 0

    shopify = a.Shopify(cfg)
    cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
    prompt = a.construir_prompt(cfg)
    bloqueados = a.carregar_blocklist(cfg.blocklist)

    if args.pasta:
        casos, sem_resposta = casos_da_pasta(graph, cfg, bloqueados, args.pasta, args.n)
    else:
        con = sqlite3.connect(cfg.db)
        casos, sem_resposta = casos_do_registo(
            graph, cfg, con, args.incluir_escalados, args.n
        )
    if not casos:
        sys.exit("Nenhum caso encontrado com estes critérios.")

    resultados = []
    ja_nao_rascunha = 0

    for msg, real in casos:
        if a.triar(msg, cfg, bloqueados):
            continue
        graph.detalhe(msg, cfg.max_body)
        # Sem isto, um email do formulário de devolução (noreply@formspree.io,
        # com list-unsubscribe carimbado por cima) era descartado aqui como se
        # fosse bulk mail -- exatamente o bug de produção de 22/08/2026 que a
        # exceção em triar_cabecalhos() existe para evitar.
        veio_contacto, veio_devolucao, motivo_formulario = a.desembrulhar_formularios(msg)
        if motivo_formulario or a.triar_cabecalhos(msg, veio_contacto, veio_devolucao):
            continue

        assunto = msg["assunto"]
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
