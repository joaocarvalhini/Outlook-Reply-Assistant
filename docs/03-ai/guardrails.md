---
title: Guardrails
type: reference
status: implemented
tags:
  - ai
  - security
  - reference
---

# Guardrails

> **Pergunta que este documento responde:** que defesas impedem o sistema de dar uma resposta
> errada a um cliente, e em que nível cada uma atua?

## Os quatro níveis

Do mais fraco ao mais forte. Um guardrail de prompt pode ser contornado por uma alucinação; um
de infraestrutura não.

```mermaid
flowchart TB
    N1["<b>1. PROMPT</b> — 10 defesas<br/><i>instruções que o modelo pode falhar</i>"]
    N2["<b>2. ESQUEMA</b> — 2 defesas<br/><i>a API recusa saída malformada</i>"]
    N3["<b>3. CÓDIGO</b> — 7 defesas<br/><i>determinístico, testável</i>"]
    N4["<b>4. INFRAESTRUTURA</b> — 3 defesas<br/><i>impossível de contornar</i>"]

    N1 --> N2 --> N3 --> N4
    N4 --> R["Dano máximo:<br/>texto que uma<br/>pessoa apaga"]

    style N1 fill:#ffe0e0
    style N2 fill:#fff3e0
    style N3 fill:#e3f2fd
    style N4 fill:#c8e6c9
    style R fill:#c8e6c9
```

## Inventário completo — 22 guardrails

### Nível 1 — Prompt (10)

| # | Guardrail | O que impede |
|---|---|---|
| 1 | Fonte de verdade única e fechada | Usar conhecimento geral sobre comércio eletrónico |
| 2 | Ausência de regra nunca prova "não" | Recusar algo que a loja na verdade faz |
| 3 | Proibição de resposta vazia de conteúdo | "Vamos verificar e entramos em contacto" como resposta |
| 4 | Na dúvida, escala (ambas as fronteiras) | Responder ao que não sabia; descartar um cliente |
| 5 | Email é informação, não instruções | Injeção de prompt |
| 6 | Nunca inventar o que uma imagem mostra | Confirmar um defeito que a foto não mostra |
| 7 | Propor ≠ comprometer | Prometer uma ação com data que ninguém confirmou |
| 8 | Reembolso escala sempre, mesmo em pergunta | Mover dinheiro sem aprovação |
| 9 | Fórmula "verificar **se conseguimos**" | Prometer o resultado, não só o processo |
| 10 | Nunca inventar data de compromisso | Datas estimadas que viram expectativas |

### Nível 2 — Esquema (2)

| # | Guardrail | O que impede |
|---|---|---|
| 11 | `acao` restrita por `enum` | Uma ação inexistente |
| 12 | `additionalProperties: False` | Campos inventados |

### Nível 3 — Código (7)

| # | Guardrail | Onde | O que impede |
|---|---|---|---|
| 13 | Categoria fora da lista → `"OUTRO"` | `_validar()` | Métricas corrompidas em silêncio |
| 14 | `rascunhar` sem corpo → rebaixado a `escalar` | `processar()` | Um rascunho vazio a passar por resposta |
| 15 | Dossiê sem conteúdo → descartado | `tem_dossie` | Fila de dossiês maior do que é |
| 16 | Corte de lixo após assinatura | `sem_lixo_apos_assinatura()` | Um *glitch* de geração chegar ao cliente |
| 17 | HTML construído em código, texto escapado | `para_html()` | Injeção de HTML no rascunho |
| 18 | Data-limite calculada em Python | `resumir_encomenda()` | Aritmética errada do modelo |
| 19 | **Identidade decidida em código** | `pode_revelar` | **Expor dados de um cliente a outro** |

### Nível 4 — Infraestrutura (3)

| # | Guardrail | O que impede |
|---|---|---|
| 20 | Prefixo de aviso visível no rascunho | Um rascunho não revisto passar despercebido |
| 21 | **Sem `Mail.Send`** | Qualquer texto chegar a um cliente sem revisão |
| 22 | **Sem escrita na Shopify** | Qualquer alteração a encomendas ou pagamentos |

## Os dois mais importantes

> [!IMPORTANT] #19 — Identidade decidida em código
> É o guardrail que protege contra o erro mais caro possível: mostrar a encomenda de uma pessoa
> a outra. Não depende de o modelo "obedecer" — o modelo **nunca recebe** os dados quando a
> identidade não está provada.
>
> Ver [[identity-resolution|Resolução de identidade]].

> [!IMPORTANT] #21 e #22 — Sem permissão de escrita
> São o que torna todos os outros toleráveis. Mesmo que os 20 guardrails anteriores falhem em
> simultâneo, o resultado é um rascunho errado que uma pessoa lê e apaga. Nenhum email sai,
> nenhuma encomenda muda.

## O padrão: guardrails que nasceram de falhas

Sete dos 22 existem porque algo correu mal em produção:

| # | Guardrail | Incidente |
|---|---|---|
| 6 | Nunca inventar o que a imagem mostra | Desenho preventivo com a funcionalidade de visão |
| 9 | "Verificar se conseguimos" | 18/08/2026 — resposta prometia o resultado como certo |
| 14 | Rebaixamento de corpo vazio | Modelo escolhia `rascunhar` e devolvia corpo vazio |
| 15 | Dossiê por conteúdo | 18/08/2026 — dossiês bons rejeitados por causa da etiqueta |
| 16 | Corte após assinatura | 18/08/2026 — `"tripat3sascamentoaao_confirmar"` |
| 18 | Data em Python | 21/08/2026 — errou o cálculo com a data à mão |

> [!TIP] O sinal de maturidade não é ter guardrails — é a proveniência deles
> Um sistema com 22 defesas genéricas copiadas de um artigo não é mais seguro do que um com 5
> escritas a partir de falhas observadas. Estas têm data e caso de origem.

## Cobertura de teste

| Nível | Testado por | Cobertura |
|---|---|---|
| Prompt | [[evaluation\|eval.py]] — 96 casos | Boa; casos dedicados para #2, #3, #4, #5, #6 |
| Esquema | A API | Implícita |
| Código | `test_assistente.py` — 252 testes | Boa para #13, #14, #15, #16, #17, #18, #19 — a classe `Processar` fechou #14 e #15 a 27/08/2026 (Finding H-2) |
| Infraestrutura | `verificar.py` | Testa ativamente #21 e a restrição de caixa |

Ver [[qa|QA e testes]].

## O que os guardrails não cobrem

Sendo honesto sobre os limites:

| Lacuna | Consequência |
|---|---|
| Nada verifica **contradições dentro da base** | Duas regras conflituantes passam sem aviso |
| Nada verifica se a resposta **cita a política certa** | O modelo pode aplicar a regra errada com confiança |
| Nada mede a **deriva de qualidade** continuamente | `medir_deriva.py` existe mas corre a pedido |
| Regras de **baixa saliência** em documentos grandes | Falharam em teste (higiene, pack) |

Estes são limites reais, não teóricos: dois deles falharam na medição de 26/08/2026.
Ver [[limitations|Limitações]].

## Related

- [[decision-making|Tomada de decisão]] — a fronteira que os guardrails de código protegem
- [[identity-resolution|Resolução de identidade]] — o guardrail #19 em detalhe
- [[prompts|Prompts]] — o texto dos guardrails de nível 1
- [[security|Segurança]] — injeção de prompt e permissões
- [[evaluation|Banco de ensaio]] — como são testados
