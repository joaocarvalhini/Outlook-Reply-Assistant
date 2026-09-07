#!/usr/bin/env python3
"""A lista de promessas por fechar, e como fechá-las à mão.

    python compromissos.py                     pendentes, mais parados primeiro
    python compromissos.py --dias 14           só os parados há 14 dias ou mais
    python compromissos.py --fechar "#22197"   marca como cumprido
    python compromissos.py --fechar "#22197" --tipo reembolso

Porque existe
-------------
Um compromisso só se fecha quando um email seguinte confirma que aconteceu. Se
a loja cumpre e o cliente fica satisfeito, esse email nunca chega e o registo
fica "pendente" para sempre.

Medido a 08/09/2026: **160 de 170 compromissos por fechar**, e só 6 alguma vez
chegaram a "concluído". Sessenta e oito deles em conversas sem um email há mais
de uma semana. Cada pendente antigo continua a ser injetado em todos os emails
seguintes dessa conversa, e COMPROMISSO_ANTERIOR é hoje 40% das escalações que
restam — a maior fatia do trabalho manual que sobra para o lojista.

Até aqui a tabela era escrita pelo modelo e lida pelo modelo. Nenhuma pessoa
tinha forma de a ver ou corrigir. É isso que isto resolve.

Não chama o Claude nem o Graph. Só lê e escreve o registo local.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import assistente as a

LARGURA = 78


def pendentes(con: sqlite3.Connection, dias_min: int = 0) -> list[dict]:
    """Os compromissos por cumprir, do mais parado para o mais recente."""
    hoje = a.agora()
    saida = []
    for cid, tipo, desc, data, em in con.execute(
        "SELECT conversation_id, tipo, descricao, data_prometida, atualizado_em "
        "FROM compromissos WHERE estado = 'pendente' ORDER BY atualizado_em"
    ).fetchall():
        dias = a.dias_desde(em, hoje)
        if dias < dias_min:
            continue
        linha = con.execute(
            "SELECT assunto, COUNT(*) FROM processados WHERE conversation_id = ? "
            "ORDER BY em DESC", (cid,)
        ).fetchone()
        assunto, emails = (linha or ("", 0))
        saida.append({
            "conversation_id": cid, "tipo": tipo, "descricao": desc or "",
            "data": data or "", "dias": dias, "assunto": assunto or "(sem assunto)",
            "emails": emails,
        })
    saida.sort(key=lambda c: -c["dias"])
    return saida


def procurar(con: sqlite3.Connection, texto: str) -> list[str]:
    """As conversas que correspondem ao texto -- id exato, ou parte do assunto.

    Um conversation_id tem centenas de caracteres e ninguém o escreve à mão. O
    que o lojista reconhece é o assunto, por isso é por aí que se procura.
    """
    alvo = texto.strip().lower()
    if not alvo:
        return []
    achados = []
    for (cid,) in con.execute(
        "SELECT DISTINCT conversation_id FROM compromissos WHERE estado = 'pendente'"
    ).fetchall():
        if cid.lower() == alvo or cid.lower().startswith(alvo):
            return [cid]
        linha = con.execute(
            "SELECT assunto FROM processados WHERE conversation_id = ? "
            "AND LOWER(COALESCE(assunto,'')) LIKE ? LIMIT 1",
            (cid, f"%{alvo}%"),
        ).fetchone()
        if linha:
            achados.append(cid)
    return achados


def fechar(con: sqlite3.Connection, conversation_id: str, tipo: str = "") -> int:
    """Marca como concluído. Devolve quantos fechou.

    "concluido" e não outra coisa: quem corre isto está a afirmar que a promessa
    foi cumprida. Se só quiser tirá-la da frente sem afirmar isso, o caminho é
    esperar pelos 14 dias em que ela deixa de ser dada como certa (ver
    DIAS_COMPROMISSO_SEM_CONFIRMACAO em assistente.py).
    """
    where = "conversation_id = ? AND estado = 'pendente'"
    valores: list[object] = [conversation_id]
    if tipo:
        where += " AND tipo = ?"
        valores.append(tipo)
    cur = con.execute(
        f"UPDATE compromissos SET estado = 'concluido', atualizado_em = ? WHERE {where}",
        [a.agora()] + valores,
    )
    con.commit()
    return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Promessas por cumprir")
    p.add_argument("--dias", type=int, default=0,
                   help="só os parados há N dias ou mais")
    p.add_argument("--fechar", metavar="TEXTO",
                   help="marca como cumprido: parte do assunto, ou o id da conversa")
    p.add_argument("--tipo", default="",
                   help="com --fechar, fecha só este tipo (ex.: reembolso)")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = a.abrir_db(cfg.db)

    if args.fechar:
        achados = procurar(con, args.fechar)
        if not achados:
            print(f"\nNenhum compromisso pendente corresponde a '{args.fechar}'.\n")
            return 1
        if len(achados) > 1:
            print(f"\n'{args.fechar}' corresponde a {len(achados)} conversas. "
                  "Sê mais específico:\n")
            for cid in achados:
                linha = con.execute(
                    "SELECT assunto FROM processados WHERE conversation_id = ? LIMIT 1",
                    (cid,)).fetchone()
                print(f"  {(linha[0] if linha else '(sem assunto)')[:60]}")
            print()
            return 1
        n = fechar(con, achados[0], args.tipo)
        print(f"\n{n} compromisso(s) marcado(s) como cumprido(s).\n")
        return 0

    lista = pendentes(con, args.dias)
    if not lista:
        print("\nNenhum compromisso pendente. Ou está tudo cumprido e fechado, "
              "ou ainda não passou nenhum email que prometesse alguma coisa.\n")
        return 0

    janela = f", parados há {args.dias}+ dias" if args.dias else ""
    print(f"\n{len(lista)} compromisso(s) por cumprir{janela}\n")
    print(f"{'dias':>5} {'tipo':<13} {'emails':>6}  assunto")
    print("─" * LARGURA)
    for c in lista:
        marca = " !" if c["dias"] >= a.DIAS_COMPROMISSO_SEM_CONFIRMACAO else "  "
        print(f"{c['dias']:5}{marca}{c['tipo']:<13} {c['emails']:>6}  "
              f"{c['assunto'][:44]}")
        if c["descricao"]:
            print(f"        {c['descricao'][:66]}")

    velhos = [c for c in lista if c["dias"] >= a.DIAS_COMPROMISSO_SEM_CONFIRMACAO]
    if velhos:
        print(f"\n{len(velhos)} marcados com ! estão sem confirmação há "
              f"{a.DIAS_COMPROMISSO_SEM_CONFIRMACAO}+ dias. O assistente já "
              "deixou de os dar como certos, mas continuam a aparecer no "
              "contexto até serem fechados aqui.")
    print("\nPara fechar:  python compromissos.py --fechar \"parte do assunto\"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
