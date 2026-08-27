#!/usr/bin/env python3
"""Cópia de segurança do registo local e purga do texto que já não é preciso.

    python manutencao.py                 as duas coisas: é o que o cron corre
    python manutencao.py --backup        só a cópia de segurança
    python manutencao.py --purgar        só a purga
    python manutencao.py --dias 30       janela da purga (omissão: 90)
    python manutencao.py --simular       diz o que faria, sem escrever nada

# Porque é que isto existe

Duas coisas que faltavam ao registo local, e que não são a mesma:

**Cópia de segurança.** O `assistente.db` guarda o cursor da caixa. Perdê-lo não
é perder histórico — é perder o sítio onde o assistente ia. Uma reinstalação sem
cursor começa em "agora" e salta em silêncio tudo o que chegou entretanto.

**Purga.** A tabela `processados` guarda o corpo dos rascunhos, o assunto e os
dossiês, que são correspondência de clientes. Sem uma janela declarada, isso
acumula-se para sempre — o que é um problema de RGPD e não de disco.

# O que a purga apaga e o que deixa

Apaga o texto livre e longo, que é onde o conteúdo pessoal vive de facto.
Mantém a classificação, que é curta e é o que dá valor às métricas: sem `acao`,
`categoria` e `em`, o metricas.py e o lacunas.py deixam de ter o que ler.

**Não apaga linhas nenhumas, de propósito.** A chave `message_id` é o que impede
o assistente de responder duas vezes ao mesmo email. Apagar a linha devolveria a
mensagem ao estado de "nunca vista" — e, se alguém repuser um cursor antigo a
partir de uma cópia de segurança, o assistente voltaria a rascunhar emails já
respondidos há meses.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import assistente as a

# Colunas de texto livre. São as que carregam conteúdo do cliente e as que
# crescem — o corpo de um rascunho e uma resposta de dossiê são parágrafos.
COLUNAS_A_PURGAR = (
    "assunto",
    "corpo",
    "dossie_resumo",
    "dossie_validacao",
    "dossie_accao",
    "dossie_resposta",
    "por_responder",
)

# O que fica: message_id e conversation_id (deduplicação e fio), acao, motivo,
# categoria e em (métricas), lacuna_tema e lacuna_em_falta (fila de lacunas),
# confianca_encomenda, dossie_tipo, dossie_risco e dossie_link (análise).

DIAS_POR_OMISSAO = 90
COPIAS_A_MANTER = 14


def copiar(origem: Path, pasta: Path, simular: bool) -> Path | None:
    """Cópia consistente com a API de backup do SQLite.

    Um `cp` do ficheiro pode apanhar a base a meio de uma escrita; esta API
    trata disso, e o assistente pode estar a correr ao mesmo tempo — corre de
    dois em dois minutos e ninguém quer coordenar cron com timer.
    """
    if not origem.exists():
        sys.exit(f"Não encontrei a base em {origem}")

    destino = pasta / f"{origem.stem}-{datetime.now(timezone.utc):%Y-%m-%d}.db"
    if simular:
        print(f"[simular] copiaria {origem} -> {destino}")
        return None

    pasta.mkdir(parents=True, exist_ok=True)
    fonte = sqlite3.connect(f"file:{origem}?mode=ro", uri=True)
    try:
        alvo = sqlite3.connect(destino)
        try:
            fonte.backup(alvo)
        finally:
            alvo.close()
    finally:
        fonte.close()
    return destino


def rodar(pasta: Path, manter: int, simular: bool) -> list[Path]:
    """Deita fora as cópias mais antigas. Devolve as que apagou."""
    copias = sorted(pasta.glob("*.db"), key=lambda p: p.name)
    a_apagar = copias[:-manter] if len(copias) > manter else []
    for c in a_apagar:
        if simular:
            print(f"[simular] apagaria a cópia antiga {c.name}")
        else:
            c.unlink()
    return a_apagar


def purgar(con: sqlite3.Connection, dias: int, simular: bool) -> int:
    """Esvazia o texto livre das linhas mais antigas do que a janela."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    condicao = (
        "em < ? AND ("
        + " OR ".join(f"COALESCE({c},'') != ''" for c in COLUNAS_A_PURGAR)
        + ")"
    )
    (quantas,) = con.execute(
        f"SELECT COUNT(*) FROM processados WHERE {condicao}", (limite,)
    ).fetchone()
    if simular or not quantas:
        return int(quantas)

    con.execute(
        f"UPDATE processados SET {', '.join(f'{c} = NULL' for c in COLUNAS_A_PURGAR)} "
        f"WHERE {condicao}",
        (limite,),
    )
    con.commit()
    con.execute("VACUUM")
    return int(quantas)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cópia de segurança e purga do registo")
    p.add_argument("--backup", action="store_true", help="só a cópia de segurança")
    p.add_argument("--purgar", action="store_true", help="só a purga")
    p.add_argument("--dias", type=int, default=DIAS_POR_OMISSAO,
                   help=f"janela da purga em dias (omissão: {DIAS_POR_OMISSAO})")
    p.add_argument("--manter", type=int, default=COPIAS_A_MANTER,
                   help=f"cópias a guardar (omissão: {COPIAS_A_MANTER})")
    p.add_argument("--pasta", type=Path, default=Path("backups"))
    p.add_argument("--simular", action="store_true", help="não escreve nada")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)

    # Sem nenhuma das duas escolhida, faz as duas: é o caso do cron.
    fazer_backup = args.backup or not args.purgar
    fazer_purga = args.purgar or not args.backup

    if fazer_backup:
        destino = copiar(cfg.db, args.pasta, args.simular)
        if destino:
            print(f"cópia de segurança: {destino} ({destino.stat().st_size} bytes)")
        apagadas = rodar(args.pasta, args.manter, args.simular)
        if apagadas and not args.simular:
            print(f"cópias antigas apagadas: {len(apagadas)}")

    if fazer_purga:
        con = sqlite3.connect(cfg.db)
        try:
            quantas = purgar(con, args.dias, args.simular)
        finally:
            con.close()
        verbo = "seriam purgadas" if args.simular else "purgadas"
        print(f"{quantas} linha(s) com mais de {args.dias} dias {verbo} "
              f"(texto livre; a classificação fica)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
