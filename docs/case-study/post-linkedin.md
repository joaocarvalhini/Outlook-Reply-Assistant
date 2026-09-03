---
title: Texto para acompanhar o case study no LinkedIn
type: reference
status: implemented
tags:
  - case-study
  - linkedin
---

# Texto para acompanhar a publicação

Três versões. O PDF (`case-study.pdf`) vai anexado como documento — o texto
abaixo é o que aparece por cima dele.

---

## Versão principal — ~200 palavras

> A melhor otimização que fiz este mês foi uma que decidi não implementar. 👇
>
> Construí um agente que lê a caixa de apoio ao cliente de uma loja online e
> escreve rascunhos de resposta. Não envia nada — a aplicação nunca pediu a
> permissão de envio.
>
> Achei que a parte difícil ia ser a qualidade da escrita. Foi o custo.
>
> Depois de instrumentar, a decomposição foi contraintuitiva: a **escrita** de
> cache era 52% da conta. Uma taxa de acerto de 89% parecia ótima, mas escrever
> custa um múltiplo do que custa ler — os 11% de falhas custavam 2,4× mais do
> que todos os acertos somados.
>
> Isso levou-me a uma descoberta: havia duas entradas de cache, não uma.
> Unificá-las cortaria metade da escrita. Óbvio.
>
> Antes de o fazer, testei. Custou $0,008 e mostrou que a mudança levava cada
> chamada de 5,3s para 67,9s — acima do timeout. Teria falhado em **todas** as
> chamadas. Teria parado o atendimento inteiro.
>
> Segunda lição do mesmo projeto: 67% dos emails escalavam para uma pessoa, e
> eu queria baixar esse número. Li os motivos um a um — quase todos estavam
> certos. Baixá-lo exigia dar ao agente permissões de escrita que o tornariam
> inseguro.
>
> A métrica certa não era quantos casos automatizei. Era quantos consegui
> automatizar com segurança.
>
> O case study completo está no documento em anexo.
>
> #AIEngineering #LLM #SystemDesign #Python

---

## Versão curta — ~90 palavras

Para quem prefere que o documento faça o trabalho todo.

> Passei um mês a construir um agente de apoio ao cliente com um LLM. Achei que
> a parte difícil ia ser a qualidade da escrita.
>
> Foi o custo, as regras de negócio, e decidir o que **não** automatizar.
>
> Três coisas que a medição contrariou:
>
> · 89% de acerto de cache parecia ótimo — os 11% de falhas custavam 2,4× mais
> do que todos os acertos juntos
> · A otimização óbvia teria parado o atendimento. $0,008 para descobrir
> · 67% dos emails escalavam para uma pessoa — e quase todos com razão
>
> 11 páginas em anexo, com os números e os erros.
>
> #AIEngineering #LLM #SystemDesign

---

## Se alguém perguntar nos comentários

Respostas curtas para as perguntas previsíveis.

**"Porque não usaste um framework de agentes?"**
Porque o sistema tem de ser mantido por uma pessoa sozinha. São quatro
dependências de runtime e zero dependências de teste. Cada ausência —
framework web, ORM, fila de mensagens, contentores — está justificada no
repositório.

**"Porque é que o modelo não envia os emails?"**
A aplicação nunca pediu a permissão de envio ao Microsoft Graph. Não é uma
regra no código que alguém possa contornar: é uma permissão que não existe.
O pior resultado possível de qualquer falha é um rascunho errado que uma
pessoa lê e apaga.

**"67% de escalação não é mau?"**
Foi a pergunta que me ocupou um dia inteiro. Li os motivos um a um: pedem
escrita em sistemas a que o agente não tem — nem deve ter — acesso, ou
perguntam por estado que só existe na cabeça de uma pessoa. Testei a hipótese
mais promissora com dados reais e apenas 2 de 9 casos se podiam ter resolvido
sozinhos.

**"Que modelo usaste?"**
Claude Sonnet, com saída restringida por esquema JSON e cache de prompt. A
parte interessante não é o modelo — é o que fica de fora dele: identidade,
aritmética, triagem e validação vivem todas em código determinístico.
