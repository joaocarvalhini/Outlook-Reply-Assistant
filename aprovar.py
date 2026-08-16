#!/usr/bin/env python3
"""Executa um cancelamento já preparado pelo dossiê, com aprovação humana.

    python aprovar.py 42            revalida o caso #42 e pede confirmação
    python aprovar.py 42 --forcar   ignora o aviso de já ter sido executado

Só cancelamento, por agora — é o tipo de dossiê com menor risco (reversível
enquanto a encomenda não foi expedida, sem reembolso envolvido). Reembolso e
troca continuam a fazer-se à mão no admin.

O LLM nunca chega a executar nada: prepara o dossiê, isto lê o dossiê. Antes
de tocar na Shopify:

  1. Revalida a encomenda em tempo real — o dossiê pode ter sido preparado
     há horas, e nesse tempo a encomenda pode ter sido expedida ou já
     cancelada por outra via. Se algo mudou, recusa.
  2. Pede para escrever o número da encomenda de volta, não um Enter — para
     não aprovar por engano o caso errado.
  3. Em DRY_RUN=true (omissão), simula: regista tudo, não chama a Shopify.

Cada tentativa fica em `acoes`, sucedida ou não. Correr contra um caso já
executado com sucesso avisa e para, a menos que --forcar seja explícito.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import textwrap

import assistente as a

LARGURA = 72


def _quebrar(texto: str, indent: str = "  ") -> str:
    return "\n".join(
        indent + l
        for l in textwrap.wrap(str(texto or "").strip(), LARGURA - len(indent))
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aprova e executa um cancelamento")
    p.add_argument("caso", type=int, help="número do caso, visto em dossie.py")
    p.add_argument("--forcar", action="store_true",
                    help="continua mesmo com uma execução com sucesso já registada")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(None)  # respeita DRY_RUN do .env, não força True
    con = sqlite3.connect(cfg.db)

    linha = con.execute(
        "SELECT assunto, acao, dossie_tipo, dossie_resumo, dossie_validacao, "
        "       dossie_accao, dossie_risco, dossie_resposta, dossie_link "
        "FROM processados WHERE rowid = ?",
        (args.caso,),
    ).fetchone()
    if linha is None:
        sys.exit(f"Caso #{args.caso} não existe no registo.")

    (assunto, acao, tipo, resumo, validacao, accao_recom, risco, resposta,
     link) = linha

    if acao != "escalar" or tipo != "cancelamento":
        sys.exit(
            f"Caso #{args.caso} é {acao}/{tipo or '(sem dossiê)'}, não um "
            "cancelamento à espera de aprovação. Só este tipo está automatizado."
        )

    anteriores = a.acoes_do_caso(con, args.caso)
    sucesso_anterior = next((h for h in anteriores if h["resultado"] == "sucesso"), None)
    if sucesso_anterior and not args.forcar:
        sys.exit(
            f"Caso #{args.caso} já foi executado com sucesso em "
            f"{sucesso_anterior['em']}. Usa --forcar se sabes que precisa de "
            "correr outra vez."
        )

    encomenda_id = link.rsplit("/", 1)[-1] if link else ""
    if not encomenda_id.isdigit():
        sys.exit(f"Caso #{args.caso} não tem um link de encomenda válido: {link!r}")

    print(f"\n{'━' * LARGURA}\nCASO #{args.caso}   ·   CANCELAMENTO   ·   risco {risco or '?'}\n{'━' * LARGURA}")
    print(f"\n{assunto}\n")
    print(_quebrar(resumo))
    if accao_recom:
        print("\n  Ação recomendada")
        print(_quebrar(accao_recom, "    "))
    print(f"\n  Link no admin: {link}")

    shopify = a.Shopify(cfg)
    try:
        encomenda = shopify.por_id(encomenda_id)
    except Exception as exc:
        sys.exit(f"\nErro a revalidar na Shopify: {type(exc).__name__}: {exc}")

    if encomenda is None:
        sys.exit(f"\nA encomenda {encomenda_id} já não existe na Shopify. Recusado.")
    if encomenda.get("cancelled_at"):
        sys.exit(f"\nJá está cancelada desde {encomenda['cancelled_at'][:10]}. Nada a fazer.")
    estado_envio = encomenda.get("fulfillment_status")
    if estado_envio not in (None, "unfulfilled"):
        sys.exit(
            f"\nA encomenda já está '{estado_envio}' — não é seguro cancelar "
            "automaticamente uma encomenda expedida. Recusado."
        )

    numero = str(encomenda.get("name", "")).lstrip("#")
    print(f"\n  Revalidado agora: encomenda {encomenda.get('name')}, ainda não "
          f"expedida, ainda não cancelada.")

    print(f"\nPara confirmar o cancelamento, escreve o número da encomenda ({numero}): ", end="")
    resposta_utilizador = input().strip().lstrip("#")
    if resposta_utilizador != numero:
        sys.exit("\nNúmero não corresponde. Cancelado, nada foi executado.")

    if cfg.dry_run:
        a.gravar_acao(con, args.caso, "cancelamento", encomenda_id, "simulado",
                      "DRY_RUN=true, nenhuma chamada de escrita feita")
        print("\nDRY_RUN ligado: simulado e registado, a Shopify não foi tocada.")
        return 0

    try:
        shopify.cancelar(encomenda_id)
    except Exception as exc:
        detalhe = f"{type(exc).__name__}: {exc}"
        a.gravar_acao(con, args.caso, "cancelamento", encomenda_id, "erro", detalhe)
        sys.exit(f"\nFalhou a cancelar na Shopify: {detalhe}")

    a.gravar_acao(con, args.caso, "cancelamento", encomenda_id, "sucesso",
                  f"encomenda {numero} cancelada")
    print(f"\nEncomenda {numero} cancelada na Shopify.")
    if "pagamento" in (validacao or "").lower() or "capturado" in (validacao or "").lower():
        print("Nota do dossiê: pode haver pagamento a devolver — isso não é "
              "feito por este comando, é uma decisão separada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
