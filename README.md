# Assistente de rascunhos — tripat3s

Lê a caixa de apoio ao cliente, decide o que fazer com cada email novo e deixa um
**rascunho encadeado** na pasta Rascunhos. Nunca envia nada — a aplicação não tem
sequer permissão para isso.

```
2026-08-06T14:02:12Z | rascunho | email=<CAF8x@mail.gmail.com> draft=AAMkAGI3...
2026-08-06T14:02:14Z | escalado | email=<CAF9y@mail.gmail.com> motivo=pede ação sobre encomenda concreta
2026-08-06T14:02:15Z | passagem | vistos=14 dry_run=False saltado=11 rascunhado=2 escalado=1
```

Um ficheiro Python de menos de 1000 linhas. Sem base de dados servida, sem
filas, sem interface, sem permissão de envio.

---

## Como funciona

```
timer (2 min)
  ├─ Graph: mensagens recebidas depois do cursor
  ├─ SQLite: já processei este internetMessageId? → sim, salta
  ├─ Triagem determinística: robôs, newsletters, domínio próprio → salta
  ├─ Shopify: email menciona nº de encomenda? → consulta, só se o email bater
  │  certo com o da encomenda (client credentials grant, scope read_orders)
  ├─ Claude: 1 chamada → {"acao": "rascunhar"|"escalar"|"saltar", ...}
  └─ "rascunhar" → Graph createReply · "escalar" → categoria para humano
```

A Shopify só responde a perguntas de leitura (estado do pagamento, se foi
expedida, código de rastreio). Pedidos para cancelar, alterar ou reembolsar
continuam sempre a escalar — a app só tem `read_orders`, nunca escrita.

Uma passagem e sai. Não há ciclo interno nem processo permanente: um arranque
limpo de dois em dois minutos é mais robusto do que um processo que tem de
sobreviver a semanas, e o estado vive no SQLite.

O rascunho é criado com `POST /messages/{id}/createReply`, o que significa que é
**uma resposta a sério**: destinatário preenchido, assunto `RE:`, cabeçalhos
`In-Reply-To`/`References` e conversa agrupada. A equipa abre os Rascunhos, revê
e carrega em Enviar.

```
assistente.py        tudo: config, triagem, texto, prompt, SQLite, Graph, Shopify, Claude
verificar.py         verificação prévia, para o dia da instalação
exportar.py          exporta emails reais anonimizados + conta a distribuição
test_assistente.py   74 testes, biblioteca padrão, sem rede
eval.py              banco de ensaio: mede o que o assistente decide
eval/casos.json      casos com resultado esperado
knowledge/           a totalidade do mundo do assistente
blocklist.txt        domínios bloqueados, editável sem tocar em código
deploy/              agendamento: Windows (testes) e systemd (produção)
```

---

## Três ações, não duas

| Ação | Quando | O que acontece |
|---|---|---|
| `rascunhar` | É um cliente e a resposta está na base de conhecimento | Rascunho em Rascunhos, categoria `IA-Rascunhado` |
| `escalar` | É um cliente mas não sabemos responder, ou o pedido exige dados da conta dele, ou é sensível | Categoria `Precisa de humano`, sem rascunho |
| `saltar` | Não é correspondência de cliente | Nada, só fica registado |

"Saltar" e "escalar" são coisas diferentes e é importante que não se misturem. O
primeiro não precisa de ninguém, nunca; o segundo precisa de alguém hoje. E o
volume de escalações é a única métrica que diz se a base de conhecimento está a
chegar — se colapsasse em "saltar", perdia-se o sinal que diz o que acrescentar
ao `knowledge/`.

---

## Instalação

```bash
git clone <url>
cd Outlook-Reply-Assistant
python -m venv .venv && .venv\Scripts\activate      # Linux: . .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # Linux: cp .env.example .env
```

Requer Python 3.12 ou superior.

---

## Registo da aplicação no Microsoft Entra

1. **Entra admin center → App registrations → New registration.** Nome à escolha,
   inquilino único, sem redirect URI.
2. Guardar **Application (client) ID** e **Directory (tenant) ID**.
3. **Certificates & secrets → New client secret.** Copiar o valor imediatamente;
   deixa de ser visível.
4. **API permissions → Microsoft Graph → Application permissions → `Mail.ReadWrite`
   → Grant admin consent.**

Nunca `Mail.Send`. O pior caso possível deste projeto tem de ser "um rascunho mau
que alguém apaga", nunca "um email errado enviado a um cliente". Isso não depende
do prompt, nem do modelo, nem da atenção de quem revê às três da manhã — depende
de uma permissão que não existe.

### Restringir o acesso a uma única caixa — passo obrigatório

`Mail.ReadWrite` como permissão de aplicação dá acesso a **todas** as caixas do
inquilino. Sem o passo seguinte, ficas com a chave do correio inteiro da empresa.

```powershell
New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId info@tripat3s.com -AccessRight RestrictAccess -Description "Assistente de rascunhos"
```

```powershell
Test-ApplicationAccessPolicy -Identity info@tripat3s.com -AppId <client-id>
```

Uma segunda verificação, com outra caixa qualquer, deve devolver `Denied`. É o
passo mais importante do projeto e o mais fácil de esquecer.

O `verificar.py` prova o mesmo a partir do lado da aplicação, que é o que
interessa: tenta ler outra caixa e **reprova se conseguir**.

```bash
python verificar.py --outra-caixa geral@tripat3s.com
```

O endereço tem de ser de uma caixa que exista mesmo no inquilino. Uma que não
exista devolve 404 e não prova nada — o script diz isso em vez de dar a
verificação por boa.

### Se o Microsoft 365 foi comprado através de um revendedor

Um inquilino cujo domínio interno seja do tipo `NETORGFT…….onmicrosoft.com` foi
provisionado através da GoDaddy — é o caso da tripat3s. O acesso ao Entra admin
center pode estar limitado consoante o plano, e o registo de aplicações pode
exigir um pedido ao suporte do revendedor.

Confirmar **antes** de agendar a instalação: entrar em `entra.microsoft.com` com a
conta de administrador e verificar se se chega a *App registrations → New
registration*. Se não se chegar, o bloqueio é comercial e não técnico.

### Autenticação de email — verificar antes de ligar

Este projeto produz respostas que a equipa envia a partir da caixa. Se o domínio
não autenticar, essas respostas vão para o spam e o trabalho todo não serve de
nada.

```bash
nslookup -type=txt tripat3s.com                        # SPF
nslookup -type=cname selector1._domainkey.tripat3s.com # DKIM
```

- O **SPF** tem de incluir `include:spf.protection.outlook.com`. Um domínio cujo
  MX aponta para `mail.protection.outlook.com` mas cujo SPF não inclui a
  Microsoft falha a autenticação em todo o correio que envia — e com `-all` no
  fim, falha de forma dura.
- O **DKIM** tem de estar ativado no portal Microsoft Defender, em *Email &
  collaboration → Policies → Email authentication settings*.

Desde 2024 o Gmail exige que pelo menos um dos dois passe, e a maioria dos
clientes de uma loja online usa Gmail.

---

## Configuração

| Variável | Omissão | Função |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chave da API |
| `GRAPH_TENANT_ID` | — | Directory (tenant) ID |
| `GRAPH_CLIENT_ID` | — | Application (client) ID |
| `GRAPH_CLIENT_SECRET` | — | Client secret |
| `SHOPIFY_STORE` | — | Domínio `*.myshopify.com` da loja |
| `SHOPIFY_CLIENT_ID` | — | Client ID da app privada (Dev Dashboard) |
| `SHOPIFY_CLIENT_SECRET` | — | Client secret da app privada |
| `MAILBOX` | — | Caixa a vigiar |
| `MODELO` | `claude-sonnet-5` | Modelo da chamada única |
| `KNOWLEDGE_DIR` | `knowledge` | Pasta de documentos `.md`/`.txt` |
| `BLOCKLIST_FILE` | `blocklist.txt` | Domínios bloqueados |
| `DB_FILE` | `assistente.db` | Cursor e registo de decisões |
| `MAX_BODY_CHARS` | `4000` | Corte do corpo enviado ao modelo |
| `DRY_RUN` | `true` | `true` não escreve nada na caixa |
| `COMPANY_NAME` | `a loja` | Aparece no prompt |
| `SIGNATURE` | `tripat3s` | Assinatura do rascunho |
| `DRAFTED_CATEGORY` | `IA-Rascunhado` | Categoria aplicada ao original |
| `ESCALATED_CATEGORY` | `Precisa de humano` | Categoria de escalação |
| `DRAFT_PREFIX` | aviso de revisão | Linha no topo de cada rascunho |

---

## Base de conhecimento

O assistente **só** pode afirmar o que estiver em `knowledge/`. Os ficheiros são
markdown que a equipa da loja edita sem tocar em código — é o único ponto de
manutenção que lhes entregas.

Regra: se não souberes, deixa de fora. Uma secção em falta faz o assistente
escalar; uma secção errada faz o assistente mentir a um cliente.

Onde as páginas do site se contradizem — prazo de reembolso, custo de portes, o
banner de 30 dias contra a política de 14 — o facto **não está** na base de
conhecimento, de propósito. O assistente escala em vez de escolher uma versão.
Cada contradição que a loja resolver é uma pergunta que ele passa a responder
sozinho.

---

## Primeira execução

0. `python verificar.py --outra-caixa <outra caixa real>` — tem de passar tudo
   antes de se ligar seja o que for.
1. `DRY_RUN=true` no `.env` (é o valor por omissão).
2. `python assistente.py` — corre uma passagem e sai.
3. Ler o log. Cada mensagem mostra a decisão e a regra que a produziu.
4. Passar uma semana assim, ajustando `blocklist.txt` e o `knowledge/`.
5. Só então `DRY_RUN=false`.

A primeira passagem coloca o cursor no instante atual e ignora o histórico:
responder a um ano de arquivo seria caro e errado. Se precisar de recomeçar do
zero, apague o `assistente.db`.

Depois, de 2 em 2 minutos. Nunca um processo permanente — os ficheiros de
agendamento estão em `deploy/`.

### Windows — para a fase de testes

PowerShell **como administrador**, na pasta do projeto:

```powershell
.\deploy\agendar-windows.ps1
```

Regista a tarefa `tripat3s-assistente`, de 2 em 2 minutos, sem janela de consola
a piscar. Os logs ficam em `logs/assistente-AAAA-MM.log`.

```powershell
Start-ScheduledTask -TaskName tripat3s-assistente        # correr já
Get-ScheduledTask   -TaskName tripat3s-assistente        # estado
Disable-ScheduledTask -TaskName tripat3s-assistente      # parar
Unregister-ScheduledTask -TaskName tripat3s-assistente -Confirm:$false
```

Se a máquina estiver desligada durante a noite não se perde nada: o cursor fica
onde estava e a passagem seguinte apanha tudo o que chegou entretanto.

### Linux — para produção

```bash
sudo cp deploy/tripat3s-assistente.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tripat3s-assistente.timer
```

Assume o projeto em `/opt/assistente` e um utilizador `assistente`. Ajustar no
`.service` se for outro sítio.

```bash
systemctl list-timers tripat3s-assistente.timer   # próxima passagem
journalctl -u tripat3s-assistente -f              # logs em direto
sudo systemctl disable --now tripat3s-assistente.timer
```

O timer usa `OnUnitActiveSec`, e não `OnCalendar`, para que duas passagens nunca
se sobreponham se uma demorar mais do que o intervalo. No Windows, o equivalente
é o `MultipleInstances IgnoreNew`.

Durante a primeira semana cada rascunho começa com a linha
`--- rascunho automático · rever e apagar esta linha ---`. Se ela aparecer num
email enviado a um cliente, ficas a saber no próprio dia que ninguém está a
rever. Custo: zero. Esvazia-se o `DRAFT_PREFIX` quando a revisão estiver
estabelecida.

---

## Testes e avaliação

```bash
python -m unittest test_assistente     # 74 testes, sem rede nem credenciais
python eval.py --triagem               # regras determinísticas, grátis
python eval.py                         # tudo, contra o modelo real
```

Os testes provam que o código faz o que foi escrito. O `eval.py` prova que o
**prompt** faz o que é preciso, e é a única forma de saber se uma alteração ao
prompt ou ao `knowledge/` melhorou ou piorou o comportamento.

Três números, com pesos diferentes:

| Número | Significado | Alvo |
|---|---|---|
| **Clientes perdidos** | Casos que deviam gerar rascunho ou escalação e foram descartados. Não deixam rasto nenhum em produção | **Zero.** Reprova a execução sozinho |
| **Recall de escalação** | Dos que deviam escalar, quantos escalaram. Baixo significa que respondeu ao que não sabia | O mais alto possível |
| **Precisão de escalação** | Dos que escalaram, quantos deviam | Secundário |

Uma falha técnica é marcada `ERRO`, fica fora da aritmética e reprova a execução.
Sem isso, uma chave expirada daria "recall 100%" — todos os casos por responder
escalam, e escalar parece correto.

Os 13 casos que vêm no repositório são todos verdadeiros independentemente das
políticas da loja. **Faltam os casos `rascunhar`** — esses dependem das políticas
e têm de sair de emails reais da caixa.

### Exportar emails reais

```bash
python exportar.py --quantos 200
python eval.py --casos eval/real-2026-08.json
```

**Só lê.** Não escreve, não marca, não move nem apaga nada na caixa, e não faz
uma única chamada ao Claude — correr isto não custa nada.

Grava em `eval/real-AAAA-MM.json`, que o `.gitignore` exclui: mesmo anonimizada,
aquela é correspondência de clientes. Cada caso sai com `expect` vazio, para ser
etiquetado à mão, e com dois campos de apoio — `_triagem` diz se a triagem o
descartaria, `_palpite` dá um palpite por palavras-chave.

Ao mesmo tempo conta a distribuição real dos tipos de email, que é a pergunta que
decide o âmbito deste projeto: se a maioria for sobre estado de encomendas, o
assistente escala quase tudo e a conversa a ter com o cliente é outra.

**A anonimização é pseudonimização, não garantia.** Substitui o que se reconhece
por padrão — endereços, telefones, NIF, IBAN, códigos postais, números longos — e
o nome do remetente onde aparecer no corpo. Um nome escrito a meio de uma frase
pode escapar. O domínio do remetente é preservado de propósito: é o que a triagem
lê, e sem ele os casos não testariam nada.

---

## Custos

Para 30 emails/dia, com a triagem a descartar a parte automática antes de custar
alguma coisa:

| Rubrica | Mensal |
|---|---|
| Servidor (Hetzner CX22 ou equivalente) | ~4 € |
| Inferência (Sonnet 5, com cache do prompt) | ~10 € |
| **Total operacional** | **~14 €/mês** |

O `claude-haiku-4-5` é cerca de três vezes mais barato, mas o mínimo de prefixo
para o cache dele são 4096 tokens e a base de conhecimento é menor do que isso —
nunca chegaria a ser cacheada. Com uma só chamada por email, e a triagem a
filtrar o lixo de graça, a diferença real é de poucos euros.

---

## Decisões de desenho

**Uma chamada, três ações.** O modelo classifica e redige no mesmo passo. Separar
em duas chamadas duplicava latência para separar o que ele já faz de uma vez.

**A triagem determinística fica.** São ~70 linhas que não custam nada e evitam uma
chamada ao modelo por cada newsletter. É também onde vive a proteção anti-ciclo:
sem ela, um out-of-office de um fornecedor e este assistente respondem-se um ao
outro indefinidamente.

**Sem filtro de "não lidas".** Numa caixa a ser trabalhada, o operador abre o
email minutos depois de chegar. Filtrar por não lidas faria desaparecer
precisamente os emails em que alguém está a trabalhar agora — e o produto é
justamente o rascunho já estar lá quando ele abre o Outlook. O SQLite garante que
não se repete.

**O registo é indexado pelo `internetMessageId`, não pelo `id` do Graph.** O `id`
tem âmbito de pasta e é reatribuído quando a mensagem muda de sítio, portanto um
registo indexado por ele deixa silenciosamente de fazer correspondência assim que
alguém arruma a caixa de entrada.

**O modelo devolve texto simples; o HTML é construído aqui.** O corpo deriva de um
email não confiável. Escapar texto é uma linha; sanitizar HTML de terceiros são
cinquenta e nunca fica fechado. Uma resposta de duas a quatro frases não precisa
de mais.

**Uma falha técnica não marca o email.** Fica por processar para a passagem
seguinte tentar outra vez. Nunca se perde um email por causa de um timeout.

**O corpo do rascunho fica gravado no SQLite.** Uma vez por semana compara-se com
o que foi realmente enviado na mesma conversa. Acima de 60% editado, o rascunho é
ruído e vale a pena desligar.

---

## Âmbito — o que este assistente não faz

- **Não sabe o estado das encomendas.** Sem ligação ao sistema de encomendas,
  "onde está a minha encomenda?" cai sempre em `escalar`. Se essas forem a maioria
  do volume, o valor entregue é pequeno — **é a pergunta a fazer antes de tudo o
  resto**, e responde-se contando os tipos de pergunta em 100 emails de arquivo.
- **Não envia.** Por construção.
- **Não aprende.** Melhora quando alguém edita o `knowledge/`. É intencional: o
  mecanismo de melhoria tem de ser legível por um humano.

## Riscos

- **O corpo do email é input não confiável.** Sem permissão de envio, o dano está
  contido a um rascunho que alguém apaga.
- **RGPD.** O assistente processa correspondência de clientes. É preciso um acordo
  de subcontratação escrito *antes* do arranque, não depois.
- **Deriva silenciosa.** Em três meses ninguém sabe se ainda serve. A medição
  semanal do rascunho contra o enviado existe para isso.
- **Dependência de uma pessoa.** Um ficheiro Python que só uma pessoa percebe é
  uma dependência dessa pessoa. Para desligar: parar o timer. Mais nada quebra.
