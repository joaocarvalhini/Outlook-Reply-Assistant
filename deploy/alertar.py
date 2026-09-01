#!/usr/bin/env python3
"""Avisa quando a passagem falhou. Chamado pelo systemd via OnFailure=, nunca
à mão -- ver Finding M-6: hoje uma falha repetida só se descobre a olhar
para o journalctl.

Funciona em duas camadas:

1. Escreve sempre para o stderr (vai para o journal, com o carimbo de erro
   do próprio systemd) -- não depende de nada estar configurado.
2. Se ALERTA_WEBHOOK_URL estiver definido no .env, envia também um POST com
   o texto em bruto no corpo. É o formato que https://ntfy.sh espera (grátis,
   sem conta, sem chave: basta escolher um nome de tópico e assinar no
   telemóvel ou no browser) -- mas serve qualquer endpoint que aceite um
   POST de texto simples.

Sem ALERTA_WEBHOOK_URL, o assistente continua a funcionar exatamente como
antes: só fica mais visível no journal. Configurar o webhook é uma decisão
de operação (que canal o lojista/a equipa vigia), por isso fica de fora até
alguém a tomar -- não vem escolhido à partida.
"""

from __future__ import annotations

import os
import subprocess
import sys

import httpx
from dotenv import load_dotenv


def ultimas_linhas(unidade: str, n: int = 15) -> str:
    """Best-effort: se o journalctl não estiver disponível ou falhar, o alerta
    sai na mesma -- sem contexto é pior do que sem alerta nenhum, mas não é
    motivo para este próprio script falhar."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", unidade, "-n", str(n), "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "(journalctl não devolveu linhas)"
    except Exception as exc:
        return f"(não foi possível ler o journal: {type(exc).__name__}: {exc})"


def main() -> int:
    load_dotenv()
    unidade = os.environ.get("SERVICO_A_VIGIAR", "tripat3s-assistente.service")
    contexto = ultimas_linhas(unidade)

    mensagem = f"[assistente] {unidade} falhou.\n\n{contexto}"
    print(mensagem, file=sys.stderr)

    url = os.environ.get("ALERTA_WEBHOOK_URL", "").strip()
    if not url:
        return 0

    # O Discord recusa um corpo em texto cru: exige JSON com um campo
    # "content". Deteta-se pelo URL para manter compatibilidade com qualquer
    # outro recetor que aceite texto. A mesma lógica está em
    # assistente.enviar_webhook() -- duplicada de propósito, porque este
    # ficheiro corre quando o assistente FALHA e não pode depender dele.
    discord = "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url
    try:
        if discord:
            httpx.post(url, json={"content": mensagem[:2000]}, timeout=10.0)
        else:
            httpx.post(url, content=mensagem[:2000].encode("utf-8"), timeout=10.0)
    except Exception as exc:
        # Se a rede estiver em baixo, é normal isto falhar também -- o
        # registo no journal (acima) já ficou feito, e é isso que garante
        # que a falha nunca desaparece em silêncio.
        print(f"[assistente] falhou a enviar o alerta: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
