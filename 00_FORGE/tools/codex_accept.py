"""Acceptance of a Codex return and the live-load wave capacity (Forge 1.2.0, N031), split from codex_exec.py (gate K11: 64 KB).

`codex_exec.py apply` asks this module whether a patch must carry tests (plan `tests`, the order's DoD), runs the declared checks
on a scratch copy of the project with the staged files laid over it (process-tree kill on timeout, junctions for dependency
folders), and `wave` asks it how many parallel Codex runs the live machine load allows. codex_exec re-exports every public name;
this module is a leaf (it must not import codex_exec: the CLI runs as __main__)."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from forge_common import now_hm, today  # noqa: E402 - tools/ is on sys.path (codex_exec inserts it before importing this module)

def section(md: str, title: str, limit: int = 2500) -> str:
    """Text of a `## <title>...` section of the order file (up to the next `## `)."""
    m = re.search(r"^## " + re.escape(title) + r"[^\n]*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return (m.group(1).strip() if m else "")[:limit]


def norm(path: str) -> str:
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def in_scope(path: str, scope: list[str]) -> bool:
    p = norm(path).lower()
    for s in scope:
        s = norm(s).lower().rstrip("/")
        if p == s or p.startswith(s + "/"):
            return True
    return False


def load_json(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def wave_slots(current: int, load: dict) -> tuple[int, str]:
    """K28: min(current slots, the live-load slots) and one line saying why when the cap lowered parallelism."""
    cap = int(load.get("slots", current))
    n = min(current, cap)
    if n >= current:
        return n, ""
    why = "; ".join(load.get("reasons") or []) or ("laptop without active cooling" if load.get("laptop") else "")
    brackets = f" ({why})" if why else ""  # never invent a reason: a verdict ok with no laptop cap lowers nothing
    return n, f"machine {load.get('verdict')}{brackets}: wave parallelism {current} -> {n}."


def wave_capacity(requested: int) -> tuple[int, str]:
    """Slots for a wave and a line to print ('' = nothing to say). The profile slots of the reading are the REQUESTED ones, so
    only a verdict or the laptop cap lowers them. FORGE_MACHINE_SLOTS=N replaces the reading (tests, a fixed cap, 0 = refuse);
    a probe that fails never blocks the wave (it goes on with the requested slots)."""
    forced = os.environ.get("FORGE_MACHINE_SLOTS", "").strip()
    if forced.isdigit():
        n = int(forced)
        load = {"slots": n, "verdict": "ok" if n else "critical", "reasons": [f"FORGE_MACHINE_SLOTS={n}"], "laptop": False}
    else:
        try:
            import machine
            load = machine.load(profile_slots=requested)
        except Exception as e:  # noqa: BLE001 - like step.py: the reading is advice, its failure is not a reason to stop
            reason = " ".join(f"{type(e).__name__}: {e}".split())
            return requested, f"machine load unavailable: the live check failed ({reason}) - keeping {requested} slot(s)."
    return wave_slots(requested, load)


CHECK_TIMEOUT = 900  # seconds for one check command on the patched copy (FORGE_CHECK_TIMEOUT overrides it)
TESTS_IN_DOD = re.compile(r"\btests?\b|\btesting\b|тест", re.I)
AMENDED_DOD = re.compile(r"^\s*[-*]\s*\[[ xX]\]")  # the DoD lines `order.py amend --dod` writes under `## Amendments`
WAIVER = "**Waiver (owner's words, K19):**"  # the marker of order.py (not importable here: it acts on import)
SCRATCH_SKIP = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}  # never copied into the check's tree
SCRATCH_LINK = {"node_modules", ".venv", "venv"}  # linked to the original instead of copied: the check needs the environment
CODE_EXT = ("py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "rb", "go", "rs", "java", "kt", "cs", "php", "swift", "scala",
            "ex", "exs", "dart", "lua", "c", "cc", "cpp", "h", "hpp", "m", "mm")
TEST_NAME = re.compile(r"[._-](?:test|tests)\.[a-z0-9]+$")
SPEC_NAME = re.compile(r"[._-]spec\.(?:" + "|".join(CODE_EXT) + r")$")  # a spec DOCUMENT (api_spec.md) is not a test
CAMEL_TEST = re.compile(r"(?:[a-z0-9]Tests?(?:[A-Z0-9_]\w*)?|^Test[A-Z0-9_]\w*)\.(?:java|kt|cs|php|swift|scala)$")  # FooTest.java


def is_test_path(path: str) -> bool:
    """A test file by name or folder: test_*.py, *_test.py, *.test.* / *.spec.<code>, FooTest.java / BarTests.cs, anything under
    tests/ test/ __tests__/ *.Tests/, and code files under spec/. Rust tests written inside a source file cannot be told by path."""
    clean = norm(path)
    parts = clean.lower().split("/")
    name, folders = parts[-1], parts[:-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return (name.startswith("test_") or name.startswith("test.") or TEST_NAME.search(name) is not None
            or SPEC_NAME.search(name) is not None or CAMEL_TEST.search(clean.split("/")[-1]) is not None
            or any(d in ("tests", "test", "__tests__") or d.endswith((".tests", ".test")) for d in folders)
            or ("spec" in folders and ext in CODE_EXT))


def scope_holds_tests(scope: list[str], *, folders: bool = False) -> bool:
    """True when a test file can be proposed inside this scope: an entry that is a test path or a test folder. With `folders`,
    also any entry that looks like a folder (a trailing slash, or no extension): a new foo.test.ts may be added there."""
    for s in scope:
        s = norm(str(s)).rstrip("/")
        last = s.split("/")[-1]
        if is_test_path(s) or is_test_path(s + "/") or last.lower() == "spec" or (folders and "." not in last):
            return True
    return False


def plan_task(plan_path: Path, tid: str) -> dict:
    return next((t for t in load_json(plan_path, {"tasks": []}).get("tasks", []) if t.get("id") == tid), {})


def declared_checks(task: dict) -> list[str]:
    """Commands the plan task declares: `check` and `checks`, each a command (string) or a list of commands. Anything else is an
    error, not a skipped check: a check that silently does not run is worse than none."""
    out: list[str] = []
    for key in ("check", "checks"):
        raw = task.get(key)
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(c, str) for c in items):
            raise SystemExit(f"{task.get('id', '?')}: plan field `{key}` must be a command (a string) or a list of commands "
                             f"(strings) - got {raw!r}")
        out += [c.strip() for c in items if c.strip()]
    return out


def declared_test_paths(task: dict) -> list[str]:
    """The test files / folders the plan task names in `tests` (a string or a list); [] for true / false / absent."""
    raw = task.get("tests")
    items = raw if isinstance(raw, list) else [raw]
    return [norm(x) for x in items if isinstance(x, str) and x.strip()]


def tests_waived(md: str) -> bool:
    """True when a waiver line of the order is a waiver OF `tests` - its rule word, not a mention inside the owner's words
    (the same reading as order.waived(); N024: 'review: ..., tests: ...' lifted the wrong rule)."""
    return any(WAIVER in ln and ln.split(WAIVER, 1)[1].strip().lower().startswith("tests:") for ln in md.splitlines())


def dod_text(md: str) -> str:
    """The order's DoD: its `## DoD` section and the `- [ ]` lines `order.py amend --dod` added under `## Amendments`."""
    extra = [ln for ln in section(md, "Amendments", 6000).splitlines() if AMENDED_DOD.match(ln)]
    return section(md, "DoD", 6000) + ("\n" + "\n".join(extra) if extra else "")


def dod_wants_tests(md: str) -> bool:
    return not tests_waived(md) and TESTS_IN_DOD.search(dod_text(md)) is not None


def tests_required(task: dict, md: str, scope: list[str] | None = None) -> str:
    """Why the patch must carry test changes ('' = it need not). What the plan task says wins, both ways: `tests` true / paths =
    required, `tests: false` = not required whatever the DoD says. Without it, a DoD that mentions tests (and no owner waiver
    of `tests:`, K19) requires them - but only when the task's scope can hold a test file (`scope` given): a docs-only task has
    nothing to add them to. The mention is read loosely ("no tests needed" counts), which is why the task can say `tests: false`."""
    declared = task.get("tests")
    if declared is not None:
        return "the plan task declares `tests`" if declared else ""
    if dod_wants_tests(md) and (scope is None or scope_holds_tests(scope)):
        return "the order's DoD mentions tests"
    return ""


def tests_note(task: dict, md: str, scope: list[str]) -> str:
    """A warning for the case `tests_required` lets go: the DoD names tests but the task's scope has no test path."""
    if task.get("tests") is None and dod_wants_tests(md) and not scope_holds_tests(scope):
        return (f"the order's DoD mentions tests, but the scope of this task ({', '.join(scope) or 'empty'}) has no test path - "
                f"tests were not required of this patch")
    return ""


def acceptance_lines(task: dict, md: str, cfg: dict) -> str:
    """The bullets of the Codex prompt that tell the executor what `apply` will demand of its result."""
    out = []
    paths = declared_test_paths(task)
    if paths:
        out.append(f"The Lead refuses the result unless these test files/folders are among the changed files: {', '.join(paths)}.")
    elif tests_required(task, md, task["scope"]):
        out.append("This task must add or change tests: the Lead refuses a result without a test file in `files`.")
    cmds = declared_checks(task)
    if cmds:
        out.append("Before applying, the Lead runs " + "; ".join(f"`{c}`" for c in cmds) + " on a copy of the project with your "
                   "files laid over it - it must pass.")
    elif tests_required(task, md, task["scope"]) and (cfg.get("code") or {}).get("test"):
        out.append(f"Before applying, the Lead runs the project's tests (`{cfg['code']['test']}`) on your result - they must pass.")
    return "".join(f"- {x}\n" for x in out)


def plan_problems(tasks: list[dict]) -> list[str]:
    """Why a plan cannot be accepted whatever Codex answers (checked by `wave` before any Codex run): a malformed check, or
    `tests` that the task's scope cannot hold (Codex may not propose a file outside it)."""
    out = []
    for t in tasks:
        if t.get("status") == "applied":
            continue
        tid, scope = t.get("id", "?"), [str(s) for s in t.get("scope", [])]
        try:
            declared_checks(t)
        except SystemExit as e:
            out.append(str(e))
        named = declared_test_paths(t)
        if named:
            outside = [w for w in named if not in_scope(w, scope)]
            if outside:
                out.append(f"{tid}: `tests` names {', '.join(outside)} outside the task's scope ({', '.join(scope) or 'empty'}) - "
                           f"Codex cannot propose it; add it to the scope")
        elif t.get("tests") and not scope_holds_tests(scope, folders=True):
            out.append(f"{tid}: `tests` is set, but the scope ({', '.join(scope) or 'empty'}) has no test file or folder - Codex "
                       f"cannot propose a file outside its scope; add the test file, or set `tests` to false")
    return out


def journal_override(cfg: dict, oid: str, text: str) -> None:
    f = cfg["_layout"]["circle"] / "journal" / f"{today()}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.is_file():
        f.write_text(f"# Journal {today()}\n\n", encoding="utf-8", newline="\n")
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {now_hm()} [{oid}] {text}\n")


def one_line(text: str) -> str:
    """A journal entry is one line (`- HH:MM [N] ...`): line breaks of an owner's reason become ' / ', the words stay."""
    return re.sub(r"\s*[\r\n]+\s*", " / ", text.strip())


def check_timeout() -> int:
    try:
        n = int(os.environ.get("FORGE_CHECK_TIMEOUT", ""))
    except ValueError:
        return CHECK_TIMEOUT
    return n if n > 0 else CHECK_TIMEOUT


def kill_tree(p: subprocess.Popen) -> None:
    """Kill the process and everything it started: with `shell=True` the shell is only the top of the tree, and `Popen.kill()`
    leaves its children (pytest, node) running."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(p.pid, signal.SIGKILL)  # the group the child leads (start_new_session)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def run_shell(cmd: str, cwd: Path, timeout: int) -> tuple[int | None, str]:
    """(exit code, stdout + stderr) of a command run like forge.json `code.test`: through the platform shell (cmd.exe on Windows,
    /bin/sh elsewhere), in a process group of its own. (None, '') after `timeout` seconds, with the whole tree killed."""
    own_group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         encoding="utf-8", errors="replace", **own_group)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # something outside the group still holds the pipes: let them go, never hang
        return None, ""
    return p.returncode, out + err


def is_junction(p: Path) -> bool:
    probe = getattr(os.path, "isjunction", None)  # Python 3.12+; a junction is a directory that points elsewhere
    return bool(probe and probe(p))


def link_dir(src: Path, dst: Path) -> None:
    """`dst` becomes a link to the directory `src`: a symlink, on Windows a junction (it needs no privilege)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.symlink(src, dst, target_is_directory=True)
        return
    try:
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))
    except (ImportError, AttributeError):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True)
        if r.returncode != 0:
            raise OSError(f"mklink /J failed for {dst}") from None


def drop_link(dst: Path) -> None:
    """Remove a link made by link_dir without touching what it points at (rmtree must never walk into the original)."""
    for remove in (os.unlink, os.rmdir):
        try:
            remove(dst)
            return
        except OSError:
            continue


def scratch_copy(root: Path, work: Path, hidden: set[Path], made: list[Path]) -> None:
    """Copy the project to `work`: no Forge circle/state, no .git or caches; node_modules, .venv, venv (and junctions) are
    linked to the originals; `made` collects the links so the caller can drop them before deleting the copy."""
    links: list[Path] = []

    def ignore(d: str, names: list[str]) -> list[str]:
        out = []
        for n in names:
            src = Path(d, n)
            if n in SCRATCH_SKIP or src in hidden:
                out.append(n)
            elif src.is_dir() and (n in SCRATCH_LINK or is_junction(src)):
                out.append(n)
                links.append(src)
        return out

    shutil.copytree(root, work, symlinks=True, ignore=ignore)
    for src in links:
        dst = work / src.relative_to(root)
        link_dir(src, dst)
        made.append(dst)


def run_checks_on_patch(root: Path, cfg: dict, sdir: Path, proposed: list[str], commands: list[str]) -> str:
    """Run the commands on a scratch copy of the project with the staged files laid over it; '' = all passed, else the failure.
    The project's dependency folders are linked, not copied, so `npm test` / `.venv/bin/python -m pytest` work; a package
    installed in editable mode still imports the ORIGINAL tree and so does not see the patch."""
    hidden = {cfg["_layout"]["circle"], cfg["_layout"]["state"]}
    scratch = Path(tempfile.mkdtemp(prefix="forge_check_"))
    made: list[Path] = []
    try:
        work = scratch / "tree"
        try:
            scratch_copy(root, work, hidden, made)
        except (OSError, shutil.Error) as e:
            return f"the copy of the project for the check could not be built: {e}"
        for p in proposed:
            (work / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sdir / p, work / p)
        timeout = check_timeout()
        for cmd in commands:
            code, output = run_shell(cmd, work, timeout)
            if code is None:
                return f"`{cmd}` timed out after {timeout}s (its process tree was killed)"
            if code != 0:
                return f"`{cmd}` failed (exit {code}):\n{output.strip()[-700:]}"
        return ""
    finally:
        for dst in made:
            drop_link(dst)
        shutil.rmtree(scratch, ignore_errors=True)
