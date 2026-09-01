#!/usr/bin/env python3
"""O que aprender com as edições do lojista, sem inventar regras.

    python aprender.py                  divergências por rever, agrupadas
    python aprender.py --tudo           inclui as já revistas
    python aprender.py --marcar <id>    marca uma como tratada
    python aprender.py --classificar    + 1 chamada: falta regra ou é saliência?

Porque existe
-------------
Cada vez que o lojista edita um rascunho antes de o enviar, escreveu um
requisito. O `medir_deriva.py --comparar-gravado` encontra essas divergências,
mas não se lembra de nada: as mesmas aparecem em todas as corridas, muito
depois de já terem virado regra. Ao fim de uma semana, a lista útil está
enterrada em casos já tratados.

Isto resolve três coisas, e **deliberadamente não resolve uma quarta**.

O que faz:

1. **Lembra-se.** Uma marca por caso (`revisto_em`), para a lista mostrar só o
   que falta olhar.
2. **Agrupa.** Um padrão visto uma vez é ruído; visto três vezes é sinal.
   Agrupa pelo *texto que o lojista acrescentou*, não pelo email, para apanhar
   o mesmo padrão em clientes diferentes.
3. **Distingue dois problemas opostos.** Com `--classificar`, uma chamada ao
   modelo pergunta, para cada grupo: o que o lojista escreveu **já está na
   base**? Se sim, não falta regra nenhuma -- é saliência, e a correção é
   *menos* texto, não mais. Se não, é lacuna. São problemas diferentes com
   soluções contrárias, e confundi-los faz a base crescer sem melhorar.

O que **não** faz, de propósito: escrever regras. Uma edição não é uma regra.
Visto três vezes em produção no mesmo dia (01/09/2026):

- Um caso parecia erro de regra; ao ler o fio inteiro, a recusa do cliente era
  um mal-entendido.
- Outro foi mal lido à primeira: parecia "nunca dar o link de rastreio", era
  "responder só ao que foi perguntado".
- Um terceiro não tinha regra em falta nenhuma -- a regra existia e o modelo
  não a aplicou.

Nos três, o salto de "edição" para "regra" exigiu ler o contexto e perguntar ao
lojista. Automatizá-lo produziria regras erradas com confiança, que é
exatamente o que a arquitetura toda existe para evitar. Esta ferramenta prepara
a pergunta; a resposta continua a vir de uma pessoa.

Uma limitação a ter presente
----------------------------
O `--classificar` compara com a base de **hoje**, não com a que existia quando
o email foi respondido. Uma edição que virou regra na semana passada aparece
como "saliência", porque a regra existe agora -- mas na altura não existia, e o
assistente não tinha como a aplicar.

Na primeira corrida real (01/09/2026) isso deu 5 de 6 grupos classificados como
saliência, quase todos por esse motivo. A ferramenta é para olhar para a frente:
marcar o que já foi tratado é o que a torna útil, e é por isso que o --marcar
existe. Um veredito de "saliência" num caso posterior à regra é que é sinal a
sério.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from difflib import SequenceMatcher

import anthropic

import assistente as a
from medir_deriva import buscar_email, resposta_real, semelhanca

# Acima disto, a diferença é cosmética e não vale uma pergunta ao lojista.
LIMIAR_DIVERGENCIA = 90.0

# Dois acrescentos com esta semelhança contam como o mesmo padrão.
LIMIAR_MESMO_PADRAO = 0.55

# Abaixo disto, o lojista não acrescentou nada -- reescreveu. Um diff de uma
# reescrita devolve fragmentos sem sentido ("e já foi expedida, porém a dpd",
# "nos atualizou com o"), por isso nesses casos mostra-se o texto novo inteiro:
# é ele o sinal, não a diferença.
#
# 60 e não 40, calibrado sobre as divergências reais de 01/09/2026 (87, 78, 71,
# 70, 52, 48, 47, 47, 46, 46, 39, 34, 29, 27, 17%): a 40 ainda saíam fragmentos
# de um caso a 46%. Acima de 60 o lojista manteve a resposta e acrescentou algo,
# e aí o diff é limpo.
LIMIAR_REESCRITA = 60.0

CHAVE_REVISTO = "revisto_em"

ESQUEMA_CLASSIFICACAO = {
    "type": "object",
    "properties": {
        "grupos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer"},
                    "veredito": {"type": "string"},
                    "onde": {"type": "string"},
                    "porque": {"type": "string"},
                },
                "required": ["indice", "veredito", "porque"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["grupos"],
    "additionalProperties": False,
}

INSTRUCAO = """És um revisor de uma base de conhecimento de apoio ao cliente, \
usada por um assistente de IA. Vais receber a base inteira e uma lista de \
textos que uma pessoa acrescentou, à mão, a respostas que o assistente tinha \
escrito.

Para cada um, decide entre dois vereditos, e só estes:

- "lacuna" — o que a pessoa escreveu NÃO está na base. Falta lá o facto ou a \
regra, e é preciso confirmá-la com o lojista antes de a escrever.
- "saliencia" — o que a pessoa escreveu JÁ ESTÁ na base, e o assistente não o \
aplicou. Não falta regra nenhuma; a regra existe e perdeu-se no meio do \
documento.

A distinção é o objetivo todo deste trabalho, porque as correções são \
opostas: uma lacuna resolve-se a escrever mais, uma falha de saliência \
resolve-se a escrever menos, ou a arrumar melhor. Confundi-las faz a base \
crescer sem melhorar.

Quando o veredito for "saliencia", diz em "onde" a secção da base onde a regra \
já está, com palavras suficientes para se encontrar. Quando for "lacuna", \
deixa "onde" vazio.

Em "porque", uma frase. Na dúvida genuína entre os dois, escolhe "lacuna": \
mandar alguém confirmar um facto com o lojista custa um minuto; assumir que \
uma regra existe quando não existe deixa o problema por resolver."""


def texto_acrescentado(original: str, final: str) -> str:
    """O que a pessoa pôs no texto e não estava no do assistente.

    Só os blocos acrescentados, não os alterados: interessa o que ela achou
    que faltava, não a reescrita de uma frase que já lá estava.
    """
    matcher = SequenceMatcher(None, original.split(), final.split())
    novos = [
        " ".join(final.split()[j1:j2])
        for etiqueta, _i1, _i2, j1, j2 in matcher.get_opcodes()
        if etiqueta in ("insert", "replace")
    ]
    return "\n".join(t for t in novos if t.strip())


def _normalizar(texto: str) -> str:
    """Sem pontuação nem maiúsculas, para dois acrescentos parecidos baterem
    certo mesmo com nomes de clientes diferentes pelo meio."""
    return re.sub(r"[^\wàáâãéêíóôõúç ]+", " ", texto.lower()).strip()


def mesmo_padrao(a_: str, b_: str) -> bool:
    return SequenceMatcher(None, _normalizar(a_), _normalizar(b_)).ratio() >= LIMIAR_MESMO_PADRAO


def agrupar(casos: list[dict]) -> list[list[dict]]:
    """Junta os casos cujo texto acrescentado diz o mesmo.

    Um padrão visto uma vez pode ser uma decisão pontual daquele cliente; visto
    três vezes é uma regra por escrever. Sem isto, uma lista de quinze
    divergências não diz por onde começar.
    """
    grupos: list[list[dict]] = []
    for caso in casos:
        for grupo in grupos:
            if mesmo_padrao(grupo[0]["acrescentado"], caso["acrescentado"]):
                grupo.append(caso)
                break
        else:
            grupos.append([caso])
    return sorted(grupos, key=len, reverse=True)


def classificar(cliente: object, cfg: "a.Config", base: str,
                grupos: list[list[dict]]) -> list[dict]:
    """Uma chamada para todos os grupos. Parte testável, sem tocar na rede."""
    if not grupos:
        return []
    listagem = "\n\n".join(
        f"[{i}] (visto {len(g)}x)\n{g[0]['acrescentado'][:600]}"
        for i, g in enumerate(grupos)
    )
    resposta = cliente.messages.create(  # type: ignore[attr-defined]
        model=cfg.modelo,
        max_tokens=4096,
        thinking={"type": "disabled"},
        system=INSTRUCAO,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_CLASSIFICACAO}},
        messages=[{"role": "user", "content":
                   f"# BASE DE CONHECIMENTO\n\n{base}\n\n"
                   f"# TEXTOS ACRESCENTADOS À MÃO\n\n{listagem}"}],
    )
    texto = next(
        (b.text for b in resposta.content if getattr(b, "type", "") == "text"), "{}"
    )
    return json.loads(texto).get("grupos", [])


def marcar_revisto(con: sqlite3.Connection, message_id: str) -> bool:
    cur = con.execute(
        f"UPDATE processados SET {CHAVE_REVISTO} = ? WHERE message_id = ?",
        (a.agora(), message_id),
    )
    con.commit()
    return cur.rowcount > 0


def recolher(graph: "a.Graph", cfg: "a.Config", con: sqlite3.Connection,
             tudo: bool) -> list[dict]:
    """As divergências entre o que o assistente escreveu e o que saiu."""
    condicao = "" if tudo else f" AND COALESCE({CHAVE_REVISTO}, '') = ''"
    linhas = con.execute(
        "SELECT message_id, assunto, acao, corpo FROM processados "
        f"WHERE corpo != '' AND conversation_id != ''{condicao} ORDER BY em DESC"
    ).fetchall()

    casos = []
    for message_id, assunto, acao, corpo in linhas:
        msg = buscar_email(graph, message_id)
        if msg is None:
            continue
        msg["_caixa"] = cfg.mailbox
        try:
            real = resposta_real(graph, msg, cfg.aviso)
        except Exception:
            continue
        if not real:
            continue
        sem = semelhanca(corpo, real)
        if sem >= LIMIAR_DIVERGENCIA:
            continue
        # Numa reescrita quase total, o diff não diz nada -- o texto novo é
        # que é o requisito.
        acrescentado = (real if sem < LIMIAR_REESCRITA
                        else texto_acrescentado(corpo, real))
        if not acrescentado.strip():
            continue
        casos.append({
            "message_id": message_id, "assunto": assunto, "acao": acao,
            "semelhanca": sem, "acrescentado": acrescentado,
        })
    return casos


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="O que aprender com as edições do lojista")
    p.add_argument("--tudo", action="store_true", help="inclui as já revistas")
    p.add_argument("--marcar", metavar="MESSAGE_ID", help="marca uma como tratada")
    p.add_argument("--classificar", action="store_true",
                   help="+1 chamada ao modelo: falta regra ou é saliência?")
    args = p.parse_args(argv)

    a.saida_utf8()
    cfg = a.carregar_config(True)
    # abrir_db() e não sqlite3.connect(): é ele que cria a coluna revisto_em
    # numa base que ainda não a tenha. Sem isto, a ferramenta rebenta em
    # qualquer instalação onde o assistente ainda não correu desde a migração.
    con = a.abrir_db(cfg.db)

    if args.marcar:
        if marcar_revisto(con, args.marcar):
            print(f"Marcada como revista: {args.marcar}")
            return 0
        print(f"Não encontrei nenhum registo com esse message_id: {args.marcar}")
        return 1

    casos = recolher(a.Graph(cfg), cfg, con, args.tudo)
    if not casos:
        print("\nNada por rever: ou não há divergências, ou já foram todas "
              "tratadas. Correr com --tudo para ver as antigas.\n")
        return 0

    grupos = agrupar(casos)
    vereditos: dict[int, dict] = {}
    if args.classificar:
        cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
        base = a.carregar_base(cfg.knowledge_dir)
        vereditos = {v["indice"]: v for v in classificar(cliente, cfg, base, grupos)}

    print(f"\n{len(casos)} divergência(s) por rever, em {len(grupos)} padrão(ões)\n")
    for i, grupo in enumerate(grupos):
        marca = ""
        if i in vereditos:
            v = vereditos[i]
            etiqueta = "LACUNA" if v["veredito"] == "lacuna" else "SALIÊNCIA"
            marca = f"  [{etiqueta}]"
        print("─" * 72)
        print(f"[{i}] visto {len(grupo)}x{marca}")
        if i in vereditos:
            v = vereditos[i]
            print(f"     {v['porque']}")
            if v.get("onde"):
                print(f"     já está em: {v['onde']}")
        print(f"\n     o lojista acrescentou:")
        for linha in grupo[0]["acrescentado"].splitlines()[:6]:
            print(f"       {linha[:80]}")
        print(f"\n     casos:")
        for caso in grupo[:4]:
            print(f"       {caso['semelhanca']:3.0f}%  {caso['assunto'][:46]}")
            print(f"             {caso['message_id']}")
        if len(grupo) > 4:
            print(f"       (+{len(grupo) - 4} outros)")
        print()

    print("─" * 72)
    print("Depois de tratar um padrão, marcar cada caso dele:")
    print("  python aprender.py --marcar <message_id>")
    if not args.classificar:
        print("\nPara saber se falta regra ou se a regra já existe e não foi")
        print("aplicada:  python aprender.py --classificar  (1 chamada, cêntimos)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
