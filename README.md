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
  ├─ Graph: mensagens anteriores do mesmo fio, marcadas LOJA ou CLIENTE
  ├─ Shopify: resolve a encomenda por níveis de certeza, e só a revela quando
  │  a identidade está provada (client credentials grant, scope read_orders)
  ├─ Claude: 1 chamada → {"acao": "rascunhar"|"escalar"|"saltar", ...}
  └─ "rascunhar" → Graph createReply · "escalar" → categoria para humano
```

A Shopify só responde a perguntas de leitura (estado do pagamento, se foi
expedida, código de rastreio). Pedidos para cancelar, alterar ou reembolsar
continuam sempre a escalar — a app só tem `read_orders`, nunca escrita.

### Como se decide que a encomenda é mesmo daquela pessoa

Um número de encomenda não é segredo: aparece em emails reencaminhados, em
capturas de ecrã, na etiqueta da caixa. Revelar o estado de uma encomenda a
quem cita um número é expor dados de um cliente a outro. Por isso a procura tem
níveis, decididos em código e não pelo modelo:

| Nível | Situação | Confiança | Revela? |
|---|---|---|---|
| 1 | Número, e o email da compra é o do remetente | `exata` | Sim |
| 2 | Número, outro email, mas o nome completo, o telefone ou o código postal batem certo | `alta` | Sim |
| 3 | Sem número, e o email do remetente tem exatamente uma encomenda | `alta` | Sim |
| 4 | Só o número, sem mais nada que ligue as duas pontas | `media` | **Não** |
| 5 | Vários candidatos possíveis | `nenhuma` | **Não** |

O nível 4 é o que separa isto de um sistema descuidado: há uma encomenda
plausível, e mesmo assim não se diz nada. O modelo é informado de que ela
existe, para escalar com a categoria certa, mas não recebe um único dado dela.

O nível 3 é capacidade nova: antes, um cliente que escrevesse do email da
compra sem citar o número nunca era encontrado, apesar de ser o caso mais fácil
de todos.

O contexto do fio existe porque muitas respostas de cliente são curtas — "e
quando envia?", "por mim tudo bem", "enviei já" — e sozinhas não querem dizer
nada. Serve também de travão: o que a loja já prometeu no fio é um compromisso
assumido, e o rascunho parte daí em vez de o contradizer.

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
reprocessar.py       repassa decisões antigas pelo código de hoje, sem escrever
medir_deriva.py       compara rascunhos regenerados com o que o lojista respondeu
lacunas.py           a fila de lacunas de conhecimento e o peso de cada causa
dossie.py            casos escalados que já vêm preparados para quem decide
casos_antigos.py       pares pergunta-resposta do histórico, sem passar pelo modelo
metricas.py           taxa de escalação, categorias e risco dos dossiês
test_assistente.py   111 testes, biblioteca padrão, sem rede
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
| `THREAD_MESSAGES` | `8` | Mensagens anteriores do fio dadas ao modelo |
| `THREAD_CHARS` | `400` | Corte de cada mensagem do fio |
| `ENABLE_ORDER_IDENTITY_RESOLUTION` | `true` | Resolução da encomenda por níveis de certeza |
| `ENABLE_PRE_DRAFTS` | `true` | Preparar o caso quando escala um pedido acionável |
| `ENABLE_COMMITMENT_REGISTRY` | `true` | Registar e lembrar promessas feitas ao cliente |
| `ENABLE_PARTIAL_ANSWERS` | `true` | Responder à parte coberta de um email com vários assuntos |
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
python -m unittest test_assistente     # 102 testes, sem rede nem credenciais
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

Os casos que vêm no repositório são todos verdadeiros independentemente das
políticas da loja. Um caso pode trazer `dados_encomenda`, e nesse caso o ensaio
faz de conta que a consulta à Shopify devolveu aquilo — permite testar o caminho
com dados de encomenda sem depender da loja real.

### Reprocessar decisões passadas

```bash
python reprocessar.py --acao escalar            # todos os escalados
python reprocessar.py --acao escalar --detalhe  # com o corpo dos rascunhos novos
```

Responde a uma pergunta que o eval não responde: uma alteração ao prompt, à base
de conhecimento ou uma integração nova mudou alguma coisa nos **casos reais** que
já passaram por aqui? Vai buscar cada email à caixa pelo `Message-ID` e volta a
correr a passagem inteira. Nunca cria rascunhos nem marca categorias.

Duas cautelas ao ler o resultado. Mudar de decisão não é o mesmo que decidir
melhor: é preciso ler os rascunhos novos, porque um rascunho errado é pior do
que uma escalação. E o mesmo conjunto corrido duas vezes não dá o mesmo número —
já se viram 9, 11 e 6 mudanças com o mesmo código. Serve para encontrar padrões,
não para afinar contra o número.

### Escalar não é despachar

```bash
python dossie.py                    # casos à espera de decisão
python dossie.py --lista            # uma linha por caso, para escolher rápido
python dossie.py --caso 42          # só o caso #42, sem percorrer os outros
python dossie.py --risco alto       # só os que precisam de atenção primeiro
python dossie.py --tipo cancelamento
```

Um terço dos escalados nunca vai desaparecer: cancelar uma encomenda, decidir
uma garantia ou responder a uma disputa são decisões que a loja deve mesmo
tomar. Baixar a percentagem tem um chão. Tornar cada escalação barata para
quem decide não tem.

Quando o pedido é acionável, a escalação passa a trazer um dossiê: o que foi
confirmado, o que impede, a ação recomendada, o link direto para a encomenda no
admin, e a resposta ao cliente já redigida à espera de aprovação.

```
CANCELAMENTO   ·   risco baixo

  Cliente pede o cancelamento da encomenda #10482, feita hoje por engano.
  A encomenda ainda não foi expedida.

  Validação
    ✓ encomenda encontrada e identidade confirmada pelo email da compra
    ✓ ainda não foi expedida, dá para cancelar
    ✗ o pagamento já foi capturado e terá de ser devolvido

  Ação recomendada (exige aprovação de uma pessoa)
    Cancelar a encomenda e devolver os 49,90 EUR pelo mesmo método.
```

Não se prepara dossiê em dois casos, ambos deliberados: quando a escalação é
por falta de conhecimento, porque não há nada a preparar quando o assistente
não sabe a resposta; e quando a identidade não está confirmada, porque preparar
o caso obrigaria a usar dados de uma encomenda que pode não ser de quem
escreveu.

O dossiê fica gravado no registo local (lê-se com `dossie.py`, útil para
consulta e para as métricas) e, quando existe uma resposta sugerida, essa
resposta **também** é escrita diretamente como rascunho no Outlook — decisão
explícita do cliente: não quer que o lojista precise de correr nenhum comando
para ver um caso escalado, e quer que o rascunho seja só o email, sem nada
à volta. Sem resumo, sem validação, sem categoria — só o texto que se enviaria,
pronto a rever e ajustar.

Quando não há dossiê (falta de conhecimento, identidade por confirmar), não há
rascunho nenhum — só a categoria marcada no email, como sempre foi.

### Um assunto descoberto não deita fora o resto

Um email real raramente traz um assunto só: "onde está a encomenda, veio com
defeito e já agora fazem desconto?". Até agosto de 2026, bastava um desses
temas não estar na base de conhecimento para o email inteiro escalar — e a
resposta ao rastreio, que o assistente sabia dar, ia ao lixo com ele.

Agora o assistente escreve a parte que sabe e regista o que ficou de fora no
campo `por_responder`, numa frase para o colega. **O corpo do rascunho nunca
menciona a parte que ficou por responder**: nem a prometer, nem a recusar, nem
a dizer que alguém responde depois. Essa parte não existe para o cliente.

O que torna isto seguro é a marca dupla: um rascunho parcial leva a categoria
de rascunhado **e** a de "precisa de humano". Sem isso ficava na fila dos
rascunhos completos e alguém enviava-o a responder a meio email. Assim é o que
deve ser — meio trabalho feito para quem revê, em vez de uma folha em branco.

Se não souber responder a nada, escala como sempre. Isto não é uma licença para
responder por alto: é uma licença para não deitar fora o que já sabe.

### Propor não é comprometer

Regra relacionada, no prompt. Um caso à espera de uma ação da loja continua a
escalar — mas *perguntar* ao cliente se aceita o passo seguinte que a base de
conhecimento prescreve não é assumir essa ação:

| Pode escrever | Escala |
|---|---|
| "Aceita que lhe enviemos um novo?" | "Vamos enviar-lhe um novo na segunda." |
| "Pode enviar-nos uma fotografia?" | "Confirmamos o reembolso de 49,90 €." |

A diferença é entre uma pergunta e um compromisso com data ou valor. Sem esta
distinção, o caso mais comum da loja — defeito confirmado, e a troca sem custo
é sempre a primeira oferta — escalava sempre, apesar de a resposta estar
escrita na base de conhecimento.

### Compromissos que sobrevivem ao fio

`THREAD_MESSAGES` só dá ao modelo as últimas mensagens do fio. Uma promessa
feita há semanas — "enviamos um par novo assim que recebermos o antigo" — pode
já ter saído dessa janela quando o cliente volta a escrever. Sem registo
próprio, o assistente esqueceria a promessa e trataria o pedido como novo.

Cada decisão pode gravar um compromisso (tipo, descrição, estado, data) numa
tabela `compromissos` à parte do fio, indexada por conversa. Um compromisso
novo do mesmo tipo substitui o anterior em vez de duplicar; "concluído" ou
"cancelado" deixam de contar como pendente. A regra mais importante: **nunca
se inventa nem se estima uma data** — sem confirmação explícita da loja, o
campo fica vazio, mesmo que o modelo pudesse "adivinhar" um prazo plausível.

Quando existe um compromisso pendente para a conversa, entra no prompt como
contexto e influencia a categoria (`COMPROMISSO_ANTERIOR`): um cliente a
perguntar pelo estado de algo já prometido não é uma pergunta nova, é um
follow-up a uma decisão que a loja já tomou.

### Porque é que cada email escalou

```bash
python lacunas.py --categorias   # peso de cada causa de escalação
python lacunas.py                # lacunas de conhecimento por fechar
```

Cada escalação traz uma categoria de uma lista fixa, além do motivo em palavras.
O motivo é para o colega que pega no caso; a categoria é o que se conta. Sem
identificadores estáveis, medir o efeito de uma alteração obriga a classificar
texto livre com expressões regulares, que não é reproduzível.

| Categoria | Fecha-se com |
|---|---|
| `DADOS_ENCOMENDA_EM_FALTA` | Melhor resolução de identidade, ou o cliente dar o número |
| `IDENTIDADE_NAO_VERIFICADA` | Nada. É a salvaguarda a funcionar |
| `ENCOMENDA_ANTIGA` | Scope `read_all_orders`, que exige aprovação da Shopify |
| `INVENTARIO_INDISPONIVEL` | Scope `read_products`, que exige aprovação da Shopify |
| `CONTEXTO_EM_FALTA` | Mais mensagens do fio, ou fios que o Graph não agrupa |
| `LACUNA_DE_CONHECIMENTO` | Escrever o facto em `knowledge/`, depois de o confirmar |
| `ACAO_SOBRE_ENCOMENDA` | Nada, por desenho. A app não tem escrita |
| `JULGAMENTO_HUMANO` | Escrever a política que a loja já pratica de facto. O que sobra é a fronteira do que se delega |
| `COMPROMISSO_ANTERIOR` | Nada, por desenho. É o registo de compromissos a funcionar |
| `OUTRO` | Rever periodicamente: se crescer, falta uma categoria |

Quando a causa é falta de conhecimento, o modelo não escreve "não sei": produz
o tema e a informação concreta que falta, e é isso que alimenta a fila. O que
ele produz é a pergunta, nunca a resposta — escalou precisamente por não saber,
e transformar a suposição dele em facto seria o pior erro possível na base.

### Números, não só casos

```bash
python metricas.py              # últimos 30 dias
python metricas.py --dias 7     # só a última semana
python metricas.py --tudo       # desde sempre
```

`dossie.py` e `lacunas.py` mostram casos individuais; `metricas.py` mostra a
proporção entre rascunhar, escalar e saltar, a categoria de cada escalação e o
risco dos dossiês preparados. Não faz chamadas à API nem à caixa — lê só o que
já está no registo local, por isso corre em qualquer altura sem custo.

É o número que decide se a arquitetura está a cumprir o objetivo: a taxa de
escalação a descer sem o recall de escalação descer com ela — recall baixo
significa que o assistente passou a responder ao que não sabia, não que ficou
melhor.

### Medir a deriva contra respostas reais

```bash
python medir_deriva.py                      # rascunhos já gravados como "rascunhar"
python medir_deriva.py --incluir-escalados  # tenta também os que ficaram "escalar"
python medir_deriva.py --so-numero          # só a tabela, sem os textos
```

Regenera o rascunho de cada email já marcado `rascunhar` com o código de hoje
(não o texto gravado, que pode ser de antes da última correção), vai buscar a
resposta que o lojista realmente enviou a seguir na mesma conversa, e mostra as
duas lado a lado. Um número de semelhança (0-100%, `difflib`) serve só para
ordenar por onde começar a ler — não é nota de qualidade.

Com `--incluir-escalados`, faz o mesmo para os emails que na altura escalaram:
se o contexto do fio, a Shopify ou uma correção ao prompt já resolveriam o
caso, mostra o que o assistente escreveria hoje a par do que o lojista
respondeu de facto — casos que nunca passaram por um rascunho real.

Duas armadilhas reais, já encontradas:
- Um rascunho criado manualmente fora do fluxo normal (por exemplo para o
  cliente ver a qualidade) pode aparecer como se fosse "a resposta real" —
  a função ignora mensagens que comecem pelo `DRAFT_PREFIX`, mas qualquer outro
  texto colocado à mão na caixa passa despercebido.
- A "resposta real" é a próxima mensagem da loja na mesma conversa, que pode
  estar a responder a outra pergunta do mesmo fio, não à que gerou o rascunho.
  Semelhança baixa não é prova de rascunho mau; é preciso ler.

Enquanto a caixa está em `DRY_RUN` há poucas conversas fechadas para comparar —
a amostra cresce à medida que o lojista vai respondendo aos fios reais. Faz
sentido correr isto periodicamente, não uma vez só.

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
