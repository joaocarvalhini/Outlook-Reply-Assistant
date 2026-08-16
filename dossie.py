#!/usr/bin/env python3
"""A fila de casos escalados que já vêm preparados.

    python dossie.py                 casos por decidir, mais recentes primeiro
    python dossie.py --lista         uma linha por caso, para escolher rápido
    python dossie.py --caso 42       só o caso #42, sem percorrer os outros
    python dossie.py -n 5            só os 5 mais recentes
    python dossie.py --tipo cancelamento
    python dossie.py --risco alto

Escalar não é despachar. Cada caso aqui traz o que foi confirmado, o que
impede, a ação recomendada e a resposta ao cliente já redigida. O objetivo é
quem decide perceber o caso em segundos, em vez de ir investigar.

Não executa nada: a recomendação é uma recomendação. Só cancelamento tem
execução automatizável, e mesmo esse só corre através do aprovar.py, nunca
daqui — este comando é sempre de leitura.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import textwrap

import assistente as a

LARGURA = 72
CORES_RISCO = {"baixo": "baixo ", "medio": "MÉDIO ", "alto": "ALTO  "}


def _quebrar(texto: str, indent: str = "  ") -> str:
    """Quebra às margens, mas mantém os parágrafos separados.

    A resposta ao cliente tem parágrafos com significado; colá-los todos torna
    difícil ver se o texto está bem escrito, que é precisamente o que quem revê
    está a tentar avaliar.
    """
    saida: list[str] = []
    for paragrafo in str(texto or "").split("\n"):
        if not paragrafo.strip():
            if saida and saida[-1] != "":
                saida.append("")
            continue
        saida.extend(textwrap.wrap(paragrafo.strip(), LARGURA - len(indent)))
    return "\n".join(indent + l if l else "" for l in saida)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Casos escalados já preparados")
    p.add_argument("-n", type=int, default=0, help="limita aos N mais recentes")
    p.add_argument("--tipo", help="cancelamento, reembolso, troca, garantia, ...")
    p.add_argument("--risco", choices=("baixo", "medio", "alto"))
    p.add_argument("--caso", type=int, help="só este caso, pelo número visto aqui")
    p.add_argument("--lista", action="store_true",
                    help="uma linha por caso, para escolher qual abrir")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)

    condicoes = ["acao = 'escalar'", "COALESCE(dossie_tipo,'') NOT IN ('', 'nenhum')"]
    valores: list[object] = []
    if args.caso:
        condicoes.append("rowid = ?")
        valores.append(args.caso)
    if args.tipo:
        condicoes.append("dossie_tipo = ?")
        valores.append(args.tipo)
    if args.risco:
        condicoes.append("dossie_risco = ?")
        valores.append(args.risco)

    linhas = con.execute(
        "SELECT rowid, assunto, dossie_tipo, dossie_resumo, dossie_validacao, "
        "       dossie_accao, dossie_risco, dossie_resposta, dossie_link, em "
        "FROM processados WHERE " + " AND ".join(condicoes) + " ORDER BY em DESC",
        valores,
    ).fetchall()
    if args.n:
        linhas = linhas[: args.n]

    if not linhas:
        if args.caso:
            print(f"\nCaso #{args.caso} não existe, ou não é um caso com dossiê "
                  "preparado (acao='escalar' e dossie_tipo definido).\n")
            return 1
        print("\nNenhum caso preparado.\n\nOu ainda não passou nenhum pedido "
              "acionável desde que os dossiês foram ligados, ou os escalados "
              "recentes eram lacunas de conhecimento e identidades por "
              "confirmar, que não se preparam.\n")
        return 0

    if args.lista:
        print(f"\n{len(linhas)} caso(s) à espera de decisão\n")
        for caso_id, assunto, tipo, *_resto, risco, _resposta, _link, em in linhas:
            marca = CORES_RISCO.get(risco, (risco or "?") + " ").strip()
            print(f"  #{caso_id:<5} {tipo.upper().replace('_', ' '):<18} "
                  f"risco {marca:<6} {em[:10]}  {assunto[:36]}")
        print()
        return 0

    print(f"\n{len(linhas)} caso(s) à espera de decisão\n")

    for (caso_id, assunto, tipo, resumo, validacao, accao, risco, resposta,
         link, em) in linhas:
        print("━" * LARGURA)
        marca = CORES_RISCO.get(risco, (risco or "?") + " ")
        print(f"caso #{caso_id}   ·   {tipo.upper().replace('_', ' ')}   ·   "
              f"risco {marca.strip()}   ·   {em[:16].replace('T', ' ')}")
        print("━" * LARGURA)
        print(f"\n{assunto}\n")
        print(_quebrar(resumo))

        if validacao:
            print("\n  Validação")
            for linha in str(validacao).split("\n"):
                linha = linha.strip()
                if not linha:
                    continue
                # "sim, ..." é uma verificação que passou; "não, ..." é um
                # impedimento. O símbolo é para se ver de relance qual é qual.
                simbolo = "✓" if linha.lower().startswith("sim") else "✗"
                texto = linha.split(",", 1)[-1].strip() if "," in linha else linha
                print(f"    {simbolo} {texto}")

        if accao:
            print("\n  Ação recomendada (exige aprovação de uma pessoa)")
            print(_quebrar(accao, "    "))

        if link:
            print(f"\n  Abrir no admin: {link}")

        if resposta:
            print("\n  Resposta ao cliente, a aguardar aprovação")
            print(_quebrar(resposta, "    "))

        if tipo == "cancelamento":
            print(f"\n  Para executar: python aprovar.py {caso_id}")

        print()

    print("━" * LARGURA)
    print("A ação é sempre executada por uma pessoa. Para cancelamentos,")
    print("'python aprovar.py <caso>' revalida e pede confirmação explícita")
    print("antes de tocar na Shopify; os outros tipos ainda só se fazem no")
    print("admin, à mão.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
