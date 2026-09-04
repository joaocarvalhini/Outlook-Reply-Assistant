#!/usr/bin/env python3
"""Gera os PDFs a partir do HTML.

    python build.py                 gera todos
    python build.py case-study      só o documento longo, 15 páginas
    python build.py carrossel       só o carrossel do feed, 10 slides
    python build.py carrossel-en    o mesmo carrossel, em inglês
    python build.py carrossel-base  o carrossel alternativo, 10 slides

Os HTML são fragmentos (sem <!doctype>, <html> ou <body>) porque é essa a
forma que a plataforma de Artifacts espera. Para o Chrome o fragmento também
serve, mas embrulha-se num documento completo à mesma -- custa nada e tira
ambiguidade do modo de renderização.

Um marcador <!--INCLUIR: ficheiro.css--> é substituído pelo conteúdo desse
ficheiro neste passo. É assim que as fontes e a folha de estilos partilhada
entram nos dois carrosséis sem ficarem duplicadas. O case-study.html tem as
fontes coladas lá dentro por razões históricas e passa intocado.
Ver build-fonts.py.

Requer o Chrome instalado e o `pypdf` (só para a verificação final).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).parent
INCLUIR = re.compile(r"<!--INCLUIR:\s*([\w.\-]+)\s*-->")

# nome -> (páginas esperadas, pasta das imagens, idioma)
DOCUMENTOS = {
    "case-study": (15, "png", "pt-PT"),
    "carrossel": (10, "png-carrossel", "pt-PT"),
    "carrossel-en": (10, "png-carrossel-en", "en"),
    "carrossel-base": (10, "png-carrossel-base", "pt-PT"),
}

CHROMES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def achar_chrome() -> str:
    for c in CHROMES:
        if Path(c).exists():
            return c
    print("Chrome não encontrado. Acrescenta o caminho a CHROMES.", file=sys.stderr)
    raise SystemExit(1)


def embrulhar(fonte: Path, intermedio: Path, idioma: str) -> None:
    # Sem as fontes embutidas o Chrome headless cai nas do sistema, em silêncio.
    frag = INCLUIR.sub(
        lambda m: (AQUI / m.group(1)).read_text(encoding="utf-8"),
        fonte.read_text(encoding="utf-8"),
    )
    cabeca, _, corpo = frag.partition('<div class="deck">')
    intermedio.write_text(
        f'<!doctype html>\n<html lang="{idioma}">\n<head>\n<meta charset="utf-8">\n'
        f"{cabeca}</head>\n<body>\n"
        f'<div class="deck">{corpo}\n</body>\n</html>\n',
        encoding="utf-8",
    )


def render(chrome: str, intermedio: Path, pdf: Path) -> None:
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=20000",
         f"--print-to-pdf={pdf}", intermedio.as_uri()],
        check=True, capture_output=True, timeout=300,
    )


def verificar(pdf: Path, esperadas: int) -> int:
    """As fontes embutidas saem como Type3 (o Chrome converte-as em
    procedimentos vetoriais). Uma contagem baixa de Type3 significa que o
    documento caiu nas fontes de recurso do sistema e a tipografia perdeu-se."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf não instalado -- salta a verificação.")
        return 0

    r = PdfReader(pdf)
    t3 = sistema = 0
    for p in r.pages:
        recursos = p.get("/Resources")
        fontes = recursos.get("/Font") if recursos else None
        for v in (fontes or {}).values():
            if str(v.get_object().get("/Subtype")) == "/Type3":
                t3 += 1
            else:
                sistema += 1

    print(f"{pdf.name}: {len(r.pages)} páginas, {pdf.stat().st_size // 1024} KB")
    print(f"  fontes embutidas (Type3): {t3} · fontes do sistema: {sistema}")

    erros = 0
    if len(r.pages) != esperadas:
        print(f"  ERRO: esperavam-se {esperadas} páginas.")
        erros += 1
    if t3 < sistema:
        print("  ERRO: a maioria do texto não está a usar as fontes embutidas.")
        erros += 1
    return erros


def exportar_png(pdf: Path, pasta: str, dpi: int = 144) -> None:
    """Uma imagem por página, para quem quer publicar como carrossel de
    imagens em vez de documento PDF."""
    try:
        import pymupdf
    except ImportError:
        print("pymupdf não instalado -- salta as imagens.")
        return
    destino = AQUI / pasta
    destino.mkdir(exist_ok=True)
    doc = pymupdf.open(pdf)
    for i, pagina in enumerate(doc, 1):
        pagina.get_pixmap(dpi=dpi).save(destino / f"pagina-{i:02d}.png")
    print(f"  {pasta}/: {len(doc)} imagens a {dpi} dpi")


def gerar(chrome: str, nome: str) -> int:
    esperadas, pasta, idioma = DOCUMENTOS[nome]
    fonte = AQUI / f"{nome}.html"
    intermedio = AQUI / f"{nome}-print.html"
    pdf = AQUI / f"{nome}.pdf"

    embrulhar(fonte, intermedio, idioma)
    render(chrome, intermedio, pdf)
    erros = verificar(pdf, esperadas)
    exportar_png(pdf, pasta)
    return erros


def main() -> int:
    pedidos = sys.argv[1:] or list(DOCUMENTOS)
    for nome in pedidos:
        if nome not in DOCUMENTOS:
            print(f"Documento desconhecido: {nome}. "
                  f"Conhecidos: {', '.join(DOCUMENTOS)}.", file=sys.stderr)
            return 1

    chrome = achar_chrome()
    erros = 0
    for nome in pedidos:
        erros += gerar(chrome, nome)
    return erros


if __name__ == "__main__":
    raise SystemExit(main())
