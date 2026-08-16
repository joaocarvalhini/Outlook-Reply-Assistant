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

Percorre a pasta indicada (Itens Eliminados por omissão, porque é onde fica
a maior parte do histórico antigo) à procura de conversas com pelo menos uma
mensagem do cliente seguida de uma resposta da loja. As duas mensagens de
cada par só se buscam por inteiro (corpo completo) depois de escolhidas — a
listagem inicial é só metadados, para não gastar uma chamada por mensagem
num universo de milhares.
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


def encontrar_pares(mensagens: list[dict], cfg: a.Config,
                     bloqueados: frozenset[str]) -> list[tuple[dict, dict]]:
    """Agrupa por conversa e devolve (mensagem do cliente, resposta da loja).

    Só o primeiro par de cada conversa — o resto do fio é contexto, não um
    segundo caso independente.
    """
    por_conversa: dict[str, list[dict]] = {}
    for m in mensagens:
        por_conversa.setdefault(m["conversation_id"], []).append(m)

    pares = []
    for msgs in por_conversa.values():
        msgs.sort(key=lambda m: m["recebido"])
        i_cliente = next(
            (i for i, m in enumerate(msgs) if a.triar(m, cfg, bloqueados) is None),
            None,
        )
        if i_cliente is None:
            continue
        resposta = next(
            (m for m in msgs[i_cliente + 1:] if a.e_da_loja(m["de"], cfg.mailbox)),
            None,
        )
        if resposta is not None:
            pares.append((msgs[i_cliente], resposta))

    pares.sort(key=lambda par: par[0]["recebido"], reverse=True)
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

    pares = encontrar_pares(mensagens, cfg, bloqueados)
    if args.contem:
        alvo = args.contem.lower()
        pares = [par for par in pares if alvo in par[0]["assunto"].lower()]
    pares = pares[: args.n]

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
