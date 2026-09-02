#!/usr/bin/env python3
"""O que aprender com as edições do lojista, sem inventar regras.

    python aprender.py                  divergências por rever, agrupadas
    python aprender.py --tudo           inclui as já revistas
    python aprender.py --marcar <id>    marca uma como tratada
    python aprender.py --classificar    + 1 chamada: falta regra ou é saliência?
    python aprender.py --perguntar 3    compõe a mensagem a enviar ao lojista
    python aprender.py --perguntar 3 --enviar   manda-a e marca os casos

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
import os
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

ESQUEMA_MENSAGEM = {
    "type": "object",
    "properties": {"mensagem": {"type": "string"}},
    "required": ["mensagem"],
    "additionalProperties": False,
}

INSTRUCAO_PERGUNTAR = """Vais escrever uma mensagem curta para o dono de uma loja online, a perguntar-lhe porque alterou algumas respostas que um assistente automático tinha preparado para clientes dele.

O objetivo é aprender a regra que está por trás de cada alteração, para o assistente deixar de a repetir. Escreve em português de Portugal, tratando-o por tu, como quem trabalha com ele todos os dias e não como um formulário. (Dizia "segunda pessoa formal" e o modelo tratava-o por tu à mesma; o que interessa numa mensagem diária é o registo ser sempre o mesmo, e o informal é o que corresponde à relação real.)

Regras da mensagem:

- Uma abertura de uma frase a dizer o que é. Nada de agradecimentos longos.
- Um bloco por caso, numerado. Em cada um: o que o cliente perguntou, em meia frase; o que o assistente tinha escrito, resumido; e o que ele enviou em vez disso, também resumido. Depois a pergunta.
- **Pergunta o PORQUÊ, nunca proponhas a resposta.** Sugerir a regra enviesa-o a concordar, e uma regra escrita a partir de uma concordância educada é pior do que nenhuma regra.
- Se um caso se repetiu, di-lo — é o que o faz perceber que vale a pena responder.
- Fecha a dizer que, se algum tiver sido uma decisão pontual e não uma regra, basta ele dizer isso.
- Sem jargão. Ele não sabe o que é um prompt, um modelo ou uma escalação.
- No máximo, uma página."""


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


def compor_pergunta(cliente: object, cfg: "a.Config", casos: list[dict]) -> str:
    """A mensagem a enviar ao lojista. Parte testável, sem tocar na rede.

    O modelo compõe a pergunta, nunca a resposta: o pior resultado possível é
    uma pergunta mal escrita, que se corrige antes de enviar. É por isso que
    esta chamada é segura e a de escrever regras não seria.
    """
    if not casos:
        return ""
    blocos = []
    for i, caso in enumerate(casos, 1):
        blocos.append(
            f"[{i}] visto {caso.get('vezes', 1)}x · assunto: {caso['assunto']}\n"
            f"O CLIENTE PERGUNTOU:\n{caso.get('pergunta', '(não disponível)')[:700]}\n\n"
            f"O ASSISTENTE ESCREVEU:\n{caso['escrito'][:700]}\n\n"
            f"ELE ENVIOU:\n{caso['enviado'][:700]}"
        )
    resposta = cliente.messages.create(  # type: ignore[attr-defined]
        model=cfg.modelo,
        max_tokens=2048,
        thinking={"type": "disabled"},
        system=INSTRUCAO_PERGUNTAR,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_MENSAGEM}},
        messages=[{"role": "user", "content": "\n\n---\n\n".join(blocos)}],
    )
    texto = next(
        (b.text for b in resposta.content if getattr(b, "type", "") == "text"), "{}"
    )
    return json.loads(texto).get("mensagem", "")


def rodape_links(casos: list[dict]) -> str:
    """Os links para os emails, acrescentados pelo código e não pelo modelo.

    Um webLink do Outlook tem centenas de caracteres. Um modelo a copiá-lo
    engana-se, e um link partido é pior do que link nenhum: manda o lojista
    procurar o email à mão, que é exatamente o trabalho que isto lhe poupa.

    O link vai mascarado atrás do assunto porque um webLink do Outlook tem
    cerca de 320 caracteres: três em cru somavam mais do que as perguntas
    todas e empurravam a mensagem para lá dos 2000 do Discord, partindo-a em
    duas -- a segunda só com a cauda dos links.
    """
    linhas = []
    for i, caso in enumerate(casos, 1):
        if not caso.get("link"):
            continue
        # Parênteses retos no assunto partiam a sintaxe do link mascarado.
        rotulo = (caso.get("assunto") or "").translate(
            {ord("["): None, ord("]"): None}).strip()[:60]
        linhas.append(f"{i}. [{rotulo or 'abrir o email'}]({caso['link']})")
    return "\n\nAbrir os emails:\n" + "\n".join(linhas) if linhas else ""


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
            # O par completo, para o --perguntar poder mostrar ao lojista o
            # que o assistente escreveu e o que ele enviou, lado a lado.
            "escrito": corpo, "enviado": real,
        })
    return casos


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="O que aprender com as edições do lojista")
    p.add_argument("--tudo", action="store_true", help="inclui as já revistas")
    p.add_argument("--marcar", metavar="MESSAGE_ID", help="marca uma como tratada")
    p.add_argument("--perguntar", type=int, metavar="N", default=0,
                   help="compõe a mensagem a enviar ao lojista, com os N "
                        "padrões mais vistos (sugestão: 5)")
    p.add_argument("--enviar", action="store_true",
                   help="além de compor, manda pelo PERGUNTAS_WEBHOOK_URL")
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

    if args.perguntar:
        # A correr todos os dias, o caso normal é não haver nada. Sair aqui
        # poupa a chamada ao modelo e, sobretudo, não manda uma mensagem vazia.
        if not grupos:
            print("Nada por perguntar.")
            return 0
        graph = a.Graph(cfg)
        cliente = anthropic.Anthropic(api_key=cfg.api_key, timeout=60.0)
        escolhidos = []
        for grupo in grupos[:args.perguntar]:
            caso = dict(grupo[0])
            caso["vezes"] = len(grupo)
            # A pergunta do cliente não fica no registo (só a resposta), por
            # isso vai-se buscar ao Graph. Sem ela, o lojista teria de abrir
            # cada caso para perceber do que se trata -- que é exatamente o
            # trabalho que isto existe para lhe poupar.
            msg = buscar_email(graph, caso["message_id"])
            caso["pergunta"] = ""
            caso["link"] = ""
            if msg is not None:
                caso["link"] = msg.get("link", "")
                try:
                    caso["pergunta"] = graph.detalhe(msg, cfg.max_body)["corpo"]
                except Exception:
                    pass
            escolhidos.append(caso)

        mensagem = compor_pergunta(cliente, cfg, escolhidos)
        if not mensagem.strip():
            print("O modelo não devolveu mensagem nenhuma. Nada enviado.")
            return 1
        mensagem += rodape_links(escolhidos)
        print(mensagem)
        print("\n" + "─" * 72)

        if args.enviar:
            url = os.environ.get("PERGUNTAS_WEBHOOK_URL", "").strip()
            if not url:
                print("PERGUNTAS_WEBHOOK_URL não está no .env — nada enviado.")
                print("A mensagem acima continua boa para copiar à mão.")
                return 1
            try:
                a.enviar_webhook(url, mensagem)
            except Exception as exc:  # noqa: BLE001
                # Falhar a enviar não deve perder a mensagem: ela já foi
                # impressa acima e pode seguir à mão. E não se marca nada --
                # o que não chegou tem de voltar a ser perguntado amanhã.
                print(f"Falhou o envio ({type(exc).__name__}: {exc}). "
                      "A mensagem acima continua boa para copiar.")
                return 1

            # Marcar só depois de a mensagem sair, e sempre. A correr todas as
            # noites sem ninguém a ver, não marcar significa reenviar os mesmos
            # casos indefinidamente -- e uma mensagem que se repete deixa de
            # se ler, que é o contrário do que isto existe para fazer.
            #
            # O custo é perder a pergunta se ele não responder. É aceitável e
            # até saudável: se o padrão importar, ele volta a editar da mesma
            # maneira, aparece nova divergência e a pergunta refaz-se sozinha.
            # Um padrão que nunca reaparece não valia a pergunta.
            marcados = sum(
                marcar_revisto(con, caso["message_id"])
                for grupo in grupos[:args.perguntar]
                for caso in grupo
            )
            a.log("perguntas-enviadas",
                  padroes=len(escolhidos), marcados=marcados)
            print(f"Enviado. {marcados} caso(s) marcado(s) como revisto(s).")
            return 0

        print("Depois de ele responder, marcar os casos:")
        for grupo in grupos[:args.perguntar]:
            for caso in grupo:
                print(f"  python aprender.py --marcar {caso['message_id']}")
        print()
        return 0

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
