#!/usr/bin/env python3
"""Exporta emails reais da caixa, anonimizados, para servirem de casos de teste.

    python exportar.py                    últimos 200 emails
    python exportar.py --quantos 100
    python exportar.py --desde 2026-07-01

**Só lê.** Não escreve, não marca, não move e não apaga nada na caixa de correio.
Usa exclusivamente pedidos GET à Microsoft Graph. Não faz uma única chamada ao
Claude, por isso correr isto não custa nada.

Faz duas coisas de uma vez:

1. Grava os emails anonimizados em `eval/real-AAAA-MM.json`, prontos a serem
   etiquetados à mão e usados pelo eval.py. Esse ficheiro está no .gitignore —
   mesmo anonimizada, aquela é correspondência de clientes e não vai para
   repositório nenhum.

2. Conta a distribuição real dos tipos de email, que é a pergunta que decide se
   este projeto vale a pena: quantos são pedidos sobre estado de encomendas,
   quantos são clientes, quantos são newsletters e notificações.

# Sobre a anonimização

É pseudonimização, não anonimização garantida. Substitui o que se consegue
reconhecer por padrão — endereços, telefones, NIF, IBAN, códigos postais,
números de encomenda — e o nome do remetente onde aparecer no corpo. Um nome
escrito a meio de uma frase pode escapar.

O domínio do remetente é preservado de propósito: é o que a triagem usa para
decidir, e sem ele os casos não testariam nada.

Por isso o ficheiro fica local e fora do git. Trate-o como dados do cliente,
porque é o que é.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import assistente as a

CAMPOS = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,categories,internetMessageHeaders,body"
)

# ─────────────────────────────────────────────────────────────────────────────
# Anonimização
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# Nove dígitos começados por 9 (móvel) ou 2 (fixo), com espaços, pontos ou
# hífenes pelo meio — que é como as pessoas os escrevem. Sem esta tolerância aos
# separadores, "21 234 5678" passava em claro, porque a regra dos números longos
# só apanha dígitos seguidos. Apanha NIF pelo caminho: melhor a mais.
_TELEFONE = re.compile(r"(?:\+351[\s.-]?)?\b[92]\d(?:[\s.-]?\d){7}\b")
_IBAN = re.compile(r"\bPT50\s?(?:\d{4}\s?){5}\d{1}\b", re.I)
_CODIGO_POSTAL = re.compile(r"\b\d{4}-\d{3}\b")
_NIF = re.compile(r"\b[1235689]\d{8}\b")
# Números longos isolados: encomendas, faturas, códigos de seguimento.
_NUMERO_LONGO = re.compile(r"#?\b\d{5,}\b")
_URL_COM_TOKEN = re.compile(r"(https?://[^\s]{0,60})[?#][^\s]{20,}")


def anonimizar(texto: str, nome_remetente: str = "") -> str:
    """Substitui o que é identificável, preservando a estrutura da mensagem."""
    if not texto:
        return ""

    texto = _IBAN.sub("<IBAN>", texto)
    texto = _EMAIL.sub(lambda m: f"<email>@{m.group(1)}", texto)
    texto = _TELEFONE.sub("<TELEFONE>", texto)
    texto = _CODIGO_POSTAL.sub("<COD-POSTAL>", texto)
    texto = _NIF.sub("<NIF>", texto)
    texto = _NUMERO_LONGO.sub("<NUMERO>", texto)
    texto = _URL_COM_TOKEN.sub(r"\1<TOKEN>", texto)

    # O nome do remetente, onde aparecer no corpo — tipicamente na assinatura.
    # Só partes com 3+ letras, para não trocar iniciais por toda a parte.
    for parte in nome_remetente.split():
        if len(parte) >= 3 and parte.isalpha():
            texto = re.sub(rf"\b{re.escape(parte)}\b", "<NOME>", texto, flags=re.I)

    return texto


def anonimizar_endereco(endereco: str) -> str:
    """Mantém o domínio, que é o que a triagem usa. Tapa a parte local."""
    local, _, dominio = endereco.partition("@")
    if not dominio:
        return "<endereco>"
    # Local-parts como "noreply" e "info" são categoria, não identidade: uma
    # newsletter deixa de ser reconhecível se as taparmos.
    generico = {"noreply", "no-reply", "info", "geral", "apoio", "suporte", "vendas",
                "encomendas", "notifications", "newsletter", "hello", "contact",
                "mailer-daemon", "postmaster", "bounce", "bounces", "marketing"}
    if any(g in local.lower() for g in generico):
        return endereco
    return f"<pessoa>@{dominio}"


# ─────────────────────────────────────────────────────────────────────────────
# Palpite de categoria — por palavras-chave, sem modelo e sem custo
# ─────────────────────────────────────────────────────────────────────────────

_ESTADO_ENCOMENDA = (
    "onde está", "onde esta", "ainda não recebi", "ainda nao recebi",
    "ainda não chegou", "ainda nao chegou", "não chegou", "nao chegou",
    "código de seguimento", "codigo de seguimento", "tracking", "rastreio",
    "quando chega", "quando recebo", "estado da encomenda", "estado do pedido",
    "não recebi nada", "nao recebi nada", "sem novidades", "prazo de entrega",
)
_DEVOLUCAO = (
    "devolver", "devolução", "devolucao", "troca", "trocar", "reembolso",
    "garantia", "avariou", "avariado", "defeito", "partido", "danificado",
)
_PRE_VENDA = (
    "têm em stock", "tem em stock", "disponível", "disponivel", "compatível",
    "compativel", "que tamanho", "qual o preço", "qual o preco", "fazem envio",
)


def palpitar(assunto: str, corpo: str) -> str:
    """Palpite grosseiro, só para a contagem. Não substitui a leitura."""
    texto = f"{assunto}\n{corpo}".lower()
    if any(p in texto for p in _ESTADO_ENCOMENDA):
        return "estado-encomenda"
    if any(p in texto for p in _DEVOLUCAO):
        return "devolucao-garantia"
    if any(p in texto for p in _PRE_VENDA):
        return "pre-venda"
    return "outro"


# ─────────────────────────────────────────────────────────────────────────────
# Recolha
# ─────────────────────────────────────────────────────────────────────────────


def recolher(graph: a.Graph, quantos: int, desde: str | None) -> list[dict]:
    """Puxa mensagens da Inbox, da mais recente para a mais antiga."""
    params: dict[str, str] = {
        "$select": CAMPOS,
        "$orderby": "receivedDateTime desc",
        "$top": str(min(quantos, 50)),
    }
    if desde:
        params["$filter"] = f"receivedDateTime ge {desde}T00:00:00Z"

    url = f"{graph.base}/mailFolders/inbox/messages"
    recolhidos: list[dict] = []

    while url and len(recolhidos) < quantos:
        dados = graph._pedir("GET", url, params=params if params else None)
        recolhidos.extend(dados.get("value", []))
        url = dados.get("@odata.nextLink", "")
        params = {}  # o nextLink já traz tudo
        print(f"  ... {len(recolhidos)} recolhidos", flush=True)

    return recolhidos[:quantos]


def converter(bruto: dict, indice: int, cfg: a.Config) -> dict:
    """Transforma a mensagem do Graph num caso anonimizado."""
    de = (bruto.get("from") or {}).get("emailAddress", {})
    endereco = str(de.get("address") or "").lower()
    nome = str(de.get("name") or "")

    corpo = a.cortar_citacao(
        a.para_texto((bruto.get("body") or {}).get("content", ""))
    )
    assunto = str(bruto.get("subject") or "")

    def enderecos(chave: str) -> list[str]:
        lista = bruto.get(chave) or []
        return [
            anonimizar_endereco(str(e.get("emailAddress", {}).get("address", "")).lower())
            for e in lista
            if e.get("emailAddress", {}).get("address")
        ]

    cabecalhos = [
        (str(h.get("name", "")), str(h.get("value", "")))
        for h in bruto.get("internetMessageHeaders") or []
        # Só os que a triagem usa. Os restantes trazem IPs e identificadores.
        if str(h.get("name", "")).lower()
        in {"list-unsubscribe", "list-id", "precedence", "auto-submitted",
            "x-auto-response-suppress", "feedback-id", "x-campaign-id"}
    ]

    msg = {
        "id": str(bruto.get("id", "")),
        "message_id": str(bruto.get("internetMessageId") or bruto.get("id", "")),
        "conversation_id": "",
        "assunto": assunto,
        "de": endereco,
        "nome": nome,
        "para": [str(e.get("emailAddress", {}).get("address", "")).lower()
                 for e in bruto.get("toRecipients") or []],
        "cc": [str(e.get("emailAddress", {}).get("address", "")).lower()
               for e in bruto.get("ccRecipients") or []],
        "recebido": str(bruto.get("receivedDateTime", "")),
        "categorias": list(bruto.get("categories") or []),
        "cabecalhos": cabecalhos,
        "corpo": corpo,
    }

    # A triagem corre sobre os dados reais, antes de anonimizar — o domínio
    # verdadeiro é o que ela lê.
    motivo = a.triar(msg, cfg, a.carregar_blocklist(cfg.blocklist)) or a.triar_cabecalhos(msg)

    return {
        "id": f"real-{indice:03d}",
        "email": {
            "from": anonimizar_endereco(endereco),
            "from_name": "<NOME>" if nome else "",
            "subject": anonimizar(assunto, nome),
            "to": enderecos("toRecipients"),
            "cc": enderecos("ccRecipients"),
            "headers": cabecalhos,
            "body": anonimizar(corpo, nome)[:4000],
        },
        "expect": "",
        "note": "",
        "_triagem": motivo or "",
        "_palpite": palpitar(assunto, corpo),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Relatório
# ─────────────────────────────────────────────────────────────────────────────


def relatar(casos: list[dict]) -> None:
    total = len(casos)
    if not total:
        print("Nenhuma mensagem recolhida.")
        return

    descartados = [c for c in casos if c["_triagem"]]
    sobreviventes = [c for c in casos if not c["_triagem"]]
    palpites = Counter(c["_palpite"] for c in sobreviventes)
    encomendas = palpites.get("estado-encomenda", 0)

    print()
    print("═" * 62)
    print(f"  {total} emails recolhidos")
    print("═" * 62)
    print()
    print(f"  Descartados pela triagem (custo zero)   {len(descartados):>4}  "
          f"{len(descartados) / total:>5.0%}")
    print(f"  Chegariam ao modelo                     {len(sobreviventes):>4}  "
          f"{len(sobreviventes) / total:>5.0%}")
    print()

    if descartados:
        print("  Porque foram descartados:")
        for regra, n in Counter(
            c["_triagem"].split(":")[0] for c in descartados
        ).most_common():
            print(f"    {regra:<28} {n:>4}")
        print()

    if sobreviventes:
        print("  Dos que chegariam ao modelo:")
        for nome, n in palpites.most_common():
            marca = "  <<<" if nome == "estado-encomenda" else ""
            print(f"    {nome:<28} {n:>4}  {n / len(sobreviventes):>5.0%}{marca}")
        print()
        print(f"  ESTADO DE ENCOMENDAS: {encomendas}/{len(sobreviventes)} "
              f"= {encomendas / len(sobreviventes):.0%} dos emails que chegam ao modelo")
        if encomendas / len(sobreviventes) > 0.6:
            print("  ⚠ Acima de 60%: o assistente vai escalar a maioria. Rever o âmbito.")
        elif encomendas / len(sobreviventes) > 0.3:
            print("  ⚠ Entre 30 e 60%: vale a pena discutir ligar ao sistema de encomendas.")
        else:
            print("  ✓ Abaixo de 30%: o assistente responde à maioria.")
        print()

    print("  Domínios mais frequentes:")
    for dominio, n in Counter(
        c["email"]["from"].partition("@")[2] for c in casos
    ).most_common(8):
        print(f"    {dominio:<32} {n:>4}")
    print()

    # A contagem é por palavras-chave, não por leitura. Serve para orientar,
    # não para concluir.
    print("  Nota: a classificação acima é por palavras-chave, não por leitura.")
    print("  Serve para orientar. Os números finais saem da revisão à mão.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exporta emails reais, anonimizados")
    p.add_argument("--quantos", type=int, default=200)
    p.add_argument("--desde", help="data mínima, AAAA-MM-DD")
    p.add_argument("--saida", type=Path, help="ficheiro de destino")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)

    saida = args.saida or Path("eval") / f"real-{datetime.now(timezone.utc):%Y-%m}.json"
    if saida.exists():
        resposta = input(f"{saida} já existe. Substituir? [s/N] ").strip().lower()
        if resposta not in {"s", "sim", "y"}:
            print("Cancelado.")
            return 1

    print(f"A ler {cfg.mailbox} (só leitura, sem custo)...")
    try:
        graph = a.Graph(cfg)
        brutos = recolher(graph, args.quantos, args.desde)
    except Exception as exc:
        print(f"Graph: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    casos = [converter(b, i + 1, cfg) for i, b in enumerate(brutos)]

    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps(casos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    relatar(casos)
    print(f"  Gravado em {saida}")
    print("  Este ficheiro é correspondência do cliente. Fica fora do git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
