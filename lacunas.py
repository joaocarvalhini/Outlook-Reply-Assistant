#!/usr/bin/env python3
"""A fila de lacunas de conhecimento, e como fechá-las.

    python lacunas.py                 lacunas por fechar, mais frequentes primeiro
    python lacunas.py --categorias    quanto custa cada causa de escalação
    python lacunas.py --tudo          inclui as que já estão cobertas

Não escreve nada na caixa nem na base de conhecimento. Uma lacuna só se fecha
escrevendo o facto num ficheiro de knowledge/, à mão, depois de o lojista o
confirmar. Isto diz quais valem mais o trabalho.

Nunca transformar a resposta do modelo em facto: o modelo escalou precisamente
por não saber. O que ele produz aqui é a pergunta, não a resposta.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter

import assistente as a


def _normalizar(tema: str) -> str:
    """Agrupa temas escritos de forma diferente para o mesmo assunto."""
    t = " ".join(str(tema or "").lower().split())
    t = re.sub(r"[^\wáàâãéêíóôõúç ]", "", t)
    # tira palavras vazias que só variam a redação
    return " ".join(w for w in t.split() if w not in {
        "de", "da", "do", "das", "dos", "a", "o", "as", "os", "para", "em", "e",
        "sobre", "com", "prazo", "informacao", "informação",
    })


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fila de lacunas de conhecimento")
    p.add_argument("--categorias", action="store_true",
                   help="mostra o peso de cada causa de escalação")
    p.add_argument("--tudo", action="store_true",
                   help="não esconde as lacunas já cobertas pela base")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)

    if args.categorias:
        linhas = con.execute(
            "SELECT COALESCE(NULLIF(categoria,''),'(sem categoria)'), COUNT(*) "
            "FROM processados WHERE acao='escalar' GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        total = sum(n for _c, n in linhas)
        if not total:
            print("Ainda não há escalações classificadas.")
            return 0
        print(f"\n{total} escalações classificadas\n")
        print(f"{'n':>5} {'%':>5}  categoria")
        print("─" * 56)
        for categoria, n in linhas:
            print(f"{n:5} {100*n/total:4.0f}%  {categoria}")
        sem = next((n for c, n in linhas if c == "(sem categoria)"), 0)
        if sem:
            print(f"\n{sem} escalações são anteriores à taxonomia e não contam "
                  f"para comparações.")
        return 0

    linhas = con.execute(
        "SELECT lacuna_tema, lacuna_em_falta, assunto, em FROM processados "
        "WHERE categoria='LACUNA_DE_CONHECIMENTO' AND COALESCE(lacuna_tema,'') != '' "
        "ORDER BY em DESC"
    ).fetchall()
    if not linhas:
        print("Nenhuma lacuna registada. Ou a base cobre tudo, ou ainda não "
              "passou nenhum email desde que a deteção foi ligada.")
        return 0

    base = a.carregar_base(cfg.knowledge_dir).lower()
    grupos: dict[str, list[tuple[str, str, str]]] = {}
    for tema, falta, assunto, em in linhas:
        grupos.setdefault(_normalizar(tema), []).append((tema, falta, em))

    ordenados = sorted(grupos.items(), key=lambda kv: len(kv[1]), reverse=True)
    print(f"\n{len(ordenados)} lacuna(s) distinta(s), em {len(linhas)} email(s)\n")

    mostradas = 0
    for chave, ocorrencias in ordenados:
        # Se as palavras do tema já aparecem todas na base, provavelmente foi
        # entretanto fechada e o registo é antigo.
        palavras = [w for w in chave.split() if len(w) > 3]
        coberta = bool(palavras) and all(w in base for w in palavras)
        if coberta and not args.tudo:
            continue
        mostradas += 1
        tema, falta, em = ocorrencias[0]
        marca = "coberta?" if coberta else f"{len(ocorrencias)}x"
        print(f"[{marca:>8}]  {tema}")
        print(f"             falta: {falta}")
        print(f"             visto pela última vez em {em[:10]}")
        print()

    if not mostradas:
        print("Todas as lacunas registadas parecem já estar cobertas pela base. "
              "Correr com --tudo para as ver.")
    else:
        print("Para fechar: confirmar o facto com o lojista e escrevê-lo num "
              "ficheiro de knowledge/. Nunca a partir do que o modelo supôs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
