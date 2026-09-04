---
title: Como publicar o case study no LinkedIn
type: reference
status: implemented
tags:
  - case-study
  - linkedin
---

# Como publicar o case study no LinkedIn

> **Pergunta que este documento responde:** o que se publica, onde, e por que ordem?

## Dois artefactos, não um

| Ficheiro | Onde vai | Porquê |
|---|---|---|
| Um dos `carrossel*.pdf` | **Post de documento no feed** | É o que é legível no telemóvel |
| `case-study.pdf` (15 páginas) | **Destaques do perfil** | É para quem clica no perfil depois |

Há três carrosséis e publica-se **um**. Dois posts com o mesmo conteúdo na
mesma rede competem um com o outro e partem o sinal da primeira hora ao meio.
A escolha está em [Que espinha escolher](#que-espinha-escolher) e
[Que idioma escolher](#que-idioma-escolher), no fim.

A razão de existirem dois está no cabeçalho do `carrossel.html`: a LinkedIn
rasteriza cada página do PDF e serve-a a 1080px, e num telemóvel o cartão do
post tem cerca de 390px. O slide é reduzido ~2,8×. O corpo de texto do
documento longo tem 15px, o que dá 5px no feed. Quem passa no feed veria
tipografia bonita e uma mancha cinzenta onde está o argumento.

O carrossel tem um piso tipográfico de 32px para corpo e 26px para rótulos,
que é o mínimo para se ler sem abrir em ecrã inteiro.

## Ordem de publicação

1. **Post de documento com o `carrossel.pdf`** e o texto abaixo por cima. É
   aqui que há alcance: as secções do perfil não têm distribuição nenhuma.
2. **Fixar o link do post nos Destaques**, e não o ficheiro. Assim o item no
   perfil leva os comentários agarrados. Um PDF subido outra vez para os
   Destaques fica órfão e compete com o post.
3. **Entrada em Projetos**, opcional, com o mesmo link. Descreve o sistema,
   nunca o negócio: os Destaques e os Projetos ficam indexados, ao contrário
   do post, e "loja de acessórios" mais as datas chega para identificar o
   cliente a quem o conheça.

## Três regras que valem mais do que o texto

**Sem link externo no corpo do post.** O estudo de maior amostra que
encontrei (1,3 milhões de posts) dá −18,8% de alcance mediano com um link. O
truque de o pôr no primeiro comentário terá sido fechado no início de 2026. Se
alguém pedir o repositório, responde-se num comentário.

**A primeira hora decide.** A plataforma testa o post em 2 a 5% da rede e é
essa amostra que determina o resto. Publicar e sair estraga o post. As
respostas preparadas no fim deste ficheiro existem para isso.

**Responder rápido conta.** Responder a comentários nos primeiros 30 minutos
está associado a 64% mais comentários e 2,3× mais visualizações. Um save vale
cerca de 5× um like.

As hashtags deixaram de distribuir: o ranker lê o texto. As quatro no fim não
fazem mal, também não fazem nada.

---

## Versão principal (~200 palavras)

> A melhor otimização que fiz este mês foi uma que decidi não implementar. 👇
>
> Construí um agente que lê a caixa de apoio ao cliente de uma loja online e
> escreve rascunhos de resposta. Não envia nada: a aplicação nunca pediu a
> permissão de envio.
>
> Achei que a parte difícil ia ser a qualidade da escrita. Foi o custo.
>
> Depois de instrumentar, a decomposição foi contraintuitiva: a **escrita** de
> cache era 52% da conta. Uma taxa de acerto de 89% parecia ótima, mas escrever
> custa um múltiplo do que custa ler, e por isso os 11% de falhas custavam
> 2,4× mais do que todos os acertos somados.
>
> Isso levou-me a uma descoberta: havia duas entradas de cache, não uma.
> Unificá-las cortaria metade da escrita. Óbvio.
>
> Antes de o fazer, testei. Custou $0,008 e mostrou que a mudança levava cada
> chamada de 5,3s para 67,9s, acima do timeout. Teria falhado em **todas** as
> chamadas. Teria parado o atendimento inteiro.
>
> Segunda lição do mesmo projeto: 66% dos emails escalavam para uma pessoa, e
> eu queria baixar esse número. Li os motivos um a um: quase todos estavam
> certos. Baixá-lo exigia dar ao agente permissões de escrita que o tornariam
> inseguro.
>
> A métrica certa não era quantos casos automatizei. Era quantos consegui
> automatizar com segurança.
>
> Dez slides em anexo, com os números e os erros.
>
> #AIEngineering #LLM #SystemDesign #Python

---

## Versão curta (~90 palavras)

Para quem prefere que o documento faça o trabalho todo.

> Passei um mês a construir um agente de apoio ao cliente com um LLM. Achei que
> a parte difícil ia ser a qualidade da escrita.
>
> Foi o custo, as regras de negócio, e decidir o que **não** automatizar.
>
> Três coisas que a medição contrariou:
>
> · 89% de acerto de cache parecia ótimo, e os 11% de falhas custavam 2,4× mais
> do que todos os acertos juntos
> · A otimização óbvia teria parado o atendimento. $0,008 para descobrir
> · 66% dos emails escalavam para uma pessoa, e quase todos com razão
>
> Dez slides em anexo, com os números e os erros.
>
> #AIEngineering #LLM #SystemDesign

---

## Se alguém perguntar nos comentários

Respostas curtas para as perguntas previsíveis.

**"Porque não usaste um framework de agentes?"**
Porque o sistema tem de ser mantido por uma pessoa sozinha. São quatro
dependências de runtime e zero dependências de teste. Cada ausência (framework
web, ORM, fila de mensagens, contentores) está justificada no
repositório.

**"Porque é que o modelo não envia os emails?"**
A aplicação nunca pediu a permissão de envio ao Microsoft Graph. Não é uma
regra no código que alguém possa contornar: é uma permissão que não existe.
O pior resultado possível de qualquer falha é um rascunho errado que uma
pessoa lê e apaga.

**"66% de escalação não é mau?"**
Foi a pergunta que me ocupou um dia inteiro. Li os motivos um a um: pedem
escrita em sistemas a que o agente não tem, nem deve ter, acesso, ou
perguntam por estado que só existe na cabeça de uma pessoa. Testei a hipótese
mais promissora com dados reais e apenas 2 de 9 casos se podiam ter resolvido
sozinhos.

**"Que modelo usaste?"**
Claude Sonnet, com saída restringida por esquema JSON e cache de prompt. A
parte interessante não é o modelo, é o que fica de fora dele: identidade,
aritmética, triagem e validação vivem todas em código determinístico.

**"Onde está a versão completa?"**
São 15 páginas, com os incidentes de produção e o que cada um mudou. Está nos
Destaques do meu perfil.

---

## Versão inglesa do post

Para acompanhar o `carrossel-en.pdf`.

> The best optimization I made this month was one I decided not to ship. 👇
>
> I built an agent that reads an online store's customer support inbox and
> writes reply drafts. It sends nothing: the application never requested send
> permission.
>
> I thought the hard part would be the quality of the writing. It was the cost.
>
> After instrumenting it, the breakdown was counterintuitive: cache **writes**
> were 52% of the bill. An 89% hit rate looked great, but writing costs a
> multiple of what reading costs, so the 11% of misses cost 2.4× more than
> every hit combined.
>
> That led me to something I did not know: there were two cache entries, not
> one. Unifying them would cut half the writes. Obvious.
>
> Before doing it, I tested. It cost $0.008 and showed the change took each
> call from 5.3s to 67.9s, past the timeout. It would have failed on **every**
> call. It would have stopped support entirely.
>
> Second lesson from the same project: 66% of emails escalated to a person, and
> I wanted that number down. I read the reasons one by one: almost all of them
> were right. Getting it down meant giving the agent write permissions that
> would make it unsafe.
>
> The right metric was never how many cases I automated. It was how many I
> could automate safely.
>
> Ten slides attached, with the numbers and the mistakes.
>
> #AIEngineering #LLM #SystemDesign #Python

### Comment answers, in English

**"Why not use an agent framework?"**
Because one person has to maintain this. Four runtime dependencies and zero
test dependencies. Every absence (web framework, ORM, message queue,
containers) is justified in the repository.

**"Why doesn't the model send the emails?"**
The application never requested send permission from Microsoft Graph. It is not
a rule in the code that someone can work around: it is a permission that does
not exist. The worst outcome of any failure is a wrong draft that a person
reads and deletes.

**"Isn't 66% escalation bad?"**
That question took me a full day. I read the reasons one by one: they ask for
writes to systems the agent does not have, and should not have, access to, or
they ask about state that only exists in someone's head. I tested the most
promising hypothesis against real data and only 2 of 9 cases could have been
resolved on their own.

**"Which model?"**
Claude Sonnet, with schema-constrained output and prompt caching. The
interesting part is not the model, it is what stays outside it: identity,
arithmetic, triage and validation all live in deterministic code.

---

## Que espinha escolher

O mesmo projeto contado de duas maneiras. Escolhe-se pela audiência, não pelo
gosto.

| | `carrossel.pdf` | `carrossel-base.pdf` |
|---|---|---|
| Espinha | O custo, e a otimização rejeitada | O que a loja sabe, e o que o modelo nunca toca |
| Capa | "A melhor otimização foi a que decidi não fazer" | "A parte difícil não foi o modelo" |
| Prova central | $0,008 de teste que travou um deploy | Duas regras verdadeiras que se contradizem |
| Fecho | Automatizar com segurança | O que está escrito à volta do modelo |
| Inglês | sim, `carrossel-en.pdf` | não |

`Recomendação:` **o do custo, para o feed.** "A otimização que decidi não
fazer" trava o scroll, e "escrevi mil linhas de regras de negócio" não trava.
A reviravolta e o número pequeno fazem o trabalho que um gancho tem de fazer.

**O da base de conhecimento serve melhor três situações:** uma conversa com um
cliente que quer perceber onde vai o dinheiro de um projeto destes; uma
entrevista técnica, onde a fronteira entre modelo e código é o assunto mais
substancial que este projeto tem; e uma segunda publicação daqui a umas
semanas, para quem já viu a primeira.

Vale a pena ter presente que o do custo ficou com a espinha mais fácil de
contar, não a mais importante. O que consumiu mais tempo no projeto foi a base
de conhecimento. O carrossel do custo dedica-lhe um slide em dez.

---

## Texto para o carrossel da base de conhecimento

> Passei mais tempo a escrever regras de negócio do que a escrever código. 👇
>
> Construí um agente que lê a caixa de apoio ao cliente de uma loja online e
> escreve rascunhos. Não envia nada: a aplicação nunca pediu a permissão de
> envio.
>
> Ligar o modelo à caixa demorou dias. O que demorou semanas foi a base de
> conhecimento: **1 088 linhas em 7 ficheiros**, a única fonte que ele pode
> citar.
>
> Um exemplo. "Quem paga o envio da devolução?" parece simples. A loja não
> emite etiqueta pré-paga, portanto num arrependimento paga o cliente. Mas num
> defeito confirmado a loja assume, e aí nem sequer é preciso devolver o artigo
> antigo primeiro.
>
> Duas regras verdadeiras sobre o mesmo assunto, que se contradizem lidas fora
> de contexto. Tiveram de ficar explicitamente ligadas uma à outra.
>
> E o custo de não o fazer não é o que se espera: uma base ambígua **não produz
> respostas erradas**. Produz escalações a mais, porque o agente é instruído a
> escalar na dúvida. Aparece na fatura, não na qualidade.
>
> A outra metade do trabalho foi decidir o que o modelo nunca pode tocar.
> Descobrir a encomenda de quem escreve dá quatro níveis de confiança e só dois
> libertam os dados para o prompt. O intermédio ficou de fora de propósito: é
> onde há indícios mas não há prova, e é exatamente aí que um engano mostra a
> encomenda de outra pessoa.
>
> Dez slides em anexo.
>
> #AIEngineering #LLM #SystemDesign #Python

As respostas de comentário da secção acima servem as duas versões.

---

## Que idioma escolher

`Decisão:` **português**, a não ser que o objetivo mude.

O primeiro teste do algoritmo é feito em 2 a 5% da rede de quem publica, e essa
rede é portuguesa. Um post em inglês para uma rede lusófona gera sinais fracos
na hora que decide tudo, e a partir daí não recupera. O ficheiro em inglês
existe para quando a rede justificar: uma audiência internacional já
construída, uma candidatura concreta a uma empresa que trabalha em inglês, ou
uma republicação passados uns meses noutro contexto.

O documento longo dos Destaques está em português. Se um dia o post passar a
inglês, esse também tem de passar, senão manda-se alguém de uma capa inglesa
para quinze páginas que não lê.
