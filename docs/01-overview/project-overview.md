---
title: Visão geral do projeto
type: overview
status: implemented
tags:
  - overview
  - production
---

# Visão geral do projeto

> **Pergunta que este documento responde:** o que é este sistema, em que estado está, e do que
> é feito?

## O que é

Um **agente de apoio ao cliente de passagem única, com humano no circuito**. Corre de 2 em 2
minutos, lê os emails novos de uma caixa de apoio, e para cada um decide uma de três coisas:

| Ação | O que faz | Quem age a seguir |
|---|---|---|
| `rascunhar` | Escreve a resposta e deixa-a como rascunho no Outlook | Pessoa revê e envia |
| `escalar` | Etiqueta o email e escreve a resposta de retenção | Pessoa decide e executa |
| `saltar` | Ignora — não é correspondência de cliente | Ninguém |

Não é conversacional. Não tem memória de sessão. Cada email é uma decisão independente,
informada por contexto que o código vai buscar antes de perguntar ao modelo.

> [!IMPORTANT] A propriedade que torna tudo o resto seguro
> A aplicação **não tem permissão para enviar email** nem para escrever na Shopify. Mesmo uma
> falha total do modelo produz apenas texto que uma pessoa apaga.
> Ver [[security|Segurança]].

## Estado atual

**Implemented** — em produção desde 26 de agosto de 2026.

| | |
|---|---|
| Caixa | `info@tripat3s.com` (Microsoft 365) |
| Loja | Shopify, integração só de leitura |
| Modelo | `claude-sonnet-5` |
| Modo | `DRY_RUN=false` — cria rascunhos reais |
| Cadência | systemd timer, 2 minutos |
| Servidor | Debian, `/opt/assistente`, utilizador dedicado |

Primeiro dia de produção: 23 emails processados, 0 perdidos.

> [!NOTE] Semana de observação
> O sistema está numa semana de observação até ~02/09/2026, para medir custo real e taxa de
> escalação com volume real antes de decisões sobre modelo. Ver [[improvements|Melhorias]].

## Stack

Deliberadamente pequena. Quatro dependências de runtime, zero dependências de teste.

```
Python ≥3.11 (a correr em 3.14)
├── anthropic        Claude Messages API
├── msal             autenticação Microsoft Graph
├── httpx            cliente HTTP (Graph + Shopify)
└── python-dotenv    configuração

SQLite               estado local (cursor, decisões, compromissos)
systemd              agendamento (oneshot + timer)
unittest             testes (biblioteca padrão)
```

**Ausências deliberadas:** sem framework web, sem ORM, sem fila de mensagens, sem Docker, sem
CI/CD, sem processo permanente, sem framework de agentes. Ver [[technical-decisions|Decisões técnicas]].

## Dimensão

| Componente | Linhas | Nota |
|---|---|---|
| `assistente.py` | 2 841 | Todo o caminho de produção |
| `test_assistente.py` | 2 708 | 275 testes |
| 11 ferramentas satélite | ~2 265 | Fora do caminho crítico; só o `manutencao.py` escreve |
| `knowledge/*.md` | 805 | A base de conhecimento |
| `eval/casos.json` | 98 casos | Banco de ensaio |

O prompt de sistema (instruções + base de conhecimento) tem **32 331 tokens**, medidos com
`count_tokens`. Ver [[ai-architecture|Arquitetura de IA]].

## O que o distingue

Não há novidade algorítmica: uma chamada ao modelo, saída estruturada, sem *tool use*, sem
*fine-tuning*. A engenharia está em três sítios:

1. **A fronteira código/modelo é explícita e foi movida com evidência.** Decisões que o modelo
   demonstrou executar mal — provar identidade, somar datas — foram passadas para código
   determinístico. Ver [[decision-making|Tomada de decisão]].
2. **O sistema instrumenta a própria ignorância.** Nove categorias classificam o motivo de cada
   escalação, e as lacunas de conhecimento produzem uma pergunta acionável, não um "não sei".
   Ver [[escalation|Escalação]].
3. **As falhas de produção viram testes permanentes.** Cada bug corrigido tem um caso de eval
   com data e descrição do incidente. Ver [[evaluation|Banco de ensaio]].

## Onde está o quê

```
Outlook-Reply-Assistant/
├── assistente.py           ← todo o caminho de produção
├── knowledge/              ← a base de conhecimento (7 ficheiros .md)
├── eval/                   ← banco de ensaio (98 casos + fixtures de imagem)
├── deploy/                 ← units systemd + scripts Windows
├── shopify-app/            ← configuração da app Shopify (scopes)
├── docs/                   ← esta knowledge base
├── entregas/               ← documentos para o cliente (PDF)
├── test_assistente.py      ← 275 testes
└── {eval,metricas,lacunas,medir_deriva,reprocessar,
     exportar,casos_antigos,verificar,verificar_kb,manutencao,aquecer}.py
                            ← ferramentas de operação
```

## Related

- [[problem-and-solution|Problema e solução]] — porque foi construído
- [[capabilities|Capacidades]] — o inventário completo do que faz
- [[system-architecture|Arquitetura do sistema]] — como está construído
- [[end-to-end-flow|Fluxo ponta a ponta]] — a viagem de um email
