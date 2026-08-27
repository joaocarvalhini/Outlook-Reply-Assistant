#!/usr/bin/env python3
"""Painel de números do registo local: para onde vão os emails e porquê.

    python metricas.py              últimos 30 dias
    python metricas.py --dias 7     só a última semana
    python metricas.py --tudo       desde sempre

Lê só o que já está gravado em processados. Não faz chamadas à API nem à
caixa — os números não mudam se este script correr dez vezes seguidas.

Serve para responder à pergunta que motivou toda esta arquitetura: a
percentagem de escalação está mesmo a descer, e em que categorias é que
ainda há trabalho a fazer?
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import assistente as a

LARGURA = 72


def _barra(n: int, total: int, largura: int = 30) -> str:
    if total == 0:
        return ""
    cheio = round(largura * n / total)
    return "█" * cheio + "░" * (largura - cheio)


def _tabela(titulo: str, contagem: Counter, total: int) -> None:
    if not contagem:
        return
    print(f"\n{titulo}")
    for chave, n in contagem.most_common():
        rotulo = (chave or "(vazio)")[:28]
        print(f"  {rotulo:<28} {n:>4}  {_barra(n, total)}  {n/total:.0%}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Métricas do registo local")
    p.add_argument("--dias", type=int, default=30, help="janela em dias (omissão: 30)")
    p.add_argument("--tudo", action="store_true", help="ignora a janela, usa tudo")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)

    condicao, valores = "1=1", []
    if not args.tudo:
        desde = (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
        condicao, valores = "em >= ?", [desde]

    linhas = con.execute(
        f"SELECT acao, categoria, dossie_tipo, dossie_risco FROM processados "
        f"WHERE {condicao} ORDER BY em",
        valores,
    ).fetchall()
    resultados_draft = con.execute(
        f"SELECT resultado_estado FROM processados "
        f"WHERE {condicao} AND COALESCE(resultado_estado, '') != '' "
        f"  AND resultado_estado != 'pendente'",
        valores,
    ).fetchall()

    janela = "todo o histórico" if args.tudo else f"últimos {args.dias} dia(s)"
    print(f"\n{len(linhas)} email(is) processado(s) · {janela}\n")
    if not linhas:
        print("Sem registos nesta janela.\n")
        return 0

    acoes = Counter(acao or "(vazio)" for acao, *_ in linhas)
    total = len(linhas)
    print("─" * LARGURA)
    for chave in ("rascunhar", "escalar", "saltar"):
        n = acoes.get(chave, 0)
        print(f"  {chave:<12} {n:>4}  {_barra(n, total)}  {n/total:.0%}")
    outras = total - sum(acoes.get(c, 0) for c in ("rascunhar", "escalar", "saltar"))
    if outras:
        print(f"  outro/erro   {outras:>4}")

    escalados = [l for l in linhas if l[0] == "escalar"]
    if escalados:
        _tabela(
            f"Categoria dos {len(escalados)} escalado(s)",
            Counter(cat or "(sem categoria)" for _, cat, _, _ in escalados),
            len(escalados),
        )

        acionaveis = [l for l in escalados if (l[2] or "nenhum") != "nenhum"]
        if acionaveis:
            _tabela(
                f"Risco dos {len(acionaveis)} dossiê(s) preparado(s)",
                Counter(r or "(sem risco)" for _, _, _, r in acionaveis),
                len(acionaveis),
            )
        sem_dossie = len(escalados) - len(acionaveis)
        if sem_dossie:
            print(f"\n{sem_dossie} escalado(s) sem dossiê "
                  "(falta de conhecimento, identidade por confirmar, ou "
                  "encomenda sem correspondência)")

    if resultados_draft:
        estados = Counter(r[0] for r in resultados_draft)
        _tabela(
            f"Resultado de {len(resultados_draft)} rascunho(s) verificado(s) "
            "pelo id (medir_deriva.py --fechar-ciclo)",
            estados, len(resultados_draft),
        )
        aceites = estados.get("enviado-tal-e-qual", 0)
        enviados = aceites + estados.get("enviado-editado", 0)
        if enviados:
            print(f"\nTaxa de aceitação sem alterações: {aceites}/{enviados} "
                  f"dos enviados ({aceites/enviados:.0%})")
    else:
        print("\nResultado dos rascunhos: sem dados ainda. Correr "
              "'medir_deriva.py --fechar-ciclo' periodicamente para começar a medir.")

    print(f"\n{'─' * LARGURA}")
    print("Para ver quais casos e não só quantos: dossie.py, lacunas.py")
    print("Para saber se o rascunho está bom: medir_deriva.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
