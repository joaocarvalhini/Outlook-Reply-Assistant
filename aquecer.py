#!/usr/bin/env python3
"""Mantém a cache do prompt quente nas horas mortas.

Porque existe
-------------
Escrever o prefixo na cache custa 20x mais do que o ler: com o prefixo nas
~31K tokens, escrever são $0,124 e ler são $0,0062. Compensa portanto ler até
20 vezes para evitar uma única reescrita.

Medido a 31/08/2026 sobre 69 emails reais: das 8 falhas de cache do dia, 4
foram expirações de TTL durante a noite (intervalos de 162, 62 e 215 minutos
entre emails). Cada uma reescreveu o prefixo inteiro, e as quatro juntas foram $0,87 --
mais de um quarto da fatura do dia.

Também medido, e é o que torna isto possível: **cada leitura renova o TTL**.
No mesmo dia houve 5,2 horas seguidas de uso da cache sem uma única escrita,
com intervalos internos até 25,6 minutos. Se o TTL contasse desde a escrita,
teria expirado aos 60 minutos e havido nova escrita; não houve.

Havia duas entradas de cache -- o esquema entra no prefixo, e havia um esquema
para o núcleo e outro para o dossiê. Com o dossiê removido a 01/09/2026 ficou
uma só, e aquecer passou a custar metade.

O que faz
---------
Não aquece às cegas. Olha para o registo, vê há quanto tempo foi a última
chamada ao modelo, e só gasta se a cache estiver mesmo a arrefecer. De dia,
com emails de dez em dez minutos, não faz nada e não custa nada.

    python aquecer.py             # aquece se for preciso
    python aquecer.py --forcar    # aquece sempre (para testar)
    python aquecer.py --simular   # diz o que faria, sem chamar a API
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import anthropic

import assistente as a

# O TTL é de 1 hora. Aquece-se aos 40 minutos de silêncio para haver folga:
# entre a decisão e a chamada há latência, e o timer não é exato ao segundo.
MINUTOS_ATE_AQUECER = 40

# O aquecimento renova o TTL tal como um email real, mas não deixa rasto na
# tabela processados (não processou nada). Sem o registar, um silêncio longo
# voltava a aquecer a cada passagem do temporizador.
CHAVE_ULTIMO_AQUECIMENTO = "ultimo-aquecimento"

# Nada do que o modelo responda é usado -- só interessa que a API leia o
# prefixo e renove o TTL. Um teto baixo evita pagar saída à toa; a resposta
# sai truncada e isso é indiferente.
TETO_SAIDA = 4


def _quando(valor: object) -> datetime | None:
    try:
        return datetime.strptime(str(valor), "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def minutos_desde_ultima_chamada(con: sqlite3.Connection, agora: datetime) -> float | None:
    """Minutos desde a última leitura da cache, ou None se nunca houve.

    Conta tanto as chamadas reais (emails) como os aquecimentos: as duas
    renovam o TTL na mesma. Sem contar os aquecimentos, um silêncio longo
    aquecia a cada passagem do temporizador em vez de a cada 40 minutos --
    pagava-se cinco vezes o que se devia pagar uma.
    """
    linha = con.execute(
        "SELECT em FROM processados WHERE chamadas_modelo > 0 "
        "ORDER BY em DESC LIMIT 1"
    ).fetchone()
    candidatos = [_quando(linha[0]) if linha else None,
                  _quando(a.ler_meta(con, CHAVE_ULTIMO_AQUECIMENTO))]
    marcos = [c for c in candidatos if c is not None]
    if not marcos:
        return None
    return (agora - max(marcos)).total_seconds() / 60


def deve_aquecer(minutos: float | None) -> bool:
    """Sem registo nenhum, aquece: é o primeiro arranque e a cache está fria."""
    if minutos is None:
        return True
    return minutos >= MINUTOS_ATE_AQUECER


def aquecer_uma(cliente: object, modelo: str, prompt: str, schema: dict) -> dict:
    """Uma leitura do prefixo. Devolve o usage para se poder registar o custo."""
    resposta = cliente.messages.create(  # type: ignore[attr-defined]
        model=modelo,
        max_tokens=TETO_SAIDA,
        system=[{
            "type": "text",
            "text": prompt,
            # Tem de ser igual ao de decidir(), senão isto aquece uma entrada
            # diferente da que a passagem real vai usar -- pagava-se duas vezes
            # e não se poupava nada.
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }],
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": "ping"}],
    )
    u = getattr(resposta, "usage", None)
    return {
        "entrada": int(getattr(u, "input_tokens", 0) or 0),
        "saida": int(getattr(u, "output_tokens", 0) or 0),
        "cache_escrita": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        "cache_leitura": int(getattr(u, "cache_read_input_tokens", 0) or 0),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mantém a cache do prompt quente")
    p.add_argument("--forcar", action="store_true",
                   help="aquece mesmo que a cache esteja quente")
    p.add_argument("--simular", action="store_true",
                   help="diz o que faria, sem chamar a API")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    con = sqlite3.connect(cfg.db)
    # O registo guarda UTC (ver agora() em assistente.py); compara-se com um
    # UTC sem fuso para o strptime da outra ponta bater certo.
    agora_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    minutos = minutos_desde_ultima_chamada(con, agora_utc)

    if not args.forcar and not deve_aquecer(minutos):
        a.log("cache-quente", minutos=f"{minutos:.0f}" if minutos else "?")
        return 0

    quando = f"{minutos:.0f}min" if minutos is not None else "sem registo"
    if args.simular:
        a.log("cache-aquecer-simulado", silencio=quando)
        return 0

    cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
    prompt = a.construir_prompt(cfg)
    total = {"entrada": 0, "saida": 0, "cache_escrita": 0, "cache_leitura": 0}
    # Uma entrada só, desde que o dossiê foi removido a 01/09/2026. Antes eram
    # duas -- o esquema entra no prefixo em cache, e havia dois esquemas.
    for nome, schema in (("nucleo", a.ESQUEMA_NUCLEO),):
        try:
            u = aquecer_uma(cliente, cfg.modelo, prompt, schema)
        except Exception as exc:  # noqa: BLE001
            # Falhar a aquecer não é uma falha do sistema: na pior das
            # hipóteses a passagem seguinte reescreve a cache, como fazia
            # antes de isto existir. Não se propaga para não disparar o
            # OnFailure de um serviço que não afeta clientes.
            a.log("erro-aquecer", entrada=nome, erro=f"{type(exc).__name__}: {exc}")
            continue
        for k in total:
            total[k] += u[k]

    # Só conta como aquecida se a chamada passou: se falhou, o TTL não foi
    # renovado e a passagem seguinte deve tentar outra vez em vez de esperar
    # mais 40 minutos.
    if total["cache_leitura"] or total["cache_escrita"]:
        a.gravar_meta(con, CHAVE_ULTIMO_AQUECIMENTO, a.agora())

    custo = a.custo_estimado(cfg.modelo, total)
    a.log("cache-aquecida", silencio=quando,
          lido=total["cache_leitura"], escrito=total["cache_escrita"],
          custo=f"{custo:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
