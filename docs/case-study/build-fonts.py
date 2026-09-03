#!/usr/bin/env python3
"""Descarrega as fontes do Google e escreve-as embutidas em fonts.css.

O Chrome em modo headless não vai buscar fontes remotas ao gerar o PDF: sem
isto, o documento sai em Segoe UI e toda a tipografia se perde. Embutir em
base64 torna o HTML autossuficiente -- funciona offline, no PDF e no browser.

Só o subconjunto `latin` é descarregado: cobre U+00C0-00FF, que é onde vivem
os acentos do português (ã, ç, õ, é, ê, í, ú, â).

    python build-fonts.py
"""
from __future__ import annotations

import base64
import re
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Só os pesos e estilos que o documento usa de facto -- cada face a mais são
# dezenas de KB de base64 sem nada os pedir.
FAMILIAS = [
    ("Newsreader",     "ital,opsz,wght@0,6..72,600;1,6..72,500"),
    ("Archivo",        "wght@400;500;600"),
    ("JetBrains+Mono", "wght@400;500;700"),
]

BLOCO = re.compile(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{.*?\})", re.S)
URL_WOFF = re.compile(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)")


def buscar(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def main() -> int:
    partes: list[str] = [
        "/* Gerado por build-fonts.py -- não editar à mão. */\n"
    ]
    total = 0
    for familia, eixo in FAMILIAS:
        css = buscar(f"https://fonts.googleapis.com/css2?family={familia}:{eixo}&display=swap").decode()
        blocos = BLOCO.findall(css)
        if not blocos:
            print(f"  {familia}: nenhum bloco @font-face -- URL rejeitado?")
            return 1
        usados = 0
        for subconjunto, regra in blocos:
            if subconjunto != "latin":
                continue
            m = URL_WOFF.search(regra)
            if not m:
                continue
            dados = buscar(m.group(1))
            total += len(dados)
            usados += 1
            b64 = base64.b64encode(dados).decode("ascii")
            # Substitui só o URL dentro do url(...), não o url(...) inteiro:
            # a regra já traz " format('woff2')" a seguir, e duplicá-lo
            # invalida o descritor src -- a face é ignorada em silêncio e o
            # documento cai na fonte de recurso.
            partes.append(
                regra.replace(m.group(1), f"data:font/woff2;base64,{b64}")
            )
        print(f"  {familia}: {usados} face(s) embutida(s)")

    destino = Path(__file__).with_name("fonts.css")
    destino.write_text("\n".join(partes), encoding="utf-8")
    print(f"\nfonts.css: {destino.stat().st_size // 1024} KB "
          f"({total // 1024} KB de woff2 antes do base64)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
