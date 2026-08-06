# Outlook Reply Assistant

Lê a caixa de entrada de uma loja online, decide que emails merecem resposta e
escreve um **rascunho encadeado** na pasta Rascunhos. Nunca envia nada.

```
2026-08-06T14:02:11 | INFO    | reply_assistant.triage     | rejected      | rule=bulk-header
2026-08-06T14:02:12 | INFO    | reply_assistant.classifier | classified    | category=pedido_cliente reply=True
2026-08-06T14:02:15 | INFO    | reply_assistant.outlook    | draft created | message=AAMkAGI2... draft=AAMkAGI3...
2026-08-06T14:02:15 | INFO    | reply_assistant.cli        | batch done    | seen=14 skipped=11 drafted=2 escalated=1
```

---

## O que faz e o que não faz

| Faz | Não faz |
|---|---|
| Classifica cada email recebido | Envia emails |
| Escreve rascunhos fundamentados na base de conhecimento da loja | Inventa políticas, prazos ou preços |
| Marca o original com uma categoria (`IA-Rascunhado` / `Precisa de humano`) | Apaga, arquiva ou move mensagens |
| Escala para revisão humana sempre que tem dúvidas | Acede a encomendas, pagamentos ou stock |

O rascunho é criado com `POST /messages/{id}/createReply` do Microsoft Graph, o
que significa que é **uma resposta a sério**: destinatário preenchido, assunto
`RE:`, cabeçalhos `In-Reply-To`/`References` e conversa agrupada. A equipa abre
os Rascunhos, revê e carrega em Enviar.

---

## Arquitetura

```
Novo email na Inbox
   │
   ├─ [0] Triagem determinística  ──► descartado, custo zero
   │      remetente, domínio, destinatários, cabeçalhos, anti-ciclo
   │
   ├─ [1] Classificador (Haiku 4.5, JSON validado por schema)
   │      responder = true apenas para pedido_cliente e pre_venda
   │
   ├─ [2] Gerador de resposta (base de conhecimento + email)
   │      devolve HTML ou uma linha `ESCALATE: <motivo>`
   │
   ├─ [3] createReply  ──► rascunho encadeado em Rascunhos
   │
   └─ [4] Categoria no original  ──► não volta a ser processado
```

Dois passos de IA em vez de um. O classificador custa cerca de um terço do
gerador e evita gastá-lo nos 60–70% da caixa que são newsletters e notificações
de plataformas.

```
daemon.py                 ciclo de polling, sinais, arranque
eval.py                   banco de ensaio: mede o que o pipeline decide
eval/cases.json           casos com resultado esperado
requirements.txt
.env.example
blocklist.txt             domínios bloqueados, editável pelo cliente
knowledge/                a totalidade do mundo do assistente
src/
  config.py               ambiente -> Config validado (falha ao arrancar)
  logger.py               logging estruturado
  models.py               dataclasses imutáveis: EmailMessage, Classification, ...
  knowledge_base.py       descobrir -> ler -> limpar -> KnowledgeBase
  utils.py                HTML -> texto, corte de citações, sanitização de saída
  triage.py               camada 0: as regras que não custam nada
  prompts.py              os dois prompts de sistema
  classifier.py           etapa 1: vale a pena responder?
  drafter.py              etapa 2: escrever ou escalar
  escalation.py           deteção do contrato `ESCALATE:`
  outlook.py              Microsoft Graph: listar, detalhe, createReply, categoria
  state.py                marca de água + registo de processados
  pipeline.py             orquestração por mensagem
tests/
  test_triage.py          todas as regras de triagem
  test_text.py            HTML, citações, sanitização, escalação
```

---

## Instalação

```bash
git clone <url>
cd Outlook-Reply-Assistant
python -m venv .venv && .venv\Scripts\activate      # Linux/macOS: . .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # Linux/macOS: cp .env.example .env
```

Requer Python 3.12 ou superior.

---

## Registo da aplicação no Microsoft Entra

1. **Entra admin center → App registrations → New registration.** Nome à
   escolha, conta de um único inquilino, sem redirect URI.
2. Guardar **Application (client) ID** e **Directory (tenant) ID**.
3. **Certificates & secrets → New client secret.** Copiar o valor imediatamente;
   deixa de ser visível.
4. **API permissions → Microsoft Graph → Application permissions → `Mail.ReadWrite`
   → Grant admin consent.**

### Restringir o acesso a uma única caixa — passo obrigatório

`Mail.ReadWrite` como permissão de aplicação dá acesso a **todas** as caixas do
inquilino. Restrinja antes de pôr em produção, no Exchange Online PowerShell:

```powershell
New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId apoio@loja.pt -AccessRight RestrictAccess -Description "Assistente de rascunhos"
```

Confirmar:

```powershell
Test-ApplicationAccessPolicy -Identity apoio@loja.pt -AppId <client-id>
```

Uma segunda verificação, com outra caixa qualquer, deve devolver `Denied`.

---

## Configuração

Tudo vem de `.env` ou do ambiente. As cinco primeiras são obrigatórias; as
restantes têm um valor por omissão que funciona.

| Variável | Omissão | Função |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chave da API |
| `GRAPH_TENANT_ID` | — | Directory (tenant) ID |
| `GRAPH_CLIENT_ID` | — | Application (client) ID |
| `GRAPH_CLIENT_SECRET` | — | Client secret |
| `MAILBOX` | — | Caixa a vigiar, ex. `apoio@loja.pt` |
| `CLASSIFIER_MODEL` | `claude-haiku-4-5` | Modelo da etapa 1 |
| `REPLY_MODEL` | `claude-haiku-4-5` | Modelo da etapa 2 |
| `MAX_TOKENS` | `1024` | Teto do rascunho |
| `KNOWLEDGE_DIR` | `knowledge` | Pasta de documentos `.md`/`.txt` |
| `BLOCKLIST_FILE` | `blocklist.txt` | Domínios bloqueados |
| `STATE_FILE` | `state.json` | Marca de água e registo local |
| `POLL_SECONDS` | `300` | Intervalo entre passagens (mínimo 30) |
| `MAX_BODY_CHARS` | `4000` | Corte do corpo enviado ao modelo |
| `DRY_RUN` | `true` | `true` não escreve nada na caixa |
| `COMPANY_NAME` | `A Loja` | Aparece nos prompts |
| `ASSISTANT_NAME` | `Assistente` | Aparece nos prompts |
| `SIGNATURE` | `Equipa de Apoio ao Cliente` | Assinatura do rascunho |
| `DRAFTED_CATEGORY` | `IA-Rascunhado` | Categoria aplicada ao original |
| `ESCALATED_CATEGORY` | `Precisa de humano` | Categoria de escalação |
| `LOG_LEVEL` | `INFO` | Verbosidade da consola |
| `LOG_FILE` | — | Log adicional em ficheiro, nível INFO |

Flags de linha de comandos sobrepõem-se ao ambiente numa execução:

```bash
python daemon.py --once --log-level DEBUG
python daemon.py --no-dry-run --log-file logs/assistant.log
```

---

## Base de conhecimento

O assistente **só** pode afirmar o que estiver em `knowledge/`. Uma pasta vazia
faz o arranque falhar, de propósito: sem políticas da loja o sistema escala todos
os emails, o que é correto mas não resolve nada.

Copie `knowledge/politicas.md.template` para `knowledge/politicas.md` e preencha.
Se ficar acima de duas páginas, divida em vários ficheiros — o carregador junta
todos por ordem alfabética.

Regra prática: se não souber a resposta, deixe de fora. Uma secção em falta faz o
assistente escalar; uma secção errada faz o assistente mentir a um cliente.

---

## Primeira execução

1. `DRY_RUN=true` no `.env` (é o valor por omissão).
2. `python daemon.py --once --log-level DEBUG`
3. Ler o log. Cada mensagem mostra a decisão e a regra que a produziu.
4. Passar uma semana assim, ajustando `blocklist.txt` e a base de conhecimento
   até os `skipped` e os `escalated` fazerem sentido.
5. Só então `DRY_RUN=false`.

A primeira execução coloca a marca de água no instante actual e ignora o
histórico. Isto é deliberado: responder a um ano de emails antigos seria caro e
errado.

Em produção use `--once` a partir do Agendador de Tarefas do Windows ou do cron,
em vez de deixar o processo a correr indefinidamente — reinicia limpo e o estado
sobrevive no `state.json`.

---

## Tratamento de exceções

Quatro camadas. As três primeiras não custam tokens.

| Camada | Regra | Onde |
|---|---|---|
| 0 | Já marcado como processado | `triage.py` |
| 0 | Remetente é a própria caixa ou o domínio da loja (anti-ciclo) | `triage.py` |
| 0 | Local-part de robô: `noreply`, `notifications`, `bounce`, ... | `triage.py` |
| 0 | Domínio em `blocklist.txt` ou na lista base | `triage.py` |
| 0 | A loja não consta em Para nem Cc (envio em massa) | `triage.py` |
| 0 | Cabeçalhos `List-Unsubscribe`, `List-Id`, `Precedence: bulk`, `Auto-Submitted: auto-*` | `triage.py` |
| 1 | Classificador devolve `responder: false` | `classifier.py` |
| 2 | Gerador devolve `ESCALATE:` — tema ausente, ambíguo, ou pedido sobre dados do cliente | `drafter.py` |
| 3 | Falha técnica: API, Graph, resposta vazia ou truncada | `pipeline.py` |

Qualquer incerteza produz uma escalação, nunca um rascunho. Um rascunho errado
que a equipa aprova por distração é o único modo de falha caro deste sistema.

---

## Avaliação

Os testes unitários provam que o código faz o que foi escrito. O `eval.py` prova
que o **prompt** faz o que é preciso — e é a única forma de saber se uma
alteração ao prompt ou à base de conhecimento melhorou ou piorou o
comportamento.

```bash
python eval.py --stage triage    # só as regras determinísticas: grátis, instantâneo
python eval.py                   # pipeline completo, contra o modelo real
```

Não toca na caixa de correio — o Graph não entra aqui, por isso é seguro correr
contra uma configuração de produção.

Cada caso em `eval/cases.json` declara um resultado esperado:

| `expect` | Significado |
|---|---|
| `skip` | O pipeline nunca deve responder a isto |
| `escalate` | Um humano tem de ver; o assistente não pode responder |
| `draft` | O assistente deve conseguir responder a partir da base de conhecimento |

Saem três números, e não têm o mesmo peso:

| Número | Significado | Alvo |
|---|---|---|
| **Clientes perdidos** | Casos que deviam gerar rascunho ou escalação e foram descartados em silêncio. Em produção não deixam rasto nenhum | **Zero.** Qualquer valor acima falha a execução |
| **Recall de escalação** | Dos casos que *deviam* escalar, quantos escalaram. Baixo significa que o assistente respondeu ao que não sabia — inventou uma política e um cliente acreditou | O mais alto possível |
| **Precisão de escalação** | Dos casos que escalaram, quantos deviam. Baixa significa trabalho a mais para a equipa. Chato, seguro | Secundário |

O conjunto que vem no repositório tem 13 casos, todos verdadeiros
independentemente das políticas da loja: sete de triagem determinística, dois de
classificação e quatro de escalação estrutural (pedidos sobre encomendas
concretas, ameaça de reclamação formal, tentativa de injeção de prompt).

**Faltam os casos `draft`** — os que afirmam "o assistente devia ter conseguido
responder a isto". Esses dependem inteiramente das políticas da loja e têm de
sair de emails reais da caixa do cliente, anotados à mão. Junte-os em
`eval/real-*.json`, que o `.gitignore` já exclui: mesmo anonimizada, aquela é
correspondência do cliente.

Regra prática: um caso novo por cada documento acrescentado à base de
conhecimento, e um caso novo por cada erro assim que for corrigido — para que uma
regressão apareça como um `FAIL` e não como uma reclamação.

---

## Custos

Estimativa para 1.000 emails recebidos por mês, com a triagem a descartar ~65%:

| Configuração | Tokens/mês |
|---|---|
| Haiku 4.5 nas duas etapas | ~3 € |
| Haiku 4.5 a classificar, Sonnet 5 a redigir | ~7 € |

Preços por milhão de tokens: `claude-haiku-4-5` 1 $ / 5 $ · `claude-sonnet-5`
3 $ / 15 $ · `claude-opus-5` 5 $ / 25 $. Opus é desnecessário aqui.

O prompt de sistema é construído uma única vez e marcado para cache. Note que o
prefixo mínimo para o cache pegar no Haiku 4.5 são 4.096 tokens — uma base de
conhecimento pequena não beneficia. Em Sonnet 5 o mínimo é 1.024.

Se trocar para `claude-sonnet-5`, acrescente `thinking: {"type": "disabled"}` às
chamadas: nesse modelo o raciocínio adaptativo está ligado por omissão e aqui não
traz nada.

---

## Testes

Sem dependências de teste — a suite corre na biblioteca padrão:

```bash
python -m unittest discover -s tests -t .
```

52 testes. Cobrem todas as regras de triagem (incluindo o anti-ciclo e os
subdomínios), a conversão de HTML para texto, o corte de citações, a sanitização
da saída do modelo e a deteção do contrato de escalação. Não é preciso rede nem
credenciais.

---

## Decisões de desenho

**Dois prompts, não um.** O classificador não vê a base de conhecimento e o
gerador não vê mais nada. O prompt caro só corre no tráfego que o justifica, e um
erro do classificador não consegue derramar política da loja para dentro de uma
resposta.

**A escalação é um contrato analisado, não prosa.** `ESCALATE: <motivo>` é uma
linha que o código deteta de forma determinística. A deteção tolera que o modelo
decore o marcador com markdown ou ponha o motivo na linha seguinte, mas nunca
inventa um motivo onde o marcador não existe.

**Os erros escalam em vez de pedir desculpa.** Graph inacessível, API em erro,
resposta truncada — são todos casos em que não há resposta com confiança, que é
exactamente para isso que serve o caminho de escalação. Um só caminho de código
para raciocinar sobre ele, e uma categoria na caixa que diz à equipa o que ver.

**A categoria no original é o registo durável; o `state.json` é só o caminho
rápido.** Apagar o ficheiro de estado é seguro. Apagar as categorias não é.

**O HTML de saída é reconstruído a partir de uma lista branca**, não filtrado. O
corpo deriva de um email não confiável, e "um humano revê antes de enviar" é um
processo, não um controlo de segurança.

**A triagem é uma função pura sobre uma dataclass.** Sem rede, sem modelo, sem
estado — é a camada onde vivem as decisões que mais importam e é a mais barata de
testar.

---

## Por fazer

- **Casos `draft` e falsos negativos da triagem.** O `eval.py` está feito, mas o
  conjunto de casos só cobre o que é verdadeiro sem a base de conhecimento.
  Faltam os emails reais: os que o assistente devia conseguir responder, e os
  emails de clientes que a triagem descartou por engano. Os segundos são
  invisíveis em produção e custam vendas.
- **Webhooks em vez de polling.** Uma subscrição do Graph elimina a latência dos
  5 minutos, mas exige um endpoint HTTPS público e renovação a cada ~3 dias.
  Polling é mais simples e chega para este volume.
- **Recuperação de contexto.** A base de conhecimento inteira vai em cada
  chamada. Acima de ~100 KB de documentos passa a compensar recuperar apenas os
  trechos relevantes.
- **Várias caixas.** Hoje é uma caixa por processo. Várias lojas exigiriam
  configuração por caixa e um estado separado para cada uma.
