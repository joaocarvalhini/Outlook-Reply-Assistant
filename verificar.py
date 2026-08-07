#!/usr/bin/env python3
"""Verificação prévia — correr no dia da instalação, antes de ligar seja o que for.

    python verificar.py
    python verificar.py --outra-caixa geral@tripat3s.com

Confirma, por esta ordem, o que tem de estar bem para o assistente poder correr:
configuração, base de conhecimento, chave da Anthropic, autenticação no Graph,
leitura da caixa alvo e — o mais importante — que a aplicação **não** consegue ler
mais nenhuma caixa do inquilino.

Esse último ponto é a razão de este ficheiro existir. `Mail.ReadWrite` como
permissão de aplicação dá acesso a todas as caixas da empresa; é o
`New-ApplicationAccessPolicy` que o limita a uma. É o passo mais importante do
projeto e o mais fácil de esquecer, e um aviso no README não é um travão. Aqui é.

Sai com código 1 se alguma verificação obrigatória falhar, para poder ser usado
como porta de entrada num script de instalação.
"""

from __future__ import annotations

import argparse
import sys

import httpx

import assistente as a

OK, FALHA, AVISO = "  OK  ", " FALHA", " AVISO"


class Relatorio:
    def __init__(self) -> None:
        self.falhas = 0
        self.avisos = 0

    def ok(self, titulo: str, detalhe: str = "") -> None:
        print(f"[{OK}] {titulo}" + (f" — {detalhe}" if detalhe else ""))

    def falha(self, titulo: str, detalhe: str) -> None:
        self.falhas += 1
        print(f"[{FALHA}] {titulo} — {detalhe}")

    def aviso(self, titulo: str, detalhe: str) -> None:
        self.avisos += 1
        print(f"[{AVISO}] {titulo} — {detalhe}")


def verificar_config(r: Relatorio) -> a.Config | None:
    try:
        cfg = a.carregar_config(True)
    except SystemExit as exc:
        r.falha("Configuração", str(exc))
        return None
    r.ok("Configuração", f"caixa {cfg.mailbox}, modelo {cfg.modelo}")
    if not cfg.dry_run:
        r.aviso("DRY_RUN", "está desligado: uma execução vai escrever na caixa")
    return cfg


def verificar_base(r: Relatorio, cfg: a.Config) -> None:
    try:
        base = a.carregar_base(cfg.knowledge_dir)
    except SystemExit as exc:
        r.falha("Base de conhecimento", str(exc))
        return
    tokens = len(base) // 4
    r.ok("Base de conhecimento", f"{len(base)} caracteres, ~{tokens} tokens")
    # O cache só pega a partir de um prefixo mínimo, que varia por modelo.
    minimo = 4096 if "haiku" in cfg.modelo else 1024
    if tokens < minimo:
        r.aviso(
            "Cache do prompt",
            f"~{tokens} tokens abaixo do mínimo de {minimo} do {cfg.modelo}: "
            "cada email paga o prompt inteiro",
        )


def verificar_anthropic(r: Relatorio, cfg: a.Config) -> None:
    try:
        import anthropic

        cliente = anthropic.Anthropic(api_key=cfg.api_key)
        cliente.messages.create(
            model=cfg.modelo,
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as exc:
        r.falha("Chave da Anthropic", f"{type(exc).__name__}: {str(exc)[:160]}")
        return
    r.ok("Chave da Anthropic", f"{cfg.modelo} respondeu")


def verificar_graph(r: Relatorio, cfg: a.Config, outra: str | None) -> None:
    try:
        # O construtor do MSAL faz descoberta do tenant e falha aqui, não na
        # primeira chamada — um tenant errado rebenta antes de haver pedido.
        graph = a.Graph(cfg)
    except Exception as exc:
        r.falha("Ligação ao Graph", f"{type(exc).__name__}: {str(exc)[:160]}")
        return

    try:
        graph._token()
    except SystemExit as exc:
        r.falha("Autenticação no Graph", str(exc))
        return
    except Exception as exc:
        r.falha("Autenticação no Graph", f"{type(exc).__name__}: {exc}")
        return
    r.ok("Autenticação no Graph", "token obtido")

    try:
        graph._pedir(
            "GET",
            f"{graph.base}/mailFolders/inbox/messages",
            params={"$select": "id", "$top": "1"},
        )
    except Exception as exc:
        r.falha("Leitura da caixa alvo", f"{type(exc).__name__}: {str(exc)[:160]}")
        return
    r.ok("Leitura da caixa alvo", cfg.mailbox)

    verificar_restricao(r, cfg, graph, outra)


def verificar_restricao(
    r: Relatorio, cfg: a.Config, graph: a.Graph, outra: str | None
) -> None:
    """A aplicação tem de falhar a ler qualquer outra caixa. É o ponto crítico."""
    if not outra:
        r.aviso(
            "Restrição a uma caixa",
            "não verificada: correr outra vez com --outra-caixa <endereço real "
            "do inquilino> para confirmar o New-ApplicationAccessPolicy",
        )
        return

    if outra.lower() == cfg.mailbox:
        r.aviso("Restrição a uma caixa", "--outra-caixa é a própria caixa alvo")
        return

    url = f"{a.GRAPH}/users/{outra}/mailFolders/inbox/messages"
    try:
        graph._pedir("GET", url, params={"$select": "id", "$top": "1"})
    except RuntimeError as exc:
        texto = str(exc)
        if "403" in texto:
            r.ok("Restrição a uma caixa", f"acesso a {outra} negado, como deve ser")
        elif "404" in texto:
            r.aviso(
                "Restrição a uma caixa",
                f"{outra} devolveu 404 — a caixa pode não existir, o que não prova "
                "nada. Usar um endereço real do inquilino",
            )
        else:
            r.aviso("Restrição a uma caixa", f"resposta inesperada: {texto[:120]}")
        return
    except httpx.HTTPError as exc:
        r.aviso("Restrição a uma caixa", f"{type(exc).__name__}: {exc}")
        return

    r.falha(
        "Restrição a uma caixa",
        f"a aplicação LEU {outra}. A política de acesso não está a restringir — "
        "correr New-ApplicationAccessPolicy antes de continuar",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verificação prévia à instalação")
    p.add_argument(
        "--outra-caixa",
        help="endereço real de outra caixa do inquilino, para provar a restrição",
    )
    args = p.parse_args(argv)

    a.saida_utf8()
    print("Verificação prévia\n")

    r = Relatorio()
    cfg = verificar_config(r)
    if cfg is not None:
        verificar_base(r, cfg)
        verificar_anthropic(r, cfg)
        verificar_graph(r, cfg, args.outra_caixa)

    print()
    if r.falhas:
        print(f"{r.falhas} verificação(ões) falharam. Não ligar o assistente.")
    elif r.avisos:
        print(f"Tudo passou, com {r.avisos} aviso(s) a rever acima.")
    else:
        print("Tudo passou.")

    print(
        "\nO SPF e o DKIM não são verificáveis daqui. No DNS do domínio:\n"
        f"  nslookup -type=txt {cfg.mailbox.partition('@')[2] if cfg else 'dominio.pt'}\n"
        f"  nslookup -type=cname selector1._domainkey."
        f"{cfg.mailbox.partition('@')[2] if cfg else 'dominio.pt'}"
    )
    return 1 if r.falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
