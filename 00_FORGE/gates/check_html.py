"""Static checks of an HTML deliverable (R28; behavioural checks stay with a browser run):

    check_html.py page.html            # exit 1 with a list of problems, 0 when clean (notes do not fail)
    check_html.py page.html --offline  # the page must work with no network: any external script or stylesheet fails
    check_html.py page.html --seo      # also title 30-60 and meta description 70-160 characters, exactly one <h1>

Checks: UTF-8 + <meta charset>, viewport meta, size <= 512 KB, external resources. Since 0.9.2 (N012 live run: the motion
card asks for GSAP from cdnjs, the old rule failed every such page): a script from a known CDN (cdnjs, jsdelivr /npm/,
unpkg, code.jquery.com) pinned to an exact version, and stylesheets/fonts from Google Fonts, are a NOTE (the page needs the
network for them); an unpinned version or any other host is a problem; with --offline every external resource is a problem.
Inline event handlers pointing to missing functions are NOT checked here (browser check). Deterministic, 0 tokens."""
from __future__ import annotations

import re
import sys
from pathlib import Path

CDN = re.compile(r"^https://(cdnjs\.cloudflare\.com/ajax/libs/|cdn\.jsdelivr\.net/npm/|unpkg\.com/|code\.jquery\.com/)", re.I)
FONTS = re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/", re.I)
PINNED = re.compile(r"(@|/|-)v?\d+\.\d+\.\d+([-.][\w.]+)?(/|\.|$)")  # gsap/3.12.5/..., react@18.3.1, jquery-3.7.1.min.js


def check(path: Path, offline: bool = False) -> tuple[list[str], list[str]]:
    """-> (problems, notes)."""
    raw = path.read_bytes(); problems, notes = [], []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ["not UTF-8"], []
    low = text.lower()
    if "<meta charset" not in low and "charset=" not in low:
        problems.append("no <meta charset=\"utf-8\">")
    if "viewport" not in low:
        problems.append("no viewport meta (mobile layout)")
    if len(raw) > 512 * 1024:
        problems.append(f"{len(raw) // 1024} KB > 512 KB")
    for m in re.finditer(r"<(script|link)[^>]+(src|href)=[\"'](https?://[^\"']+)", text, re.I):
        kind, url = m[1].lower(), m[3]
        allowed = (kind == "script" and CDN.match(url) and PINNED.search(url.split("?")[0])) or \
                  (kind == "link" and (FONTS.match(url) or (CDN.match(url) and PINNED.search(url.split("?")[0]))))
        if offline or not allowed:
            why = "--offline: the page must work without a network" if offline else \
                  "unknown host or unpinned version (allowed: cdnjs / jsdelivr /npm/ / unpkg / code.jquery.com with an exact version, Google Fonts)"
            problems.append(f"external {kind}: {url[:70]} — {why}")
        else:
            notes.append(f"external {kind} from a pinned CDN: {url[:70]} (needs the network)")
    if "<script" in low and "</script>" not in low:
        problems.append("unclosed <script>")
    if low.count("<html") > 1:
        problems.append("more than one <html> — several documents pasted together?")
    return problems, notes


SEO = {"title": (30, 60), "meta description": (70, 160)}  # search-result truncation conventions, not a standard


def seo(path: Path) -> tuple[list[str], list[str]]:
    """--seo (0.9.2, marketing card; N012: a 19-character title was called "pass" with no criterion): title and meta
    description lengths against SEO (characters after entity decoding), exactly one <h1>. -> (problems, measurements)."""
    import html as _html
    text = path.read_text(encoding="utf-8", errors="replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    # the value ends at the SAME quote it opened with: content="Don't ..." keeps its apostrophe (N013 verifier2 #4)
    meta = re.search(r"<meta\s+[^>]*name=[\"']description[\"'][^>]*content=([\"'])(?P<v>.*?)\1", text, re.S | re.I) or \
        re.search(r"<meta\s+[^>]*content=([\"'])(?P<v>.*?)\1[^>]*name=[\"']description[\"']", text, re.S | re.I)
    vals = {"title": title, "meta description": meta}
    problems, seen = [], []
    for k, (lo, hi) in SEO.items():
        m = vals[k]
        if not m:
            problems.append(f"SEO: no {k}"); continue
        val = m.group("v") if "v" in m.re.groupindex else m.group(1)
        n = len(re.sub(r"\s+", " ", _html.unescape(val)).strip())
        seen.append(f"{k} {n} chars (convention {lo}-{hi})")
        if not lo <= n <= hi:
            problems.append(f"SEO: {k} is {n} characters, convention {lo}-{hi}")
    h1 = len(re.findall(r"<h1[\s>]", text, re.I))
    seen.append(f"h1 x{h1}")
    if h1 != 1:
        problems.append(f"SEO: {h1} <h1> elements, expected exactly 1")
    return problems, seen


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__); return 2
    p = Path(args[0])
    problems, notes = check(p, offline="--offline" in sys.argv)
    if "--seo" in sys.argv:
        sp, seen = seo(p)
        problems += sp; notes.append("SEO measured: " + "; ".join(seen))
    for n in notes:
        print(f"note: {n}")
    print("\n".join(f"- {x}" for x in problems) if problems else f"{p.name}: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
