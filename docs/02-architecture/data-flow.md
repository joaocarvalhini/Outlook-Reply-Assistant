---
title: Fluxo de dados
type: architecture
status: implemented
tags:
  - architecture
  - data
---

# Fluxo de dados

> **Pergunta que este documento responde:** que dados existem, de onde vêm, como se transformam,
> e o que fica gravado?

## O pipeline

```mermaid
flowchart LR
    subgraph IN["Entrada"]
        I1["Email bruto<br/>HTML + cabeçalhos"]
        I2["Anexos<br/>bytes"]
    end
    subgraph TR["Transformação"]
        T1["para_texto()"]
        T2["cortar_citacao()"]
        T3["truncar 4000"]
        T4["base64 ≤5MB"]
    end
    subgraph EN["Enriquecimento"]
        E1["fio<br/>8 × 400"]
        E2["encomenda<br/>Shopify"]
        E3["compromissos<br/>SQLite"]
        E4["base<br/>knowledge/"]
        E5["saudação<br/>+ data"]
    end
    subgraph MD["Modelo"]
        M1["sistema<br/>29K · cacheado"]
        M2["utilizador<br/>variável"]
        M3["JSON validado"]
    end
    subgraph OUT["Saída"]
        O1["rascunho<br/>no Outlook"]
        O2["categoria<br/>no email"]
        O3["linha em<br/>processados"]
        O4["linha em<br/>compromissos"]
        O5["cursor"]
        O6["log no<br/>journal"]
    end

    I1 --> T1 --> T2 --> T3 --> M2
    I2 --> T4 --> M2
    E1 & E2 & E3 & E5 --> M2
    E4 --> M1
    M1 & M2 --> M3
    M3 --> O1 & O2 & O3 & O4 & O6
    O3 --> O5

    style MD fill:#e8d5f2
    style OUT fill:#d5e8f2
```

## Orçamento de contexto

O que ocupa espaço no pedido, e porquê está onde está.

| Bloco | Tamanho | Cacheado? | Porquê |
|---|---|---|---|
| Instruções do prompt | ~430 linhas | ✅ | Nunca muda |
| Base de conhecimento | ~29K tokens | ✅ | Muda raramente, e só por commit |
| Saudação + data atual | 2 linhas | ❌ | **Muda ao longo do dia** |
| Compromissos registados | Variável | ❌ | Por conversa |
| Fio da conversa | ≤8 × 400 chars | ❌ | Por conversa |
| Email novo | ≤4000 chars | ❌ | Por email |
| Dados da encomenda | ~10 linhas | ❌ | Por email |
| Imagens | ≤4 × 5 MB | ❌ | Por email |

> [!IMPORTANT] Porque é que a saudação não está no prompt de sistema
> **Implemented** — `decidir()`: a saudação muda de "Bom dia" para "Boa tarde" às 13h. Se
> estivesse no bloco de sistema, **invalidava o cache da base de conhecimento inteira** a cada
> mudança. Fica na mensagem do utilizador, que não é cacheada.

## Dados que entram no modelo — e os que não entram

Esta é a distinção mais importante do fluxo de dados.

```mermaid
flowchart TB
    S["Shopify devolve<br/>CAMPOS_ENCOMENDA"]
    S --> A["<b>Usados só em código</b><br/>customer · shipping_address<br/>email · contact_email"]
    S --> B["<b>Enviados ao modelo</b><br/>via resumir_encomenda()"]

    A --> A1["Verificação de identidade<br/>_sinais_de_identidade()"]
    A1 --> A2["Nunca chegam ao prompt.<br/>Nunca chegam ao cliente."]

    B --> B1["número · data<br/>estado de pagamento<br/>estado de expedição<br/>rastreio · estado do envio<br/>data de entrega<br/>prazo de devolução<br/>valor total"]

    style A2 fill:#ffcdd2
    style B1 fill:#c8e6c9
```

**Implemented** — `Shopify.CAMPOS_ENCOMENDA` traz `customer` e `shipping_address` precisamente
para a verificação de identidade, e `resumir_encomenda()` **não os inclui** na saída. Nem morada
completa, nem telefone, nem email do comprador, nem dados de pagamento.

Ver [[identity-resolution|Resolução de identidade]] e [[security|Segurança]].

## Modelo de dados — SQLite

Três tabelas. O ficheiro é local (`assistente.db`) e está no `.gitignore`.

```mermaid
erDiagram
    meta {
        TEXT chave PK
        TEXT valor
    }
    processados {
        TEXT message_id PK "internetMessageId"
        TEXT conversation_id
        TEXT assunto
        TEXT acao "rascunhar|escalar|saltar"
        TEXT motivo
        TEXT corpo "para medir deriva"
        TEXT em
        TEXT categoria
        TEXT lacuna_tema
        TEXT lacuna_em_falta
        TEXT confianca_encomenda
        TEXT dossie_tipo
        TEXT dossie_resumo
        TEXT dossie_validacao
        TEXT dossie_accao
        TEXT dossie_risco
        TEXT dossie_resposta
        TEXT dossie_link
        TEXT por_responder
    }
    compromissos {
        TEXT conversation_id PK
        TEXT tipo PK
        TEXT descricao
        TEXT estado
        TEXT data_prometida
        TEXT atualizado_em
    }
    processados ||--o{ compromissos : "conversation_id"
```

### `meta` — o cursor

Uma linha, chave `cursor`. Marca temporal da mensagem mais recente processada com segurança.

### `processados` — uma linha por email

19 colunas. As 7 primeiras são originais; as 12 restantes foram acrescentadas ao longo do tempo
por `ALTER TABLE`.

> [!TIP] Migrações aditivas e `INSERT` nomeado
> **Implemented** — `abrir_db()` acrescenta cada coluna em falta com `ALTER TABLE`, em vez de
> recriar a base. A razão está no comentário do código: *o cursor vive na mesma base, e apagá-la
> faria o assistente reprocessar tudo desde o início*.
>
> E `registar()` usa colunas **nomeadas**, nunca posicionais — *"a tabela ganha colunas com o
> tempo, e um INSERT posicional passa a gravar valores na coluna errada sem dar erro"*.

O campo `corpo` guarda o texto do rascunho. Existe para uma coisa só: comparar mais tarde com o
que o lojista realmente enviou. Ver [[qa|QA e testes]].

### `compromissos` — estado, não histórico

Chave composta `(conversation_id, tipo)`. Um `ON CONFLICT DO UPDATE` substitui sempre o registo
anterior do mesmo tipo.

**Implemented** — `gravar_compromisso()`:

> Não é um histórico de tudo o que já foi prometido, é o estado atual: se a loja prometeu um
> reembolso e depois disse que já foi feito, o que importa ao próximo email é "concluído", não
> as duas mensagens.

Só compromissos com estado `pendente` são injetados no prompt seguinte.

```mermaid
flowchart LR
    A["Fio visível<br/>8 mensagens"] -.->|"promessa de<br/>há 3 semanas<br/>já não aparece"| B["❌ perdida"]
    C["Tabela<br/>compromissos"] -->|"sobrevive à<br/>janela do fio"| D["✅ injetada<br/>no prompt"]
    style B fill:#ffcdd2
    style D fill:#c8e6c9
```

## Onde ficam os dados

| Dado | Onde | Retenção | No git? |
|---|---|---|---|
| Cursor, decisões, compromissos | `assistente.db` (local) | **Indefinida** ⚠️ | Não |
| Corpo dos rascunhos | `processados.corpo` | **Indefinida** ⚠️ | Não |
| Logs de passagem | journal do systemd | Política do systemd | Não |
| Base de conhecimento | `knowledge/*.md` | Versionada | **Sim** |
| Casos de teste | `eval/casos.json` | Versionada | **Sim** |
| Emails reais anonimizados | `eval/real-*.json` | Local | Não (`.gitignore`) |
| Corpos de email | Enviados à API da Anthropic | Política da Anthropic | Não |

> [!WARNING] Sem política de retenção
> `processados` cresce indefinidamente, incluindo o corpo integral dos rascunhos. Não há purga
> nem backup. É risco de RGPD **e** risco operacional (perder o disco = perder o cursor).
> Finding M-4 em [[technical-debt|Dívida técnica]].

## Fluxo de saída — o HTML

O modelo devolve **texto simples**. O HTML é construído em código:

```python
# para_html() — assistente.py
paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
return "".join(
    "<p>" + html.escape(p, quote=False).replace("\n", "<br>") + "</p>"
    for p in paragrafos
)
```

> [!NOTE] Porquê construir e não sanitizar
> O corpo da resposta deriva de um email não confiável. O comentário no código:
> *"escapar texto é uma linha, enquanto sanitizar HTML de terceiros são cinquenta e nunca fica
> fechado"*.

## Related

- [[end-to-end-flow|Fluxo ponta a ponta]] — a sequência que move estes dados
- [[identity-resolution|Resolução de identidade]] — porque é que alguns dados nunca passam
- [[ai-architecture|Arquitetura de IA]] — o orçamento de contexto em detalhe
- [[security|Segurança]] — dados pessoais e RGPD
- [[technical-debt|Dívida técnica]] — retenção e backup
