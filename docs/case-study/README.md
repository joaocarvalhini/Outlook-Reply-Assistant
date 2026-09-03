---
title: Case study — como regenerar
type: reference
status: implemented
tags:
  - case-study
  - build
---

# Case study — como regenerar

> **Pergunta que este documento responde:** como se altera e volta a gerar o PDF do case study?

## Ficheiros

| Ficheiro | O que é |
|---|---|
| `case-study.html` | **A fonte.** É aqui que se edita o conteúdo e o desenho |
| `fonts.css` | As três famílias em base64. Gerado, não se edita à mão |
| `build-fonts.py` | Descarrega as fontes do Google e escreve o `fonts.css` |
| `build.py` | Gera o `case-study.pdf` e as imagens em `png/` |
| `case-study-print.html` | Intermediário, criado pelo `build.py`. Não editar |
| `case-study.pdf` | O entregável para o LinkedIn — 11 páginas, 1080×1350 |
| `png/` | Uma imagem por página a 144 dpi, para publicar como carrossel |
| `post-linkedin.md` | O texto que acompanha a publicação |

## Regenerar

```bash
python docs/case-study/build.py
```

Precisa do Chrome instalado. O `pypdf` e o `pymupdf` são opcionais — sem eles o
PDF sai na mesma, mas salta-se a verificação e as imagens.

As fontes só precisam de ser descarregadas outra vez se mudarem as famílias ou
os pesos usados:

```bash
python docs/case-study/build-fonts.py
```

Depois é preciso voltar a colar o conteúdo do `fonts.css` no primeiro bloco
`<style>` do `case-study.html`.

## Três coisas que partem isto

Estão todas resolvidas no código, mas voltam a morder se alguém mexer sem saber:

**1 · As fontes têm de estar embutidas.** O Chrome em modo headless não vai
buscar fontes remotas ao gerar o PDF: com um `<link>` para o Google Fonts, o
documento sai inteiro em Segoe UI e a tipografia perde-se sem dar erro. Estão
em base64 dentro do próprio HTML por isso.

> A verificação do `build.py` conta as fontes **Type3** — é assim que o Chrome
> embute fontes web, convertendo-as em procedimentos vetoriais. Uma contagem
> baixa de Type3 significa que o documento caiu nas fontes do sistema.

**2 · A área utilizável da folha não são os 1350px.** É cerca de 1320. Cada
página está fixada em **1315px** de altura: acima disso, cada uma empurra uma
folha em branco a seguir e o PDF sai com 13 páginas em vez de 11. A faixa que
sobra tem a cor do papel, por isso não se vê.

**3 · A quebra é *antes* de cada página, não depois.** Com `break-after` na
última página sobrava sempre uma folha em branco no fim. Usa-se
`.page + .page { break-before: page }`.

## Se o PDF sair com o número de páginas errado

O `build.py` falha com um erro explícito. As causas prováveis, por ordem:

1. Alguém acrescentou conteúdo a uma página e ela passou dos 1315px
2. A altura em `@media print` foi mexida
3. Uma `<section class="page">` nova sem o `break-before` a funcionar

Para ver qual página transbordou, abre o `case-study.html` no browser e corre
na consola:

```js
[...document.querySelectorAll('.page')].map((p,i)=>({
  pg: i+1, transborda: p.querySelector('.body').scrollHeight - p.querySelector('.body').clientHeight
})).filter(x => x.transborda > 0)
```

## Regra de conteúdo

Os números do documento foram verificados contra a base de dados de produção e
o código a **3 de setembro de 2026**, e a capa diz essa data. Não os atualizes
em silêncio: ou se atualizam todos e se muda a data, ou ficam como estão. Um
documento com números de datas diferentes não é verificável por ninguém.

A distinção entre **Medido**, **Estimado** e **Decisão rejeitada** é o que
separa isto de marketing. Onde não há medição, o documento diz que não há.
