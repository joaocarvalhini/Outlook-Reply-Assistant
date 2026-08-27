---
title: Melhorias recomendadas
type: roadmap
status: proposed
tags:
  - roadmap
  - improvements
---

# Melhorias recomendadas

> **Pergunta que este documento responde:** o que fazer a seguir, por que ordem, e quanto custa
> cada coisa?

> [!IMPORTANT] Tudo nesta página é `Proposed`
> Nada aqui está construído. Para o que **está** implementado, ver [[capabilities|Capacidades]].

Priorizado por rácio impacto/esforço, com base no que o código revela.

---

## P0 — Crítico

### ✅ P0-1 · Perda de email em falha do modelo

**Feito a 27/08/2026.** `cursor_seguro()` + 7 testes. Era o Finding C-1.
Ver [[technical-debt|Dívida técnica]].

### P0-2 · Verificação periódica da política de acesso do Exchange

| | |
|---|---|
| **Problema** | `verificar.py --outra-caixa` prova a restrição, mas só corre quando alguém se lembra. Se a política for removida, a aplicação passa a poder ler **todas** as caixas do inquilino e nada o assinala |
| **Solução** | Incluir a verificação na passagem, uma vez por dia, com falha ruidosa |
| **Impacto** | **Crítico** — é a única defesa contra acesso indevido ao correio da empresa |
| **Complexidade** | Baixa — a lógica já existe em `verificar.py` |

Ver [[security|Segurança]].

---

## P1 — Alto impacto

### P1-1 · Testes unitários para `processar()`

| | |
|---|---|
| **Problema** | 10 pontos de retorno, zero cobertura. É a concentração de risco do sistema |
| **Solução** | Duplos para `Graph`, `Shopify` e cliente Anthropic. **Os padrões já existem** (`ShopifyFalsa`, `ClienteFalso`) |
| **Impacto** | Alto |
| **Complexidade** | Média — trabalho de horas, não de dias |

Finding H-2.

### P1-2 · Retentativa com *backoff* em Graph e Shopify

| | |
|---|---|
| **Problema** | Um 429/5xx transitório degrada silenciosamente a decisão — escala por falta de dados quando os dados existiam |
| **Solução** | `httpx.HTTPTransport(retries=…)` ou decorador com *backoff*, limitado a 429/5xx |
| **Impacto** | Médio-alto |
| **Complexidade** | Baixa |

Finding M-2.

### P1-3 · Corrigir a documentação de custo e cache

| | |
|---|---|
| **Problema** | Três locais afirmam que a base não atinge o mínimo de cache do Haiku. Falso desde que a base cresceu — está 5,4× acima |
| **Solução** | Corrigir `README.md`, `.env.example` e o comentário em `assistente.py` |
| **Impacto** | Médio — **decisões de negócio dependem disto** |
| **Complexidade** | Trivial |

Finding H-1.

### P1-4 · Resolver o cálculo do pack em código

| | |
|---|---|
| **Problema** | A regra "valor = total ÷ nº artigos" está escrita mas falha em **ambos** os modelos |
| **Solução** | Fornecer o valor por artigo já calculado nos dados da encomenda, em vez de pedir ao modelo que divida |
| **Impacto** | Médio — trabalho manual recorrente e evitável |
| **Complexidade** | Baixa |

Finding H-3. É o mesmo padrão que resolveu o prazo de devolução — ver
[[technical-decisions|Decisões técnicas]].

### P1-5 · Alerta em falha de passagem

| | |
|---|---|
| **Problema** | Uma passagem que falhe repetidamente só se descobre por inspeção manual |
| **Solução** | `OnFailure=` no systemd com um envio simples |
| **Impacto** | Médio |
| **Complexidade** | Baixa |

Finding M-6.

---

## P2 — Evolução

| # | Melhoria | Problema | Complexidade |
|---|---|---|---|
| P2-1 | **CI mínimo** | Nada impede deploy com testes a falhar. As verificações são grátis e demoram <1 s | Baixa |
| P2-2 | **Backup do SQLite** | Perder o disco = perder o cursor | Trivial |
| P2-3 | **Reconciliar o README** | A secção "Âmbito" descreve um sistema anterior à Shopify | Trivial |
| P2-4 | **Fechar `INVENTARIO_INDISPONIVEL`** | Scope `read_products` + consulta de stock | Média |
| P2-5 | **Política de retenção** | Correspondência guardada indefinidamente (RGPD) | Baixa |
| P2-6 | **Deteção de contradições na base** | Nada verifica se duas secções se contradizem | Média |
| P2-7 | **Medir a linha de base da deriva** | A referência dos 60% nunca foi medida | Baixa |

### Sobre P2-4 — a categoria mais facilmente eliminável

```mermaid
flowchart LR
    A["Pergunta de stock"] --> B["Hoje: escala<br/>INVENTARIO_INDISPONIVEL"]
    A -.->|"com read_products"| C["Responde<br/>com o stock real"]
    style B fill:#ffe0b2
    style C fill:#c8e6c9
```

O padrão de integração já existe (autenticação, cliente HTTP, tradução de estados). Falta pedir
o scope e escrever a consulta.

### Sobre P2-6 — deteção de contradições

**Inference:** uma verificação por LLM, **offline**, que leia os 7 documentos e assinale regras
conflituantes, correndo apenas quando `knowledge/` muda. Não entra no caminho de produção nem
custa por email.

---

## P3 — Visão

| # | Melhoria | Nota |
|---|---|---|
| P3-1 | **Fecho de ciclo com o resultado real** | Saber se o rascunho foi enviado tal e qual, editado ou apagado |
| P3-2 | **Editor de base de conhecimento** | Hoje exige editar Markdown e fazer commit |
| P3-3 | **Raciocínio seletivo por categoria** | `thinking` adaptativo só em devoluções/garantia |
| P3-4 | **Painel de operação** | Custo, latência e qualidade ao longo do tempo |
| P3-5 | **Multi-tenancy** | Ver [[future-architecture\|Arquitetura futura]] |

### Sobre P3-1 — a lacuna de observabilidade mais importante

O sistema regista o que **decidiu**, mas não sabe o que **aconteceu depois**.

```mermaid
flowchart LR
    A["Decisão gravada<br/>✅ sabemos"] --> B["Rascunho criado<br/>✅ sabemos"]
    B --> C["Operador revê<br/>❌ não sabemos"]
    C --> D["Enviado tal e qual?<br/>Editado? Apagado?<br/>❌ não sabemos"]
    style C fill:#ffe0e0
    style D fill:#ffe0e0
```

**Inference:** detetável comparando o rascunho gravado com a mensagem efetivamente enviada na
conversa — **a lógica já existe em `medir_deriva.py`**, falta a cadência automática.

Transformaria a medição de deriva de ferramenta manual em métrica contínua, e fecharia o risco
de "deriva silenciosa" identificado no próprio README.

### Sobre P3-3 — raciocínio seletivo

`thinking` está desativado. As falhas observadas concentram-se em **regras compostas** (pack,
higiene × tipo de produto).

**Inference:** ativar raciocínio adaptativo **apenas** quando a categoria é de devolução/garantia
poderia fechar parte dos 9% restantes a custo controlado. Mensurável com o
[[evaluation|banco de ensaio]] existente antes de decidir.

---

## O que não fazer

> [!IMPORTANT] Três coisas que parecem melhorias e não são
> Removê-las mudaria a natureza do sistema.

| Não fazer | Porquê |
|---|---|
| **Permitir envio automático** | É a propriedade que torna todo o resto seguro. Sem ela, uma alucinação chega ao cliente |
| **Permitir escrita na Shopify** | Idem. `dossie.py` afirma-o explicitamente: *"não tem permissão de escrita e não a vai ter"* |
| **Aprendizagem automática a partir das respostas** | O ciclo humano é intencional: *"o mecanismo de melhoria tem de ser legível por um humano"* |

---

## Sequência sugerida

```mermaid
flowchart LR
    A["<b>Agora</b><br/>semana de observação<br/><i>até ~02/09</i>"] --> B["<b>Imediato</b><br/>P1-3 documentação<br/>P2-2 backup<br/>P2-3 README"]
    B --> C["<b>Curto prazo</b><br/>P0-2 verificação<br/>P1-5 alertas<br/>P2-1 CI"]
    C --> D["<b>Médio prazo</b><br/>P1-1 testes<br/>P1-2 retry<br/>P1-4 pack"]
    D --> E["<b>Depois dos dados</b><br/>P2-4 stock<br/>P2-7 deriva<br/>decisão de modelo"]

    style A fill:#e8d5f2
    style B fill:#c8e6c9
```

> [!TIP] Começar pelas triviais
> P1-3, P2-2 e P2-3 são de esforço trivial e fecham dois riscos reais (decisão de custo com
> informação errada; perda de estado). Valem mais por hora investida do que qualquer item de P1.

## Related

- [[technical-debt|Dívida técnica]] — os findings que estas melhorias resolvem
- [[limitations|Limitações]] — o que cada melhoria removeria
- [[future-architecture|Arquitetura futura]] — o que fica para lá do P3
- [[escalation|Escalação]] — as categorias que P2-4 fecharia
- [[qa|QA e testes]] — o contexto de P1-1
