"""Markdown -> PDF for the user's reading folder (Cyrillic-safe; reportlab + DejaVu fonts shipped with matplotlib).

    md2pdf.py path/to/file.md [out.pdf]        # default: same name, .pdf, next to the .md

Supports: headings, paragraphs, bullet / numbered lists, pipe tables, code blocks, bold/italic/inline code, rules.
Deterministic, 0 tokens. Own implementation (the owner's invest project has a similar exporter; this one is
written from scratch to stay dependency-light)."""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import re
import sys
from html import escape
from pathlib import Path


def _fonts():
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    d = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    for name, f in (("DV", "DejaVuSans.ttf"), ("DV-B", "DejaVuSans-Bold.ttf"), ("DV-I", "DejaVuSans-Oblique.ttf"), ("DV-M", "DejaVuSansMono.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(d / f)))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-B")


EMOJI_MAP = {"✅": "[ok]", "❌": "[x]", "⚠": "[!]", "⚠️": "[!]", "✔": "[v]", "✘": "[x]", "→": "->", "←": "<-", "•": "-", "🚨": "[!!]", "📌": "[*]", "🔴": "[red]", "🟢": "[green]"}


def inline(text: str) -> str:
    """Inline markdown -> reportlab markup. R17: code spans are protected first (a `*` inside code no longer opens italics);
    emoji that the PDF font lacks are mapped to ASCII markers instead of tofu boxes."""
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    text = "".join(ch for ch in text if ord(ch) < 0x1F000 or ord(ch) > 0x1FAFF)  # drop remaining emoji glyphs
    codes: list[str] = []

    def stash(m):
        codes.append(m.group(1)); return f"\x00{len(codes) - 1}\x00"
    t = re.sub(r"`([^`]+)`", stash, text)
    t = escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: f'<font face="DV-M" size="8.5">{escape(codes[int(m.group(1))])}</font>', t)


def convert(md_path: Path, out: Path) -> Path:
    _fonts()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

    S = {"h1": ParagraphStyle("h1", fontName="DV-B", fontSize=16, leading=20, spaceAfter=6, spaceBefore=6),
         "h2": ParagraphStyle("h2", fontName="DV-B", fontSize=12.5, leading=16, spaceAfter=4, spaceBefore=8, textColor=colors.HexColor("#1f3864")),
         "h3": ParagraphStyle("h3", fontName="DV-B", fontSize=10.5, leading=14, spaceAfter=3, spaceBefore=6),
         "p": ParagraphStyle("p", fontName="DV", fontSize=9.5, leading=13, spaceAfter=4),
         "li": ParagraphStyle("li", fontName="DV", fontSize=9.5, leading=13, leftIndent=12, bulletIndent=2, spaceAfter=1),
         "cell": ParagraphStyle("cell", fontName="DV", fontSize=8, leading=10),
         "code": ParagraphStyle("code", fontName="DV-M", fontSize=7.5, leading=9.5, backColor=colors.HexColor("#f4f4f4"), leftIndent=4)}
    story, table, code, para = [], [], None, []
    width = A4[0] - 30 * mm

    def flush_para():
        if para:
            story.append(Paragraph(inline(" ".join(para)), S["p"])); para.clear()

    def flush_table():
        nonlocal table
        if table:
            rows = [r for r in table if not re.match(r"^\s*\|?\s*:?-{2,}", r)]
            cells = [[Paragraph(inline(c.strip()), S["cell"]) for c in r.strip().strip("|").split("|")] for r in rows]
            n = max(len(r) for r in cells)
            cells = [r + [Paragraph("", S["cell"])] * (n - len(r)) for r in cells]
            # R17: column widths proportional to content (min 8 % each), not equal
            raw_cols = [[c.strip() for c in r.strip().strip("|").split("|")] + [""] * n for r in rows]
            weights = [max(8.0, min(60.0, sum(len(rc[i]) for rc in raw_cols) / max(1, len(raw_cols)))) for i in range(n)]
            total = sum(weights)
            t = Table(cells, colWidths=[width * w / total for w in weights], repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8c8c8")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
            story.append(t); story.append(Spacer(1, 4)); table = []

    for line in md_path.read_text(encoding="utf-8").splitlines():
        if code is not None:
            if line.startswith("```"):
                story.append(Preformatted("\n".join(code), S["code"])); story.append(Spacer(1, 4)); code = None
            else:
                code.append(line)
            continue
        if line.startswith("```"):
            flush_para(); flush_table(); code = []; continue
        if line.lstrip().startswith("|"):
            flush_para(); table.append(line); continue
        flush_table()
        if not line.strip():
            flush_para(); continue
        if line.startswith("#"):
            flush_para(); level = min(len(line) - len(line.lstrip("#")), 3)
            story.append(Paragraph(inline(line.lstrip("#").strip()), S[f"h{level}"])); continue
        if re.match(r"^\s*(---|\*\*\*)\s*$", line):
            flush_para(); story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c8c8c8"))); continue
        m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", line)
        if m:
            flush_para(); bullet = "•" if m[1] in "-*" else m[1]
            story.append(Paragraph(inline(m[2]), S["li"], bulletText=bullet)); continue
        if line.startswith(">"):
            flush_para(); story.append(Paragraph("<i>" + inline(line.lstrip("> ")) + "</i>", S["p"])); continue
        para.append(line.strip())
    flush_para(); flush_table()
    out.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                      title=md_path.stem).build(story)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 2
    if sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    src = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else src.with_suffix(".pdf")
    if out.suffix.lower() != ".pdf" or out == src:  # producer quest 2026-09-22: `md2pdf a.md b.md` wrote PDF bytes into b.md
        print(f"refused: the output must be a .pdf file, got {out.name} (one file per call: md2pdf.py doc.md [doc.pdf])")
        return 2
    print(convert(src, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
