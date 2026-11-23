"""Figures discipline of an analysis document (K32: verify line of the analytics and invest domains) - 0 tokens.

    check_figures.py report.md                 # every markdown table with figures has a source and a period
    check_figures.py report.md --strict        # + a type column (fact / calc / consensus / scenario), and every row
                                               #   with figures has its own source, period and type
    check_figures.py report.md --allow-rating  # the owner asked for a rating: rating wording is not flagged
    check_figures.py report.md --json

Period = a named column (period / период / date / FY / год / quarter ...) OR period headers (2024, FY2024, Q3 2024,
H1 2025, 9M2024, LTM, TTM) - then the year columns ARE the figures. A table "has figures" when a cell outside its
source / named-period / type columns contains a digit. Rating wording (BUY / HOLD / SELL in capitals, "Rating: Buy",
overweight / outperform as a rating, price target, "рекомендуем покупать") is flagged outside code blocks; a price target
inside a table row typed consensus is allowed (it is someone else's number, labelled). A column headed Rating / Рейтинг
with Buy, Overweight ... in its cells is flagged too. Rating detection is a heuristic: free wording such as "we rate it
Overweight" can pass - the reviewer still reads the text. Exit 1 when anything is flagged.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
import sys
from pathlib import Path

SOURCE = re.compile(r"\b(sources?|источник\w*|src|ссылк\w*|links?|filings?|документ\w*)\b", re.I)  # words: "Resources" is not a source
PERIOD_WORDS = {"period", "период", "date", "дата", "fy", "fiscal", "year", "год", "quarter", "квартал", "as", "of", "на", "дату",
                "end", "конец", "reporting", "отчётный", "отчетный"}
PERIOD_HEAD = re.compile(r"(?<!\d)((fy|q[1-4]|h[12]|[1-9]m)\s*'?)?(19|20)\d{2}(e|f|п)?(?!\d)|\b(ltm|ttm)\b", re.I)


def period_name(h: str) -> bool:
    """A named period column: every word of the header is period vocabulary ("Period", "FY", "Fiscal year end", "На дату")."""
    words = re.findall(r"\w+", h.lower())
    return bool(words) and all(w in PERIOD_WORDS for w in words)
TYPE = re.compile(r"^\W*(type|тип|kind|вид)\W*$", re.I)
# columns derived from the dated ones or notes - they take the period of the year columns (verifier2 r3 #2)
DERIVED = re.compile(r"%|Δ|\b(yoy|y/y|qoq|cagr|change|growth|diff|delta|notes?|comment|изменени\w*|рост|прирост|разниц\w*|примечани\w*|комментари\w*)\b", re.I)
TYPES = {"fact", "calc", "calculation", "consensus", "scenario", "факт", "расчёт", "расчет", "консенсус", "сценарий"}
EMPTY = {"", "-", "—", "–", "?", "n/a", "na", "н/д", "нет", "none", "tbd"}
RATING_WORDS = r"(strong\s+)?(buy|sell|hold|overweight|underweight|outperform|underperform|accumulate|reduce|покупать|продавать|держать)"
RATING = re.compile(r"\b(STRONG\s+)?(BUY|SELL|HOLD)\b"
                    r"|(?i:\b(rating|рейтинг|recommendation|рекомендация)\s*[:=\-–—]\s*" + RATING_WORDS + ")"
                    r"|(?i:рекоменд\w*\s+(к\s+)?(покупать|покупке|купить|продавать|продаже|продать|держать))")
RATING_LABEL = re.compile(r"^\W*(rating|рейтинг|recommendation|рекомендация)\W*$", re.I)
RATING_CELL = re.compile(r"^\W*" + RATING_WORDS + r"\W*$", re.I)
TARGET = re.compile(r"(?i:price target|target price|целев\w* цен\w*)")
# a clause that says a rating is ABSENT is not a rating (N012 live run: "рейтинга и целевой цены нет" was flagged).
# Only the clause around the match counts (N012 verifier2 r1 #1: "price target is 250 (not advice)" must still fail).
CLAUSE_END = re.compile(r"[.:;!?()\[\],—–|]|\s-\s|(?i:(?<!\w)(but|however|но|однако|а)(?!\w))")  # "|": a table cell is a clause


WORD = re.compile(r"\w+")
AFTER_NEG = re.compile(r"(?i)нет|отсутствует|отсутствуют")  # Russian puts it after; "BUY no matter what" stays flagged (r3)
NEG1 = {"нет", "без", "no", "not", "never", "without"}
NE_VERBS = {"даём", "даем", "выдаём", "выдаем", "приводим", "ставим", "даётся", "дается"}
# words allowed between the negation and the term: "we do NOT give a BUY rating", "NO BUY/HOLD/SELL", "НЕ даём рейтинг BUY"
FILLER = {"a", "an", "the", "any", "own", "our", "give", "gives", "issue", "issues", "provide", "provides", "assign", "rating",
          "ratings", "recommendation", "buy", "sell", "hold", "strong", "рейтинг", "рейтинга", "рейтингов", "оценку",
          "рекомендацию", "собственной", "своей", "и"}


def negated(line: str, m: re.Match) -> bool:
    """True when the negation governs the match in its clause: a negation word (or "не" + даём/ставим/...) followed only by
    filler words up to the term ("no BUY", "we do not give a BUY rating"), or "нет"/"отсутствует" right after it ("целевой цены
    нет"). A negation of something else does not count: "BUY and no regrets" (N012 r2), "we recommend not only BUY",
    "there is no doubt BUY is right" (N013 rung 3 #2) all stay flagged."""
    cuts = [x.end() for x in CLAUSE_END.finditer(line, 0, m.start())]
    tail = CLAUSE_END.search(line, m.end())
    start, end = (cuts[-1] if cuts else 0), (tail.start() if tail else len(line))
    words = [w.lower() for w in WORD.findall(line[start:m.start()])][-6:]
    for i in range(len(words) - 1, -1, -1):
        rest = None
        if words[i] in NEG1:
            rest = words[i + 1:]
        elif words[i] == "не" and i + 1 < len(words) and words[i + 1] in NE_VERBS:
            rest = words[i + 2:]
        if rest is not None:
            if all(w in FILLER for w in rest):
                return True
            break  # the nearest negation governs something else
    after = WORD.findall(line[m.end():end])[:1]
    return bool(after and AFTER_NEG.fullmatch(after[0]))
FENCE = re.compile(r"^\s*(```|~~~)")
SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def tables(lines: list[str]):
    """(start line number, header cells, data rows as (line number, cells)) for each table outside code fences."""
    i, fence = 0, False
    while i < len(lines):
        if FENCE.match(lines[i]):
            fence = not fence; i += 1; continue
        if not fence and "|" in lines[i] and i + 1 < len(lines) and SEP.match(lines[i + 1]):
            head, rows, j = cells(lines[i]), [], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip() and not FENCE.match(lines[j]):
                rows.append((j + 1, cells(lines[j]))); j += 1
            yield i + 1, head, rows
            i = j; continue
        i += 1


def blank(row: list[str], k: int) -> bool:
    return k >= len(row) or row[k].strip().lower() in EMPTY


def check(text: str, strict: bool = False, allow_rating: bool = False) -> list[str]:
    lines = text.lstrip("\ufeff").splitlines()
    out = []
    for start, head, rows in tables(lines):
        src = [k for k, h in enumerate(head) if SOURCE.search(h)]
        per = [k for k, h in enumerate(head) if period_name(h)]
        per_heads = [k for k, h in enumerate(head) if k not in per and PERIOD_HEAD.search(h)]
        typ = [k for k, h in enumerate(head) if TYPE.match(h)]
        skip = set(src + per + typ)
        if not allow_rating:  # | Company | Rating | ... | X | Buy | - a rating column (N010 rung 3 #3)
            for k in [k for k, h in enumerate(head) if RATING_LABEL.match(h)]:
                out += [f"line {n}: rating '{r[k].strip()}' in the {head[k]} column - no rating unless the owner asked (--allow-rating)"
                        for n, r in rows if k < len(r) and RATING_CELL.match(r[k])]
        fig_rows = [(n, r) for n, r in rows if any(re.search(r"\d", c) for k, c in enumerate(r) if k not in skip)]
        if not fig_rows:
            continue
        if not src:
            out.append(f"line {start}: table with figures has no source column (source / источник)")
        if not per and not per_heads:
            out.append(f"line {start}: table with figures has no period (a period / период / date / FY column, or years as column headers)")
        elif not per:  # years as headers date only their own columns; other figure columns need a period too (N010 rung 3 #2)
            undated = sorted({k for _, r in fig_rows for k, c in enumerate(r) if k > 0 and k not in skip and k not in per_heads
                              and not (k < len(head) and DERIVED.search(head[k])) and re.search(r"\d", c)})
            if undated:
                names = ", ".join(head[k] if k < len(head) else f"#{k + 1}" for k in undated)
                out.append(f"line {start}: column(s) {names} hold figures without a period - only the year columns are dated")
        if strict:
            if not typ:
                out.append(f"line {start}: --strict: no type column (fact / calc / consensus / scenario)")
            for n, r in fig_rows:
                if src and all(blank(r, k) for k in src):
                    out.append(f"line {n}: row with figures has no source")
                if per and all(blank(r, k) for k in per):
                    out.append(f"line {n}: row with figures has no period")
                if typ and (blank(r, typ[0]) or r[typ[0]].strip().lower() not in TYPES):
                    out.append(f"line {n}: type '{r[typ[0]] if typ[0] < len(r) else ''}' is not fact / calc / consensus / scenario")
    if not allow_rating:
        heads = {start for start, _, _ in tables(lines)}  # a "Price target" column header is a label, not a target
        fence = False
        for n, line in enumerate(lines, 1):
            if FENCE.match(line):
                fence = not fence; continue
            if fence or n - 1 in heads:  # n - 1: the separator line under a header
                continue
            row = cells(line) if "|" in line else []
            # every match counts unless a negation sits next to it in its clause; a table cell is a clause ("|" ends it,
            # 0.9.2: "| invest | Рейтинга и целевой цены нет |" was flagged); a Rating column with a Buy cell never is exempt
            m = next((x for x in RATING.finditer(line) if not negated(line, x)), None)
            if row and not m and any(RATING_LABEL.match(c) for c in row):  # | Rating | Buy | (N010 rung 3 #3)
                m = next((re.search(r".+", c.strip()) for c in row if RATING_CELL.match(c)), None)
            t = None if n in heads else next((x for x in TARGET.finditer(line) if not negated(line, x)), None)
            consensus_row = any(c.strip().lower() in ("consensus", "консенсус") for c in row)
            if m or (t and not consensus_row):
                out.append(f"line {n}: rating wording '{(m or t).group(0)}' - no rating or own price target unless the owner asked (--allow-rating)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="figures discipline: source + period (+ type) for every table with figures; no ratings")
    ap.add_argument("file"); ap.add_argument("--strict", action="store_true"); ap.add_argument("--allow-rating", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    p = Path(a.file)
    if not p.is_file():
        raise SystemExit(f"not a file: {p}")
    probs = check(p.read_text(encoding="utf-8", errors="replace"), a.strict, a.allow_rating)
    if a.json:
        print(json.dumps({"file": str(p), "ok": not probs, "problems": probs}, ensure_ascii=False))
    else:
        print("\n".join(probs) if probs else f"OK {p.name}: figures have source and period" + (" and type" if a.strict else ""))
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
