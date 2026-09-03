#!/usr/bin/env python3
"""Gera o PDF do case study a partir do case-study.html.

    python build.py

O `case-study.html` é um fragmento (sem <!doctype>, <html> ou <body>) porque
é essa a forma que a plataforma de Artifacts espera. Para o Chrome o
fragmento também serve, mas embrulha-se num documento completo à mesma --
custa nada e tira ambiguidade do modo de renderização.

Requer o Chrome instalado e o `pypdf` (só para a verificação final).
As fontes têm de estar já embutidas: ver build-fonts.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).parent
FONTE = AQUI / "case-study.html"
INTERMEDIO = AQUI / "case-study-print.html"
PDF = AQUI / "case-study.pdf"

CHROMES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

PAGINAS_ESPERADAS = 14


def achar_chrome() -> str:
    for c in CHROMES:
        if Path(c).exists():
            return c
    print("Chrome não encontrado. Acrescenta o caminho a CHROMES.", file=sys.stderr)
    raise SystemExit(1)


def embrulhar() -> None:
    frag = FONTE.read_text(encoding="utf-8")
    cabeca, _, corpo = frag.partition('<div class="deck">')
    INTERMEDIO.write_text(
        '<!doctype html>\n<html lang="pt-PT">\n<head>\n<meta charset="utf-8">\n'
        f"{cabeca}</head>\n<body>\n"
        f'<div class="deck">{corpo}\n</body>\n</html>\n',
        encoding="utf-8",
    )


def render(chrome: str) -> None:
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=20000",
         f"--print-to-pdf={PDF}", INTERMEDIO.as_uri()],
        check=True, capture_output=True, timeout=300,
    )


def verificar() -> int:
    """As fontes embutidas saem como Type3 (o Chrome converte-as em
    procedimentos vetoriais). Uma contagem baixa de Type3 significa que o
    documento caiu nas fontes de recurso do sistema e a tipografia perdeu-se."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf não instalado -- salta a verificação.")
        return 0

    r = PdfReader(PDF)
    t3 = sistema = 0
    for p in r.pages:
        recursos = p.get("/Resources")
        fontes = recursos.get("/Font") if recursos else None
        for v in (fontes or {}).values():
            if str(v.get_object().get("/Subtype")) == "/Type3":
                t3 += 1
            else:
                sistema += 1

    print(f"{PDF.name}: {len(r.pages)} páginas, {PDF.stat().st_size // 1024} KB")
    print(f"  fontes embutidas (Type3): {t3} · fontes do sistema: {sistema}")

    erros = 0
    if len(r.pages) != PAGINAS_ESPERADAS:
        print(f"  ERRO: esperavam-se {PAGINAS_ESPERADAS} páginas.")
        erros += 1
    if t3 < sistema:
        print("  ERRO: a maioria do texto não está a usar as fontes embutidas.")
        erros += 1
    return erros


def exportar_png(dpi: int = 144) -> None:
    """Uma imagem por página, para quem quer publicar como carrossel de
    imagens em vez de documento PDF."""
    try:
        import pymupdf
    except ImportError:
        print("pymupdf não instalado -- salta as imagens.")
        return
    destino = AQUI / "png"
    destino.mkdir(exist_ok=True)
    doc = pymupdf.open(PDF)
    for i, pagina in enumerate(doc, 1):
        pagina.get_pixmap(dpi=dpi).save(destino / f"pagina-{i:02d}.png")
    print(f"  png/: {len(doc)} imagens a {dpi} dpi")


def main() -> int:
    chrome = achar_chrome()
    embrulhar()
    render(chrome)
    erros = verificar()
    exportar_png()
    return erros


if __name__ == "__main__":
    raise SystemExit(main())
