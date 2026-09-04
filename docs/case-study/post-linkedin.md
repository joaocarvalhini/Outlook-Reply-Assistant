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
| `carrossel.pdf` (10 slides) | **Post de documento no feed** | É o que é legível no telemóvel |
| `case-study.pdf` (15 páginas) | **Destaques do perfil** | É para quem clica no perfil depois |

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
