"""Order checks (N031, split from order.py to keep both files under the K11 size limit): formal verdict reading, the owner's
words, evidence freshness and the commit scope check. No state of its own: order.py calls these and re-exports their names."""
from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import family, find_root  # noqa: E402

ROOT = find_root()


def read_text_any(path: Path) -> str:
    """Text of a file written on Windows: UTF-8 with or without a BOM, or UTF-16 (PowerShell 5 `>` and Notepad's 'Unicode'
    write it); undecodable bytes are replaced, never fatal."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8-sig", errors="replace").lstrip("\ufeff")


VERDICT_LINE = re.compile(r"VERDICT:\s*(APPROVE|CHANGES|REJECT)\s*\.?")


def review_verdict(path: Path) -> str:
    """Verdict token of a review evidence file: JSON {"verdict": ...} (Ollama/ChatGPT) or text whose first line is 'VERDICT: X' (verifier2)."""
    if not path.is_file():
        return ""
    text = read_text_any(path)
    if path.suffix == ".json":
        try:
            d = json.loads(text)
            v = d.get("verdict")
            return (v.get("verdict") if isinstance(v, dict) else v or "").upper()
        except (json.JSONDecodeError, AttributeError):
            return ""
    # 1.2.0 (N031, "read the verdict formally"): a text review counts only when its FIRST non-empty line is exactly the verdict
    # line ('VERDICT: APPROVE'; a BOM, CRLF and a final full stop are tolerated, any other tail is not). The word anywhere else
    # (a quote, a table, 'VERDICT: APPROVE would be wrong', 'VERDICT: APPROVE, but CHANGES are needed') is not a verdict.
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    m = VERDICT_LINE.fullmatch(first)
    return m.group(1) if m else ""


def owner_words(a) -> str:
    """1.2.0 (N031): an owner confirmation (--by owner) must carry the owner's own words in --owner-text - a bare flag is not evidence."""
    return (getattr(a, "owner_text", None) or "").strip()


def require_owner_words(a) -> None:
    if a.verdict == "APPROVE" and family(a.by) == "owner" and not owner_words(a):
        raise SystemExit(f"{a.id}: --by owner needs --owner-text \"<the owner's words>\" (verbatim, journaled): the flag alone is not the owner's confirmation")


def owner_words_json(a) -> str:
    """The owner's words as ONE JSON string: verbatim (quotes, newlines and Cyrillic survive), and a line break inside them can
    neither start a fake journal event ('- 23:59 [N001] closed') nor a fake report field ('RUNG3: ...') (N031 review)."""
    return json.dumps(owner_words(a), ensure_ascii=False)


NEWEST_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache"})
NEWEST_SKIP_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})  # touched by Finder / Explorer, not by the work
NEWEST_SCAN_CAP = 20000


def newest_mtime(declared: list[str]) -> tuple[float, bool]:
    """1.2.0 (N031): mtime of the newest file the answer declares; a declared folder counts through the files under it (caches,
    VCS folders and OS litter skipped; at most NEWEST_SCAN_CAP files). (0.0, False) = nothing found; True = the scan was cut."""
    best, seen = 0.0, 0
    for f in declared:
        p = ROOT / f
        try:
            if p.is_file():
                best = max(best, p.stat().st_mtime); seen += 1
                continue
            if not p.is_dir():
                continue
            for dirpath, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in NEWEST_SKIP_DIRS]
                for n in names:
                    if n.lower() in NEWEST_SKIP_FILES:
                        continue
                    seen += 1
                    if seen > NEWEST_SCAN_CAP:
                        return best, True
                    try:
                        best = max(best, os.stat(os.path.join(dirpath, n)).st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return best, False


def scope_key(path: str, fold: bool = False, user: bool = True) -> str:
    """1.2.0 (N031): the comparable form of a path - project-relative, '/' separators, no './' or trailing '/', Unicode NFC
    (macOS stores Cyrillic decomposed), lower case when the file system ignores case. '.' = the project folder itself;
    '' = unusable (empty, or an absolute path outside the project). `user` = typed by a person (a Windows '\\' is a separator);
    git's own names are taken as they are."""
    x = unicodedata.normalize("NFC", (path or "").strip())
    if user:
        x = x.replace("\\", "/")
    if not x:
        return ""
    if os.path.isabs(x):
        try:
            x = Path(x).resolve().relative_to(ROOT.resolve()).as_posix()
        except (ValueError, OSError):
            return ""
    x = posixpath.normpath(x)
    x = "." if x == "." else x.strip("/")
    return x.casefold() if fold else x


def ignores_case() -> bool:
    """Windows, or git told us (core.ignorecase is set by `git init` from what the file system does - APFS too)."""
    if os.name == "nt":
        return True
    r = subprocess.run(["git", "config", "--get", "core.ignorecase"], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip().lower() == "true"


def covers(keys: set[str], rel: str) -> bool:
    return any(x == "." or rel == x or rel.startswith(x + "/") for x in keys if x)


def staged_names() -> list[str] | None:
    """Paths staged in the index under the project folder, relative to it; None = not a git repository (or git failed).
    Like git_tree (K34d): a project nested in a larger repository sees only its own folder (`--relative -- .`), never the
    parent repository's files, and no file-system path is computed (a junction, `subst` or a symlink cannot confuse it);
    -z keeps Cyrillic literal; --no-renames lists both sides of a rename."""
    try:
        res = subprocess.run(["git", "diff", "--cached", "--name-only", "--no-renames", "-z", "--relative", "--", "."], cwd=ROOT, capture_output=True)
    except OSError:
        return None
    if res.returncode != 0:
        return None
    return [x for x in res.stdout.decode("utf-8", errors="replace").split("\0") if x]


def staged_scope(allow: str | None, own_paths: list[str], named_paths: set[str]) -> tuple[list[str], list[str], list[str]]:
    """1.2.0 (N031, "staged = scope"): (refused, accepted, undeclared) among the files already staged. The scope is exactly what
    the commit takes (`own_paths`: order_paths plus the answer's declared files); `accepted` = outside it but named in --allow-extra
    (they stay staged, the pathspec commit does not take them); `undeclared` = refused ones that the order itself names
    (`named_paths`, its Deliverable) but the answer does not declare - they would be staged and silently NOT committed (N011)."""
    names = staged_names()
    if not names:
        return [], [], []
    fold = ignores_case()
    key = lambda x, user=True: scope_key(x, fold, user)  # noqa: E731
    own = {key(x) for x in own_paths}
    allowed = {key(x) for x in (allow or "").split(",") if x.strip()}
    outside = [n for n in names if not covers(own, key(n, user=False))]
    accepted = [n for n in outside if covers(allowed, key(n, user=False))]
    refused = [n for n in outside if n not in accepted]
    named = {key(x) for x in named_paths}  # (the "Context" section lists read-only files: a staged one is a stray)
    return refused, accepted, [n for n in refused if covers(named, key(n, user=False))]


def check_staged(order_id: str, scope: tuple[list[str], list[str], list[str]], journal, final: bool = False) -> None:
    """Before anything is written (`final` False) a staged file outside the order's scope stops `done --commit`. Right before the
    commit (`final`) only the journal and a warning remain: the pathspec commit is safe whatever is staged."""
    refused, accepted, undeclared = scope
    if final:
        if refused:
            print(f"{order_id}: staged outside the order's scope since the first check, left out of the commit: {'; '.join(refused[:5])}")
        if accepted:
            journal("left staged outside the commit (--allow-extra): " + "; ".join(accepted), order_id)
        return
    if refused:
        msg = (f"{order_id}: staged files outside the order's scope: {'; '.join(refused[:10])}" + (" ..." if len(refused) > 10 else "")
               + " - unstage them (git restore --staged <path>) or leave them staged knowingly: --allow-extra \"path,...\" "
               "(they stay staged and are NOT in this commit; journaled)")
        if undeclared:
            msg += (f". Named in the order but not declared in the answer, so they would be staged and NOT committed: "
                    f"{'; '.join(undeclared[:5])} - declare them with --files")
        raise SystemExit(msg)
