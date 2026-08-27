---
title: Capacidades
type: reference
status: implemented
tags:
  - overview
  - reference
---

# Capacidades

> **Pergunta que este documento responde:** o que é que o sistema consegue e não consegue fazer,
> em concreto?

Inventário verificado contra o código. Legenda: ✅ implementado · 🟡 parcial · ❌ ausente
· ⚠️ problemático.

## Processamento de email

| Capacidade | Estado | Onde | Nota |
|---|---|---|---|
| Ler emails novos de uma caixa | ✅ | `Graph.novas` | Lote de 25, filtro por cursor |
| Não reprocessar o mesmo email | ✅ | `ja_processado` | Chave: `internetMessageId` |
| Descartar robôs e newsletters sem custo | ✅ | `triar` | 10 regras determinísticas |
| Cortar a conversa citada | ✅ | `cortar_citacao` | 6 padrões PT/EN |
| Ler o fio da conversa | ✅ | `Graph.historico` | 8 mensagens × 400 chars |
| Distinguir quem falou no fio | ✅ | `e_da_loja` | Apanha nomes distintos do Exchange |
| Criar rascunho encadeado | ✅ | `Graph.criar_rascunho` | `createReply` |
| Marcar o email com categoria | ✅ | `Graph.marcar` | Preserva categorias existentes |
| **Enviar email** | ❌ | — | **Por construção.** Sem `Mail.Send` |

## Compreensão do cliente

| Capacidade | Estado | Onde | Nota |
|---|---|---|---|
| Encontrar a encomenda por número | ✅ | `resolver_encomenda` | Nível 1 e 2 |
| Encontrar a encomenda só pelo email | ✅ | `resolver_encomenda` | Nível 3, só se inequívoco |
| Provar que a encomenda é de quem escreve | ✅ | `Correspondencia.pode_revelar` | 4 níveis de confiança |
| Reconhecer identidade por telefone/CP/nome | ✅ | `_sinais_de_identidade` | Cliente que escreve de outro email |
| Tratar várias encomendas no mesmo email | ✅ | `processar` | Cada uma verificada à parte |
| Ler fotografias anexadas | ✅ | `selecionar_anexos_de_imagem` | ≤5 MB, ≤4, 4 formatos |
| Ler vídeos | ❌ | — | Nota explícita ao modelo a pedir fotos |
| Ler PDFs anexados | ❌ | — | Entram na nota de "não processados" |
| Recuperar clientes de formulários do site | ✅ | `desembrulhar_formulario_*` | [[web-forms\|Shopify + Formspree]] |

## Dados da encomenda (Shopify)

| Dado | Estado | Nota |
|---|---|---|
| Número, data, valor | ✅ | |
| Estado de pagamento | ✅ | 6 estados traduzidos |
| Estado de expedição | ✅ | 3 estados traduzidos |
| Código e link de rastreio | ✅ | Quando o fulfillment o traz |
| Estado do envio (em trânsito, entregue…) | ✅ | 9 estados; nem toda a transportadora preenche |
| Data real de entrega | ✅ | Chamada extra a `fulfillment_events` |
| Prazo de devolução (data-limite) | ✅ | Calculado em Python, não pelo modelo |
| **Encomendas com mais de 60 dias** | ❌ | Limite do scope `read_orders` |
| **Stock / disponibilidade** | ❌ | Falta scope `read_products` |
| **Alterar seja o que for** | ❌ | **Por construção.** Só leitura |

Ver [[shopify|Integração Shopify]].

## Decisão e redação

| Capacidade | Estado | Onde |
|---|---|---|
| Decidir entre rascunhar / escalar / saltar | ✅ | `decidir` |
| Classificar o motivo da escalação | ✅ | 9 categorias fixas (`CATEGORIAS`) |
| Escrever no estilo da loja | ✅ | Secção dedicada no prompt |
| Responder sempre em português de Portugal | ✅ | Regra no prompt, testada com email em inglês |
| Responder a parte de um email e assinalar o resto | ✅ | `por_responder` |
| Preparar dossiê de caso escalado | ✅ | 6 campos; ver [[escalation\|Escalação]] |
| Registar compromissos assumidos | ✅ | Sobrevive à janela do fio |
| Detetar lacunas de conhecimento acionáveis | ✅ | `lacuna_tema` + `lacuna_em_falta` |
| Recusar responder ao que não sabe | ✅ | Ver [[guardrails\|Guardrails]] |
| **Aprender com as respostas do lojista** | ❌ | **Intencional.** Ver [[knowledge-base]] |

## Fiabilidade

| Capacidade | Estado | Onde |
|---|---|---|
| Continuar se a Shopify falhar | ✅ | Escala por falta de dados |
| Continuar se o fio não vier | ✅ | Escala por falta de contexto |
| Continuar se os anexos falharem | ✅ | Decide sem imagens |
| Continuar se o dossiê falhar | ✅ | Escala sem dossiê |
| Saltar email apagado a meio da passagem | ✅ | Corrigido 26/08/2026 |
| Não perder email em falha do modelo | ✅ | `cursor_seguro`, corrigido 27/08/2026 |
| Retentar automaticamente | ✅ | Via timer — a passagem seguinte vê o mesmo |
| **Retentar 429/5xx no Graph/Shopify** | ❌ | Sem *backoff*. Ver [[technical-debt]] |
| **Alertar quando falha** | ❌ | Só `journalctl` |
| **Backup do estado** | ❌ | Nenhum |

## Operação e observabilidade

| Capacidade | Estado | Ferramenta |
|---|---|---|
| Ver a distribuição de decisões | ✅ | `metricas.py` |
| Ver a fila de casos preparados | ✅ | `dossie.py` |
| Ver as lacunas por frequência | ✅ | `lacunas.py` |
| Reavaliar decisões passadas com o código de hoje | ✅ | `reprocessar.py` |
| Comparar rascunho com resposta real | 🟡 | `medir_deriva.py` — referência nunca medida |
| Verificar a instalação antes de ligar | ✅ | `verificar.py` |
| Exportar casos anonimizados | 🟡 | `exportar.py` — pseudonimização, não garantida |
| Ler casos históricos sem gastar créditos | ✅ | `casos_antigos.py` |
| Desligar funcionalidades sem *deploy* | ✅ | 5 `ENABLE_*` + `DRY_RUN` |
| **Painel / métricas ao longo do tempo** | ❌ | Tudo é a pedido |

Ver [[operations|Ferramentas de operação]].

## Âmbito — o que nunca vai fazer

> [!WARNING] Limites por desenho, não por falta de tempo
> Estas três não são funcionalidades em falta. São as propriedades que tornam o sistema seguro.

1. **Não envia email.** Sem `Mail.Send`. Todo o texto passa por uma pessoa.
2. **Não escreve na Shopify.** `read_orders` apenas. Quem cancela ou reembolsa é uma pessoa, no
   admin.
3. **Não aprende sozinho.** O mecanismo de melhoria tem de ser legível por um humano — um facto
   novo escreve-se em Markdown e passa por revisão.

## Related

- [[limitations|Limitações]] — o detalhe do que falta e porquê
- [[problem-and-solution|Problema e solução]] — a razão destas escolhas
- [[shopify|Shopify]] · [[email|Email]] — o detalhe das integrações
- [[technical-debt|Dívida técnica]] — o que está marcado ❌ e ⚠️ acima
