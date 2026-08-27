#!/usr/bin/env python3
"""Deteta contradições e ambiguidades na base de conhecimento.

    python verificar_kb.py

Uma chamada só ao Claude, offline e fora do caminho de produção: lê a base
inteira (knowledge/*.md) e pede uma lista estruturada de contradições --
regras que respondem de forma diferente à mesma pergunta, ou secções com
prioridade ambígua entre si.

Não é deteção contínua nem monitorização: corre-se à mão depois de editar a
base, antes do commit -- o mesmo momento em que já se corre o eval. Gasta
uma chamada (~o tamanho da base em tokens de entrada, sem desconto de cache
por ser isolada); ver a documentação para o custo estimado antes de correr
com frequência.

O resultado é uma sugestão para uma pessoa ler, nunca uma correção
automática: só quem confirma com o lojista deve editar knowledge/*.md.
"""

from __future__ import annotations

import argparse
import json
import sys

import anthropic

import assistente as a

ESQUEMA_CONTRADICOES = {
    "type": "object",
    "properties": {
        "contradicoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "documentos": {
                        "type": "string",
                        "description": "os ficheiros e secções envolvidos, "
                                       "ex.: 'devolucoes.md > Higiene' e "
                                       "'provas-e-defeitos.md > Trocas'",
                    },
                    "descricao": {
                        "type": "string",
                        "description": "o que muda na resposta ao cliente "
                                       "consoante qual regra se aplicar",
                    },
                    "gravidade": {"type": "string", "enum": ["baixa", "media", "alta"]},
                },
                "required": ["documentos", "descricao", "gravidade"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["contradicoes"],
    "additionalProperties": False,
}

INSTRUCAO = """És um revisor de qualidade de uma base de conhecimento de apoio ao \
cliente, usada por um assistente de IA para decidir como responder a clientes \
de uma loja online. O teu trabalho é encontrar contradições reais: pontos em \
que duas regras, se ambas se aplicassem ao mesmo caso, levariam o assistente \
a responder de forma diferente consoante qual lesse primeiro ou desse mais \
peso.

Não sinalizes:
- Secções sobre temas diferentes, mesmo que pareçam relacionados.
- Uma regra geral com uma exceção já escrita explicitamente para ela.
- Diferenças de tom, redação ou nível de detalhe.
- Repetição da mesma regra em dois sítios, se disser a mesma coisa.

Sinaliza só o que mudaria a decisão do assistente perante o mesmo email de \
cliente. Se não encontrares nada, devolve uma lista vazia -- inventar uma \
contradição fraca só para ter algo a mostrar é pior do que não encontrar \
nada."""


def analisar_base(cliente: object, cfg: "a.Config", base: str) -> list[dict]:
    """A parte testável sem tocar na rede: recebe um cliente Anthropic (ou um
    dublê) e a base já carregada, devolve a lista de contradições."""
    resposta = cliente.messages.create(  # type: ignore[attr-defined]
        model=cfg.modelo,
        max_tokens=4096,
        thinking={"type": "disabled"},
        system=INSTRUCAO,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_CONTRADICOES}},
        messages=[{"role": "user", "content": f"# BASE DE CONHECIMENTO\n\n{base}"}],
    )
    texto = next(
        (b.text for b in resposta.content if getattr(b, "type", "") == "text"), "{}"
    )
    dados = json.loads(texto)
    return dados.get("contradicoes", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deteta contradições na base de conhecimento")
    parser.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    base = a.carregar_base(cfg.knowledge_dir)
    if not base.strip():
        sys.exit("Base de conhecimento vazia -- nada a verificar.")

    cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=120.0)
    contradicoes = analisar_base(cliente, cfg, base)

    if not contradicoes:
        print("\nNenhuma contradição encontrada.\n")
        return 0

    ordem = {"alta": 0, "media": 1, "baixa": 2}
    contradicoes.sort(key=lambda c: ordem.get(c.get("gravidade", ""), 3))

    print(f"\n{len(contradicoes)} possível(eis) contradição(ões)\n")
    for c in contradicoes:
        print(f"[{c.get('gravidade', '?').upper():<6}] {c.get('documentos', '?')}")
        print(f"          {c.get('descricao', '')}\n")
    print("Sugestão para leitura humana, não uma correção automática -- confirmar "
          "com o lojista antes de mudar seja o que for em knowledge/*.md.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
