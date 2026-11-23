#!/usr/bin/env python3
"""weaken_scan.py - report-only detector of check weakening in a diff (N031, research finding E-02).

On ImpossibleBench the Claude models bypass tests they cannot satisfy mostly by editing the tests. This tool reads a git diff
(working tree against --base, default HEAD, or only the files an order declared) and lists signals of weakened checks:
deleted or emptied test files, removed test functions, skip/xfail/only markers, removed or loosened assertions, new lint/type
suppressions, stub bodies in non-test code, loosened or removed check configuration (strict off, coverage threshold lowered
or removed, a limit raised, a CI test step dropped).

It never blocks: the exit code is always 0 (a usage error of the command line aside). The owner measures the false-positive
rate before any of this becomes a gate. A clean report is never claimed without a check: when the scope cannot be narrowed
(no answer yet, an unreadable answer) the whole working tree is scanned and the note says so; when the diff cannot be read or
parsed the note says that instead of a silent "0 signal(s)".

    weaken_scan.py [--base REF] [--order Nnnn | --files a,b] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SNIPPET_MAX = 160
UNTRACKED_MAX = 1 << 20  # untracked files above 1 MB are not read
BINARY_PROBE = 8192      # an untracked file with a NUL in its first bytes is binary and is not read as text

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_HEAD = re.compile(r"^diff --git a/(.*?) b/(.*)$")

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".c", ".cc",
            ".cpp", ".h", ".hpp", ".swift", ".sh", ".ps1"}
SLASH_COMMENT_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp",
                     ".swift", ".php", ".json", ".jsonc"}
TEST_DIRS = ("tests", "test", "__tests__", "spec", "specs", "e2e")
CONFIG_NAMES = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", ".coveragerc", "ruff.toml", ".ruff.toml", "mypy.ini",
                ".mypy.ini", "package.json", ".flake8", ".pylintrc", "pyrightconfig.json", "noxfile.py"}
CONFIG_PREFIXES = (".eslintrc", "eslint.config.", "tsconfig", "jest.config.", "vitest.config.", "karma.conf.", "phpunit.xml")

TEST_FUNC = re.compile(r"^\s*(?:async\s+def\s+|def\s+)(test\w*)\s*\(|^\s*func\s+(Test\w+)\s*\(|^\s*(?:it|test)\s*\(\s*['\"`]([^'\"`]+)")
# A skip marker is a statement or a decorator, so it is anchored at the start of the statement: the same words inside a string
# fixture, a comment or `model.fit(...)` are not markers.
SKIP_MARKER = re.compile(
    r"(?:^|[;:{}])\s*(?:"
    r"@unittest\.skip\w*|unittest\.skip\w*\(|self\.skipTest\(|@pytest\.mark\.(?:skip\w*|xfail)\b|pytest\.(?:skip|xfail)\(|"
    r"(?:it|describe|test|context)\.(?:skip|todo|only|fixme)\b|(?:xit|xdescribe|xtest|fit|fdescribe)\s*\(|t\.Skip(?:Now)?\(|"
    r"@(?:Disabled|Ignore)\b|#\[ignore\])")
ASSERT_LINE = re.compile(
    r"^\s*(?:assert\b|self\.assert\w+\(|assert_\w+\(|expect\(|\bassert\w*\(|require\.\w+\(|t\.(?:Error|Fatal)f?\(|"
    r"(?:async\s+)?with\s+(?:self\.assert\w+|pytest\.(?:raises|warns|deprecated_call))\b)")
WEAK_ASSERT = re.compile(
    r"self\.assert(?:True|IsNotNone|In|NotIn|Greater\w*|Less\w*|IsInstance|NotEqual|Regex)\(|^\s*assert\s+True\b|^\s*assert\s+1\b|"
    r"\.toBeTruthy\(|\.toBeDefined\(|\.toBeDefined\b|\.not\.toBeNull\(|\.toBeGreaterThan\w*\(|\.toBeInstanceOf\(|\.toContain\(|"
    r"^\s*assert\s+\w+\s+is\s+not\s+None\b|^\s*assert\s+\w+\s*$|^\s*assert\s+len\(|"
    r"^\s*assert\s+[\w.]+(?:\([^=<>!]*\))?\s*(?:,.*)?$")  # `assert f()` - a bare truth test, no comparison
TRIVIAL_ASSERT = re.compile(r"^\s*assert\s+(?:True|1|\"[^\"]+\"|'[^']+')\s*(?:,.*)?$|self\.assertTrue\(\s*True\s*\)|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)")
# Comment-style markers are anchored on a comment opener and checked against string literals (see _in_string); annotation
# markers on the start of the line.
SUPPRESSION = re.compile(
    r"#\s*noqa\b|#\s*type:\s*ignore|#\s*pyright:\s*ignore|#\s*pylint:\s*disable|#\s*pragma:\s*no\s+cover|#\s*nosec\b|#\s*fmt:\s*skip|"
    r"(?:#|//|/\*|<!--)\s*(?:eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|nolint|istanbul\s+ignore)|"
    r"^\s*#!?\[allow\(|^\s*@SuppressWarnings")
NOQA_CODES = re.compile(r"#\s*noqa\s*:\s*([A-Za-z]+\d+(?:\s*[, ]\s*[A-Za-z]+\d+)*)")
BENIGN_NOQA = {"E402"}  # an import after sys.path.insert is the plugin's idiom, not a weakened check
# A stub is "hard" when it says outright that the code is not written; "soft" stubs (pass, ..., return None, TODO) are everyday
# code (except: pass, early return None, Protocol bodies) and count only as a new function's whole body or as the replacement
# of real code.
HARD_STUB = re.compile(
    r"^\s*(?:raise\s+NotImplementedError\b.*|throw\s+new\s+Error\(\s*['\"`](?:not\s+implemented|todo)[^)]*\)\s*;?|"
    r"unimplemented!\(.*\)\s*;?|todo!\(.*\)\s*;?|panic\(\s*\"(?:not implemented|todo)[^)]*\)\s*;?)\s*$", re.I)
SOFT_STUB = re.compile(r"^\s*(?:pass|\.\.\.|return\s+None|(?:#|//)\s*(?:TODO|FIXME|XXX)\b.*)\s*;?\s*$", re.I)
DEF_LINE = re.compile(r"^\s*(?:(?:export|public|private|protected|static|async|default|abstract|final)\s+)*(?:def|function\*?|fn|func)\b.*[:{]\s*$")
CONFIG_LOOSE = re.compile(
    r"""(?ix)
    \bstrict\w*["']?\s*[=:]\s*(?:false|0)\b|ignore_errors\s*=\s*true|ignore_missing_imports\s*=\s*true|
    \b(?:allow_untyped_\w+|allow_redefinition)\s*["']?\s*[=:]\s*["']?true\b|
    \b(?:disallow_untyped_\w+|disallow_any_\w+|check_untyped_defs|warn_\w+|noImplicit\w+|noUnused\w+)\s*["']?\s*[=:]\s*["']?false\b|
    ["']?(?:--cov-fail-under|fail_under|fail-under)["']?|
    \b(?:ignore|exclude|omit|extend-ignore|extend-exclude|per-file-ignores|testPathIgnorePatterns|coveragePathIgnorePatterns)\s*[=:]|
    :\s*["']off["']|=\s*["']off["']|["']error["']\s*->\s*["']warn|\bcontinue-on-error:\s*true|
    filterwarnings\s*=\s*ignore|\bpassWithNoTests\b|\bbail\s*[=:]\s*(?:0|false)\b|--passWithNoTests|--no-verify|\bskipLibCheck\b|
    \btimeout\s*[=:]\s*\d{4,}|\bnoEmit\b.*false
    """)
# keys whose number is a floor (lowering weakens) and `max-*` keys whose number is a ceiling (raising weakens)
THRESHOLD = re.compile(
    r"(?P<key>fail[_-]under|--cov-fail-under|coverageThreshold|branches|functions|lines|statements|threshold|max[_-][a-z][a-z_-]*[a-z])"
    r"\W{0,6}(?P<num>\d+(?:\.\d+)?)", re.I)
THRESH_KEY = re.compile(r"(?i)fail[_-]under|cov-fail-under|coverageThreshold")
STRICT_ON = re.compile(r"(?i)\bstrict\w*[\"']?\s*[=:]\s*(?:true|1)\b|\b(?:disallow_untyped_\w+|check_untyped_defs|warn_\w+|noImplicit\w+)\s*[=:]\s*true\b")
CI_CHECK_STEP = re.compile(r"(?i)\brun:.*?\b(pytest|unittest|npm\s+(?:run\s+)?test|jest|vitest|go\s+test|cargo\s+test|ruff|mypy|pyright|eslint|tsc|flake8|pylint)\b")


def is_test_path(path: str) -> bool:
    """A test is a CODE file in a test directory or with a test-style name; a document or a fixture under tests/, specs/ or e2e/ is not."""
    p = Path(path.replace("\\", "/"))
    if p.suffix.lower() not in CODE_EXT:
        return False
    if any(part in TEST_DIRS for part in p.parts[:-1]):
        return True
    n = p.name
    return bool(re.match(r"^(?:test_.*|.*_test|.*_tests|conftest)\.\w+$", n) or re.search(r"\.(?:test|spec)\.\w+$", n)
                or re.match(r"^Test\w*\.\w+$", n) or re.search(r"Tests?\.(?:java|kt|cs|swift)$", n))


def is_config_path(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name
    return name in CONFIG_NAMES or name.startswith(CONFIG_PREFIXES) or (".github/workflows/" in path.replace("\\", "/") and name.endswith((".yml", ".yaml")))


def is_code_path(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXT


# ---------- diff parsing ----------

def parse_diff(text: str) -> list[dict]:
    """Unified diff -> [{path, old_path, deleted, new, hunks:[{old_start, old_count, new_start, new_count, lines:[(op, oldno, newno, text)]}]}].
    Hunk bodies are read by the counts in the header, so a content line that looks like `--- x` is never taken for a file header."""
    files: list[dict] = []
    cur: dict | None = None
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = DIFF_HEAD.match(ln)
        if m:
            cur = {"path": m[2], "old_path": m[1], "deleted": False, "new": False, "hunks": []}
            files.append(cur)
            i += 1
            continue
        if cur is not None and not cur["hunks"]:
            if ln.startswith("deleted file mode"):
                cur["deleted"] = True
            elif ln.startswith("new file mode"):
                cur["new"] = True
            elif ln.startswith("+++ "):
                tgt = ln[4:].strip()
                if tgt == "/dev/null":
                    cur["deleted"] = True
                elif tgt.startswith("b/"):
                    cur["path"] = tgt[2:]
            elif ln.startswith("--- ") and ln[4:].strip() == "/dev/null":
                cur["new"] = True
        h = HUNK.match(ln)
        if h and cur is not None:
            oc = int(h[2]) if h[2] is not None else 1
            nc = int(h[4]) if h[4] is not None else 1
            hunk = {"old_start": int(h[1]), "old_count": oc, "new_start": int(h[3]), "new_count": nc, "lines": []}
            o, n = int(h[1]), int(h[3])
            ro, rn = oc, nc
            i += 1
            while i < len(lines) and (ro > 0 or rn > 0):
                body = lines[i]
                if body.startswith("\\"):
                    i += 1
                    continue
                op = body[:1]
                txt = body[1:]
                if op == "-":
                    hunk["lines"].append(("-", o, None, txt))
                    o += 1
                    ro -= 1
                elif op == "+":
                    hunk["lines"].append(("+", None, n, txt))
                    n += 1
                    rn -= 1
                else:
                    hunk["lines"].append((" ", o, n, txt))
                    o += 1
                    n += 1
                    ro -= 1
                    rn -= 1
                i += 1
            cur["hunks"].append(hunk)
            continue
        i += 1
    return files


def added_file_diff(path: str, content: str) -> str:
    """Synthesize the diff of an untracked file (git diff does not show it)."""
    body = content.replace("\r\n", "\n").split("\n")
    if body and body[-1] == "":
        body.pop()
    head = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    if not body:
        return head
    return head + f"@@ -0,0 +1,{len(body)} @@\n" + "\n".join("+" + b for b in body) + "\n"


# ---------- detection ----------

def _snip(s: str) -> str:
    s = " ".join(s.strip().split())
    return s if len(s) <= SNIPPET_MAX else s[:SNIPPET_MAX - 3] + "..."


def _sig(path: str, line: int, kind: str, text: str, side: str = "new") -> dict:
    return {"file": path, "line": line, "kind": kind, "snippet": _snip(text), "side": side}


def _blocks(hunk: dict) -> list[tuple[list, list]]:
    """Contiguous change runs inside a hunk -> [(removed lines, added lines)]."""
    out: list[tuple[list, list]] = []
    rem: list = []
    add: list = []
    for ln in hunk["lines"] + [(" ", None, None, "")]:
        if ln[0] == "-":
            if add:
                out.append((rem, add))
                rem, add = [], []
            rem.append(ln)
        elif ln[0] == "+":
            add.append(ln)
        else:
            if rem or add:
                out.append((rem, add))
                rem, add = [], []
    return out


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _masked(line: str, idx: int, path: str) -> bool:
    """True when position idx of a one-line source text is not live code: inside a string literal, or after an earlier comment
    opener (prose that merely mentions a marker). Heuristic - quotes are paired on this line only; `//` opens a comment only
    in the languages that have it (in Python it is floor division)."""
    ext = Path(path).suffix.lower()
    slash = ext in SLASH_COMMENT_EXT
    hash_ = ext not in SLASH_COMMENT_EXT or ext == ".php"
    quote = None
    i = 0
    while i < idx:
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif (hash_ and ch == "#") or (slash and line.startswith("//", i)):
            return True
        i += 1
    return quote is not None


def _suppression(txt: str, path: str) -> re.Match | None:
    """The first real lint/type suppression of a source line: not inside a string literal, not inside prose of a comment, not
    the benign `# noqa: E402`."""
    for m in SUPPRESSION.finditer(txt):
        if _masked(txt, m.start(), path):
            continue
        codes = NOQA_CODES.match(txt, m.start())
        if codes and {c.upper() for c in re.split(r"[,\s]+", codes[1].strip())} <= BENIGN_NOQA:
            continue
        return m
    return None


def _is_comment(txt: str) -> bool:
    return txt.strip().startswith(("#", "//", "/*", "*"))


def _is_stub(txt: str) -> bool:
    return bool(HARD_STUB.match(txt) or SOFT_STUB.match(txt))


def _stub_signals(path: str, f: dict) -> list[dict]:
    """Stub bodies in non-test code. Counted: a hard stub (raise NotImplementedError, todo!(), ...) anywhere; a soft stub
    (pass, ..., return None, TODO) only as the first line after a newly added def / function, or when a change replaces real
    code with stubs alone. A bare `pass` in `except:`, an early `return None` or a Protocol body is not a weakened check."""
    sigs: list[dict] = []
    seen: set[int] = set()
    for h in f["hunks"]:
        for rem, add in _blocks(h):
            prev = ""
            for a in add:
                txt = a[3]
                if not txt.strip():
                    continue
                if HARD_STUB.match(txt) or (SOFT_STUB.match(txt) and DEF_LINE.match(prev)):
                    sigs.append(_sig(path, a[2], "stub_body_added", txt))
                    seen.add(a[2])
                prev = txt
            real_removed = [r for r in rem if r[3].strip() and not _is_comment(r[3]) and not _is_stub(r[3])]
            stubs = [a for a in add if _is_stub(a[3])]
            others = [a for a in add if a[3].strip() and not _is_stub(a[3]) and not DEF_LINE.match(a[3]) and not _is_comment(a[3])]
            if real_removed and stubs and not others and not any(a[2] in seen for a in stubs):
                sigs.append(_sig(path, stubs[0][2], "stub_body_added",
                                 f"{len(real_removed)} line(s) of code replaced by: {stubs[0][3].strip()}"))
    return sigs


def _norm_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _config_signals(path: str, f: dict) -> list[dict]:
    sigs: list[dict] = []
    added_all = [l for h in f["hunks"] for l in h["lines"] if l[0] == "+"]
    workflow = ".github/workflows/" in path.replace("\\", "/")
    for h in f["hunks"]:
        for rem, add in _blocks(h):
            flagged: set[int] = set()
            for r in rem:
                rm = THRESHOLD.search(r[3])
                if rm:
                    rkey = _norm_key(rm["key"])
                    ceiling = rkey.startswith("max_")  # a limit: raising it weakens the check
                    rn = _num(rm["num"])
                    for a in add:
                        am = THRESHOLD.search(a[3])
                        if am and _norm_key(am["key"]) == rkey:
                            an = _num(am["num"])
                            if rn is not None and an is not None and (an > rn if ceiling else an < rn):
                                sigs.append(_sig(path, a[2], "config_limit_raised" if ceiling else "config_threshold_lowered",
                                                 f"{rm['key']} {rm['num']} -> {am['num']}"))
                                flagged.add(a[2])
                if THRESH_KEY.search(r[3]) and not any(THRESH_KEY.search(a[3]) for a in added_all):
                    sigs.append(_sig(path, r[1], "config_threshold_removed", r[3], "old"))
                if workflow:
                    step = CI_CHECK_STEP.search(r[3])
                    if step and not any(re.search(r"\b" + step[1].replace(" ", r"\s+") + r"\b", a[3]) for a in added_all):
                        sigs.append(_sig(path, r[1], "config_check_removed", r[3], "old"))
            for r in rem:
                if STRICT_ON.search(r[3]) and not any(STRICT_ON.search(a[3]) for a in add):
                    loosened = [a for a in add if a[2] not in flagged and CONFIG_LOOSE.search(a[3])]
                    for a in loosened:
                        sigs.append(_sig(path, a[2], "config_loosened", f"{r[3].strip()}  ->  {a[3].strip()}"))
                        flagged.add(a[2])
                    if not loosened and not any(STRICT_ON.search(a[3]) for a in added_all):
                        sigs.append(_sig(path, r[1], "config_check_removed", r[3], "old"))
            for a in add:
                if a[2] in flagged or not CONFIG_LOOSE.search(a[3]):
                    continue
                if re.search(r"(?i)fail[_-]under|cov-fail-under", a[3]):
                    continue  # a threshold line is judged against its removed twin above
                sigs.append(_sig(path, a[2], "config_loosened", a[3]))
    return sigs


def scan_file(f: dict) -> list[dict]:
    path = f["path"]
    test = is_test_path(path) or is_test_path(f.get("old_path") or path)
    sigs: list[dict] = []
    removed = [l for h in f["hunks"] for l in h["lines"] if l[0] == "-"]
    added = [l for h in f["hunks"] for l in h["lines"] if l[0] == "+"]
    # a commented-out `def test_x` does not keep the test: only live added lines count when looking for the name
    added_text = "\n".join(l[3] for l in added if not _is_comment(l[3]))

    if test:
        if f["deleted"]:
            sigs.append(_sig(path, 1, "test_file_deleted", f"test file deleted ({len(removed)} lines)", "old"))
        elif removed and not any(l[3].strip() for l in added) and any(h["new_count"] == 0 and h["new_start"] == 0 for h in f["hunks"]):
            sigs.append(_sig(path, 1, "test_file_emptied", f"test file emptied ({len(removed)} lines removed, nothing added)", "old"))
        if not f["deleted"]:
            for op, o, _n, txt in removed:
                m = TEST_FUNC.match(txt)
                if m:
                    name = next(g for g in m.groups() if g)
                    if not re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", added_text):
                        sigs.append(_sig(path, o, "test_function_removed", txt, "old"))
        for _op, _o, n, txt in added:
            if SKIP_MARKER.search(txt):
                sigs.append(_sig(path, n, "skip_marker_added", txt))
            if TRIVIAL_ASSERT.search(txt):
                sigs.append(_sig(path, n, "trivial_assertion_added", txt))
        if not f["deleted"]:
            for h in f["hunks"]:
                for rem, add in _blocks(h):
                    add_asserts = [a for a in add if ASSERT_LINE.match(a[3])]
                    rem_asserts = [r for r in rem if ASSERT_LINE.match(r[3])]
                    for k, r in enumerate(rem_asserts):
                        same = [a for a in add_asserts if a[3].strip() == r[3].strip()]
                        if same:
                            continue
                        partner = add_asserts[k] if k < len(add_asserts) else None
                        if partner is not None:
                            if WEAK_ASSERT.search(partner[3]) and not WEAK_ASSERT.search(r[3]):
                                sigs.append(_sig(path, partner[2], "assertion_loosened", f"{r[3].strip()}  ->  {partner[3].strip()}"))
                        else:
                            sigs.append(_sig(path, r[1], "assertion_removed", r[3], "old"))

    # suppressions: code and check configuration only (a document that talks about them is not one)
    if (is_code_path(path) or is_config_path(path)) and not f["deleted"]:
        for _op, _o, n, txt in added:
            if _suppression(txt, path):
                sigs.append(_sig(path, n, "suppression_added", txt))

    # stub bodies in non-test code
    if not test and is_code_path(path) and not f["deleted"]:
        sigs.extend(_stub_signals(path, f))

    # config of checks
    if is_config_path(path) and not f["deleted"]:
        sigs.extend(_config_signals(path, f))
    return sigs


def scan_diff(text: str) -> list[dict]:
    """All signals of a unified diff text (the testable core: no git needed)."""
    out: list[dict] = []
    for f in parse_diff(text):
        out.extend(scan_file(f))
    return out


# ---------- git ----------

def _git(args: list[str], cwd: Path, literal: bool = False) -> tuple[int, str]:
    pre = ["--literal-pathspecs"] if literal else []
    p = subprocess.run(["git", *pre, "-c", "core.quotepath=off", *args], cwd=str(cwd), capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


def _read_untracked(p: Path) -> str | None:
    """Text of an untracked file, or None when it is too big, binary or unreadable."""
    try:
        if not p.is_file() or p.stat().st_size > UNTRACKED_MAX:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:BINARY_PROBE]:
        return None
    return data.decode("utf-8", errors="replace")


def collect_diff(cwd: Path, base: str, paths: list[str] | None, subtree: bool = False) -> tuple[str, str | None]:
    """(diff text incl. untracked files, note or None). Without `paths` the whole repository is scanned (git is run from its
    top level) - or only the folder `cwd` with `subtree`; with `paths` they are relative to `cwd` and so are the reported
    file names. A note is returned for every case where the report could be clean only because nothing was read."""
    if base.startswith("-"):
        return "", f"--base {base!r} is not a git ref"
    rc, top = _git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0 or not top.strip():
        return "", "not a git repository"
    if not paths and not subtree:
        cwd = Path(top.strip())
    rel = ["--relative"] if (paths or subtree) else []
    pathspec = ["--", *paths] if paths else ["--", "."] if subtree else ["--"]
    # --src-prefix / --dst-prefix override diff.noprefix and diff.mnemonicPrefix of the owner's git config; -M keeps a renamed
    # test from looking like a deleted one
    rc, out = _git(["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-U0", "--src-prefix=a/", "--dst-prefix=b/",
                    *rel, base, *pathspec], cwd, literal=bool(paths))
    if rc != 0:
        return "", f"git diff against {base!r} failed (unknown ref?)"
    note = None
    if out.strip() and not parse_diff(out):
        note = "the git diff was not parsed (unexpected format): tracked files were not checked"
    rc2, un = _git(["ls-files", "--others", "--exclude-standard", *pathspec], cwd, literal=bool(paths))
    extra = []
    if rc2 == 0:
        for rel_path in un.splitlines():
            body = _read_untracked(cwd / rel_path)
            if body is not None:
                extra.append(added_file_diff(rel_path, body))
    return out + "".join(extra), note


def order_files(order_id: str) -> tuple[Path | None, list[str] | None, str | None]:
    """(project root, files the order declared, note). The declaration is files_changed of circle/answers/<id>.json.
    files is None whenever nothing usable was declared (no answer yet, unreadable, malformed, no files): the caller then scans
    the whole working tree and the note says why."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from forge_common import find_root, layout
        root = find_root()
        ans = layout(root)["circle"] / "answers" / f"{order_id}.json"
    except (SystemExit, OSError, ValueError, KeyError) as e:
        return None, None, f"not inside a Forge project ({e}): showing the whole working tree"
    if not ans.is_file():
        return root, None, f"{order_id}: no answers/{order_id}.json yet (nothing declared): showing the whole working tree"
    try:
        data = json.loads(ans.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return root, None, f"{order_id}: answers/{order_id}.json is unreadable: showing the whole working tree"
    files = data.get("files_changed") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return root, None, f"{order_id}: answers/{order_id}.json is malformed (no files_changed list): showing the whole working tree"
    declared = [str(x).replace("\\", "/") for x in files if isinstance(x, str) and x.strip()]
    if not declared:
        return root, None, f"{order_id}: the answer declares no files: showing the whole working tree"
    return root, declared, None


def build_report(signals: list[dict], base: str, scope: str, note: str | None) -> dict:
    by_kind: dict[str, int] = {}
    for s in signals:
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + 1
    return {"report_only": True, "base": base, "scope": scope, "count": len(signals), "by_kind": by_kind,
            "signals": signals, "note": note}


def format_text(rep: dict) -> str:
    out = [f"weaken_scan (report only): {rep['count']} signal(s) against {rep['base']}, scope: {rep['scope']}"]
    if rep.get("note"):
        out.append(f"note: {rep['note']}")
    for s in rep["signals"]:
        side = "" if s["side"] == "new" else " (old)"
        out.append(f"  {s['file']}:{s['line']}{side} [{s['kind']}] {s['snippet']}")
    if rep["count"]:
        out.append("A signal is a question, not a verdict: confirm each one weakened a check, or say why it did not.")
    return "\n".join(out)


def scan(base: str, order: str | None, files: str | None) -> dict:
    cwd = Path.cwd()
    paths: list[str] | None = None
    notes: list[str] = []
    scope = "working tree"
    subtree = False  # an order's project may be a folder of a bigger repository: scan that folder only
    if files:
        paths = [x.strip().replace("\\", "/") for x in files.split(",") if x.strip()] or None
        scope = "declared files"
    elif order:
        root, paths, note = order_files(order)
        scope = f"order {order}" if paths else f"working tree (order {order}: no declaration)"
        if root is not None:
            cwd, subtree = root, True
        if note:
            notes.append(note)
    text, err = collect_diff(cwd, base, paths, subtree)
    if err:
        notes.append(err)
    signals = scan_diff(text) if text else []
    return build_report(signals, base, scope, "; ".join(notes) or None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report-only detector of check weakening in a git diff (always exits 0).")
    ap.add_argument("--base", default="HEAD", help="git ref to compare the working tree with (default HEAD)")
    ap.add_argument("--order", help="limit to the files the order declared (answers/<id>.json files_changed); "
                                    "until the answer exists the whole working tree is scanned")
    ap.add_argument("--files", help="limit to these comma-separated paths (relative to the current folder)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    try:
        rep = scan(a.base, a.order, a.files)
    except Exception as e:  # report-only: never fail the caller, never look clean without saying so
        rep = build_report([], a.base, "working tree", f"scan failed: {type(e).__name__}: {e}")
    print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json else format_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
