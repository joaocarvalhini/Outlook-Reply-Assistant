#!/usr/bin/env python3
"""Pares pergunta-resposta de conversas antigas, para leitura humana.

    python casos_antigos.py                    até 15 pares, mais recentes primeiro
    python casos_antigos.py -n 40
    python casos_antigos.py --contem reclamação
    python casos_antigos.py --pasta inbox       por omissão lê "deleteditems"

Não passa nada pelo modelo — zero créditos gastos. Não escreve nada, nem no
registo local nem na caixa. Serve para uma pessoa ler como é que casos
difíceis (reclamações, disputas, decisões de exceção) foram resolvidos antes
deste projeto existir, e usar isso para melhorar a base de conhecimento ou a
lista de categorias de escalação — não para gerar dados de treino
automaticamente. Um "apagado" não é sinónimo de "bom exemplo": a maioria vai
ser ruído, e cabe a quem lê decidir o que vale a pena.

A pergunta do cliente vem da pasta indicada (Itens Eliminados por omissão,
onde fica a maior parte do histórico antigo). A resposta da loja procura-se
na caixa inteira, não só nessa pasta — a pergunta pode ter sido apagada e a
resposta continuar nos Itens Enviados, que raramente se apagam. Procurar só
dentro da mesma pasta perdia a maioria dos pares reais nesta caixa.

As duas mensagens de cada par só se buscam por inteiro (corpo completo)
depois de escolhidas — a listagem inicial é só metadados, para não gastar
uma chamada por mensagem num universo de milhares.
"""

from __future__ import annotations

import argparse
import textwrap

import assistente as a

LARGURA = 72


def _quebrar(texto: str, indent: str = "  ") -> str:
    saida: list[str] = []
    for paragrafo in str(texto or "").split("\n"):
        if not paragrafo.strip():
            if saida and saida[-1] != "":
                saida.append("")
            continue
        saida.extend(textwrap.wrap(paragrafo.strip(), LARGURA - len(indent)))
    return "\n".join(indent + l if l else "" for l in saida)


def listar_pasta(graph: a.Graph, pasta: str) -> list[dict]:
    """Pagina a pasta inteira, só com os campos leves de CAMPOS_LISTA."""
    url = f"{graph.base}/mailFolders/{pasta}/messages"
    params: dict[str, str] | None = {
        "$select": a.CAMPOS_LISTA,
        "$top": "100",
        "$orderby": "receivedDateTime asc",
    }
    todas: list[dict] = []
    while url:
        dados = graph._pedir("GET", url, params=params)
        todas.extend(dados.get("value", []))
        url = dados.get("@odata.nextLink")
        params = None  # o nextLink já traz a query toda embutida
    return [graph._converter(m) for m in todas]


def resposta_da_loja(graph: a.Graph, cliente_msg: dict, cfg: a.Config) -> dict | None:
    """A primeira resposta da loja na mesma conversa, em qualquer pasta.

    A pergunta do cliente pode estar em Itens Eliminados e a resposta da loja
    em Itens Enviados — pastas diferentes da mesma conversa. Procurar só
    dentro da pasta de origem perdia a maioria dos pares: confirmado nesta
    sessão que das conversas com mensagem de cliente, menos de metade tinham
    a resposta na mesma pasta. A consulta geral (sem mailFolders no caminho)
    alcança a caixa toda, como o historico() já faz para o fio ativo.
    """
    dados = graph._pedir(
        "GET",
        f"{graph.base}/messages",
        params={
            "$filter": f"conversationId eq '{cliente_msg['conversation_id']}'",
            "$select": a.CAMPOS_LISTA,
            "$top": "25",
        },
    )
    candidatas = [
        graph._converter(m)
        for m in dados.get("value", [])
        if m.get("receivedDateTime", "") > cliente_msg["recebido"]
    ]
    candidatas = [m for m in candidatas if a.e_da_loja(m["de"], cfg.mailbox)]
    if not candidatas:
        return None
    candidatas.sort(key=lambda m: m["recebido"])
    return candidatas[0]


def encontrar_pares(graph: a.Graph, mensagens: list[dict], cfg: a.Config,
                     bloqueados: frozenset[str], limite: int) -> list[tuple[dict, dict]]:
    """Para cada mensagem de cliente na pasta, procura a resposta na caixa
    inteira. Para na primeira mensagem de cliente sem resposta encontrada
    depois de já ter `limite` pares — evita percorrer centenas de conversas
    quando só se pediram poucos pares.
    """
    candidatos = sorted(
        (m for m in mensagens if a.triar(m, cfg, bloqueados) is None),
        key=lambda m: m["recebido"],
        reverse=True,
    )
    pares = []
    for cliente_msg in candidatos:
        resposta = resposta_da_loja(graph, cliente_msg, cfg)
        if resposta is not None:
            pares.append((cliente_msg, resposta))
        if len(pares) >= limite:
            break
    return pares


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pares pergunta-resposta de conversas antigas")
    p.add_argument("-n", type=int, default=15, help="quantos pares mostrar (omissão: 15)")
    p.add_argument("--pasta", default="deleteditems",
                    help="pasta do Graph a percorrer (omissão: deleteditems)")
    p.add_argument("--contem", help="filtra por palavra no assunto")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    graph = a.Graph(cfg)
    bloqueados = a.carregar_blocklist(cfg.blocklist)

    print(f"\nA listar {args.pasta}...")
    mensagens = listar_pasta(graph, args.pasta)
    print(f"{len(mensagens)} mensagem(ns) na pasta.\n")

    if args.contem:
        alvo = args.contem.lower()
        mensagens = [m for m in mensagens if alvo in m["assunto"].lower()]

    print("A procurar respostas na caixa inteira (uma conversa de cada vez)...")
    pares = encontrar_pares(graph, mensagens, cfg, bloqueados, args.n)

    if not pares:
        print("Nenhum par pergunta-resposta encontrado com estes filtros.\n")
        return 0

    print(f"{len(pares)} par(es) — a ir buscar o corpo completo de cada um...\n")

    for cliente_msg, loja_msg in pares:
        graph.detalhe(cliente_msg, cfg.max_body)
        graph.detalhe(loja_msg, cfg.max_body)

        print("━" * LARGURA)
        print(f"{cliente_msg['assunto']}   ·   {cliente_msg['recebido'][:16].replace('T', ' ')}")
        print("━" * LARGURA)
        print("\n  CLIENTE")
        print(_quebrar(cliente_msg["corpo"], "    "))
        print(f"\n  LOJA  ({loja_msg['recebido'][:16].replace('T', ' ')})")
        print(_quebrar(loja_msg["corpo"], "    "))
        print()

    print("━" * LARGURA)
    print("Não gastou créditos da API nem escreveu nada. Leitura só.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
