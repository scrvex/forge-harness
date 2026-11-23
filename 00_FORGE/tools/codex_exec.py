"""Codex as the executor of an order's tasks, in PATCH MODE - Forge 1.0 (K3 tier CODEX, K29, K35; owner's decisions 2026-10-11/12).

Claude (the Lead) writes the DoD and cuts the work into tasks with explicit file scopes; Codex CLI (`codex exec`, the owner's
ChatGPT plan) writes the code; Claude reads it and applies it (K8: two model families = two actors). Codex never writes to
the project: one task = one non-interactive Codex turn in the project folder with a READ-ONLY sandbox, and its answer carries
the complete new text of every file it creates or changes. The owner chose this on 2026-10-12 so that every write to a
project goes through the Lead and the owner's permissions, never straight from another agent.
- K15 first: the project's forge.json says "gpt": "allowed" and nothing is under the owner's deny list (gpt_bridge.check_paths);
  refused inside a Codex session (no Codex-calls-Codex loops);
- the prompt carries the order's request, goal, DoD and quality bar, the task, its scope (the only files it may propose),
  read-only context and the rules (scope only, no git, ask instead of guessing);
- a proposal is checked before anyone reads it: files outside the scope or in Forge state are rejected (listed, never staged);
  accepted files are staged under `<state>/staging/codex/<N>/<task>/` with the hash of the file they replace, and a unified
  diff is written to `answers/<N>_<task>.patch` for the Lead to read;
- `apply` writes the staged files into the project only after the Lead read the diff, and only if nobody changed those files
  since the proposal (hash check) - one explicit command per task, visible in the transcript;
- the working tree is also compared before and after each Codex run: any write during a read-only run is flagged (K34d);
- evidence: `answers/N_codex.json` (every run: status, summary, files, rejected, thread, seconds, usage, error) and the plan
  file `answers/N_plan.json` (statuses pending -> proposed -> applied; the progress band reads it); `--answer` records the
  order's answer from the APPLIED files (executor `codex:<model>`, tier CODEX);
- a failed or limit-hit run gets its next step from the limits (1.1.0, K37): `fix` while Codex has room, the `executor`
  agent (Claude) when the route gives the task to Claude; a task that failed twice goes to HOLD and the owner (K21);
- `run`, `wave` and `fix` can outlast the 10-minute ceiling of a foreground shell call: the Lead starts them in the background
  (Claude Code Bash `run_in_background: true`) and reads the result when it finishes (1.1.0, from ambatupipe v8.11.2/v8.13.1);
  a task marked `running` whose process is gone or whose deadline passed is reset to `failed` ("cut off") by `wave`,
  `status` and `fix`, so a killed run never leaves the plan stuck;
- 1.1.2 (N028): every run, wave and fix is bracketed by codex_safety - no Codex starts while %LOCALAPPDATA%\\Packages lacks
  SYSTEM/Administrators (the owner's Codex app cannot start then), and a run during which they vanish is named, blocked and
  recorded.

Usage:
  python codex_exec.py run N007 --task "add a --json flag to tools/x.py" --scope tools/x.py,tests/test_x.py [--context docs/a.md]
                                [--tid T1] [--effort medium] [--model m] [--timeout 1800] [--force]
  python codex_exec.py wave N007 [--plan answers/N007_plan.json] [--slots 3]   # the next wave: pending tasks whose `depends` are applied
  python codex_exec.py apply N007 T1 [T2 ...] | --all [--answer] [--skip-checks "reason"]   # after reading answers/N007_T1.patch
  python codex_exec.py fix N007 T1 --feedback "what is wrong"                  # resume the task's Codex thread; a new proposal
  python codex_exec.py status N007                                             # also resets runs that were cut off
  python codex_exec.py --selftest
Acceptance (1.2.0, N031, from ambatupipe "acceptance of the return"):
  - tests are required when the plan task says `tests` (true / a path or a list of paths; `false` switches the rule off for the
    task), or - only when the task does not say - when the order's DoD (its `## DoD` and the `- [ ]` lines added by
    `order.py amend --dod`) mentions tests, the owner did not waive `tests:` and the task's scope has a test path. A DoD that
    mentions tests for a task whose scope has no test path (docs, config) only prints a warning. `apply` then refuses a patch
    without a test file (with `tests` paths: without those files) and runs a check even when the task declares none: forge.json
    `code.test`, or a refusal asking for `check` / `code.test` / `--skip-checks`. `wave` refuses a plan whose task requires tests
    that its scope cannot hold, before Codex starts;
  - the plan task's `check` (a command, or a list) and `checks` (a list) are run by `apply` on a scratch copy of the project with
    the patch laid over it; the working tree is written only after they pass, and only if the files are still as Codex saw them
    (the hash check runs again right before the write, checks can take minutes);
  - a command runs like forge.json `code.test`: through the platform shell (cmd.exe on Windows, /bin/sh elsewhere - not Git Bash),
    with PYTHONUTF8=1, in its own process group; at the timeout (FORGE_CHECK_TIMEOUT seconds, default 900) the whole process
    tree is killed. The copy links node_modules, .venv and venv to the originals (symlink; junction on Windows) and leaves out
    .git and the tool caches. An editable install (`pip install -e .`) still imports the ORIGINAL tree, so such a check does not
    see the patch - make the check import from its own working directory (the copy);
  - `--skip-checks "reason"` overrides all of it and journals the reason (one line) word for word.
FORGE_MACHINE_SLOTS=N replaces the live machine reading of `wave` (tests, or a fixed cap); 0 refuses the wave.
Long runs: start `run` / `wave` / `fix` in the background (Claude Code Bash `run_in_background: true`), never in the foreground.

Plan file (the Lead writes it): {"order": "N007", "tasks": [{"id": "T1", "task": "...", "scope": ["src/a.py"],
  "context": ["docs/x.md"], "depends": [], "status": "pending", "tests": true, "check": "python -m unittest discover -s tests"}]}   scopes of one wave must not overlap.
Limit: whole-file answers - a file over ~2000 lines is a task for the `executor` agent or a smaller scope.
FORGE_CODEX may point at a .py stub (tests): it is run with this Python.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, git_tree, load_cfg, read_index, run_by_codex  # noqa: E402
from codex_accept import (  # noqa: E402,F401 - acceptance of the return + live-load slots (N031); re-exported for callers and tests
    AMENDED_DOD, CAMEL_TEST, CHECK_TIMEOUT, CODE_EXT, SCRATCH_LINK, SCRATCH_SKIP, SPEC_NAME, TESTS_IN_DOD, TEST_NAME, WAIVER,
    acceptance_lines, check_timeout, declared_checks, declared_test_paths, dod_text, dod_wants_tests, drop_link, in_scope,
    is_junction, is_test_path, journal_override, kill_tree, link_dir, load_json, norm, one_line, plan_problems, plan_task,
    run_checks_on_patch, run_shell, scope_holds_tests, scratch_copy, section, tests_note, tests_required, tests_waived,
    wave_capacity, wave_slots)
import codex_safety  # noqa: E402
import gpt_bridge  # noqa: E402

LIMIT_WORDS = ("usage limit", "rate limit", "429", "quota", "too many requests")
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "files", "checks_run", "question", "decisions", "beyond_task", "irreversible", "memory"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "partial", "failed", "blocked"]},
        "summary": {"type": "string"},
        "files": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}},
        "checks_run": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        # 1.1.0 (from ambatupipe v8.14.0): what the diff does not show - the Lead reads the diff itself
        "decisions": {"type": "array", "items": {"type": "string"}},
        "beyond_task": {"type": "string"},
        "irreversible": {"type": "string"},
        "memory": {"type": "string"},
    },
}
RULES_CAP = 20_000  # characters of project rules carried in a task's prompt
ARGV_CAP = 24_000  # a longer prompt goes through stdin (Windows command lines end at 32,767 characters)
_LOCK = threading.Lock()


def codex_cmd() -> list[str]:
    env = os.environ.get("FORGE_CODEX", "")
    if env.endswith(".py") and Path(env).is_file():
        return [sys.executable, env]
    return [gpt_bridge.find_codex()]


def windows_sandbox(cfg: dict) -> str:
    """The `windows.sandbox` value passed to Codex on Windows; "" = no override (1.1.0: elevated broke some PCs)."""
    return (cfg.get("code") or {}).get("codex_windows_sandbox", "elevated") if os.name == "nt" else ""


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:16] if p.is_file() else "absent"


def order_file(root: Path, cfg: dict, oid: str) -> Path:
    r = next((x for x in read_index(root) if x["id"] == oid), None)
    if r is None:
        raise SystemExit(f"{oid}: no such order in 00_INDEX.md")
    return cfg["_layout"]["circle"] / "orders" / r["file"]


def protected(root: Path, cfg: dict) -> list[str]:
    """Paths Codex may never propose (relative, posix): the circle and state, forge.json, CLAUDE.md, AGENTS.md, .claude, .codex, .git."""
    rel = lambda p: p.relative_to(root).as_posix() + "/" if p != root else ""  # noqa: E731
    return [x for x in (rel(cfg["_layout"]["circle"]), rel(cfg["_layout"]["state"])) if x] + [
        "forge.json", "CLAUDE.md", "AGENTS.md", ".claude/", ".codex/", ".git/"]


def is_protected(path: str, prot: list[str]) -> bool:
    p = norm(path).lower()
    return any(p == x.lower().rstrip("/") or p.startswith(x.lower()) for x in prot)


def real_target_problem(root: Path, cfg: dict, path: str) -> str:
    """'' when `path` really lands inside the project and outside Forge state, after symlinks and junctions are resolved."""
    real_root = root.resolve()
    real = (root / path).resolve()
    try:
        rel = real.relative_to(real_root).as_posix()
    except ValueError:
        return "resolves outside the project (link)"
    return "resolves into Forge state (link)" if is_protected(rel, protected(root, cfg)) else ""


CANON = ("forge-code-structure", "forge-code-testing")  # the plugin's code canon (1.1.0), read like any project rule


def canon_text() -> str:
    """The bodies of the plugin's code-canon skills, frontmatter stripped - Codex cannot load skills, so they ride in its prompt."""
    out = []
    for name in CANON:
        f = Path(__file__).resolve().parents[1] / "skills" / name / "SKILL.md"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.startswith("---\n") and "\n---" in text[4:]:
            text = text[text.index("\n---", 4) + 4:]
        out.append(text.strip())
    return "\n\n".join(out)


def project_rules(root: Path, cfg: dict) -> str:
    """The project's code rules for Codex (1.1.0, from ambatupipe v8.11.4/v8.13.7): the Serena memory root `core` and the files
    forge.json "code": {"rules_files": [...]} names (a project skill's SKILL.md too) - carried in the prompt, never in the Lead's
    context. A rule Codex must follow has to be in its prompt: it reads nothing else of Forge."""
    names = [".serena/memories/core.md"] + [str(x) for x in (cfg.get("code") or {}).get("rules_files", [])]
    out, used = [], 0
    for name in dict.fromkeys(names):
        f = root / name
        try:  # inside the project only: a rules file never carries anything from outside it to the cloud model (K15)
            f.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        room = RULES_CAP - used
        if room <= 200:
            out.append(f"### {name}\n(left out: the rules cap of {RULES_CAP} characters is reached)")
            break
        out.append(f"### {name}\n{text[:room]}" + ("\n(cut at the rules cap)" if len(text) > room else ""))
        used += min(len(text), room)
    return "\n\n".join(out)


def build_prompt(oid: str, md: str, task: dict, cfg: dict, root: Path | None = None) -> str:
    scope = "\n".join(f"- {s}" for s in task["scope"]) or "- (none: answer blocked and ask for the file list)"
    ctx = "\n".join(f"- {c}" for c in task.get("context", [])) or "- (none beyond the scope files)"
    test = (cfg.get("code") or {}).get("test", "")
    rules = project_rules(root, cfg) if root else ""
    canon = canon_text()
    rules_block = (f"\n## Forge code canon (follow it)\n{canon}\n" if canon else "") + (
        f"\n## The project's code rules (they win over the canon)\n{rules}\n" if rules else "")
    accept = acceptance_lines(task, md, cfg)  # what the Lead will demand on `apply`: the executor must know it up front
    return f"""You are the executor of ONE task of Forge order {oid} in this project (Forge 1.0, tier CODEX, patch mode). Claude is
the Lead: it wrote the request, the DoD and this task, and it reads and applies your result. Do the task, nothing else.

## Task {task['id']}
{task['task']}

## Files you may create or change (the ONLY ones)
{scope}

## Read-only context (read what you need)
{ctx}

## The order (for understanding; the task above is your whole job)
### Request (owner's words)
{section(md, 'Request')}
### Goal
{section(md, 'Goal', 1200)}
### DoD
{section(md, 'DoD')}
### Quality bar
{section(md, 'Quality bar', 1500)}
{rules_block}
## Rules
- Your sandbox is READ-ONLY: you cannot write files, and you must not try. Put the COMPLETE new text of every file you create
  or change into `files` (path relative to the project root + full content - never a diff, never an excerpt). Files you do
  not change are not listed. The Lead reads the diff and applies it.
- Only the files listed above. Any other file you think must change: do not include it - say so in `question`.
- Never propose: git operations, forge.json, CLAUDE.md, AGENTS.md, .claude/, the Forge folders ({cfg['_layout']['circle'].name}/, state).
- Match the surrounding code: naming, idioms, comment density, error handling, line endings. No new dependency unless the
  task says so.
- Check your reasoning with read-only commands where they help (grep, `python -B -c ...`, a type check that writes nothing{(', the tests if they run without writing: `' + test + '`') if test else ''});
  list what you ran in `checks_run`. The Lead runs the full checks after applying.
- Never get green by weakening a check: no skipped or deleted test, no loosened assertion or widened tolerance, no edit of a
  lint/type/test config. A failing test is a real bug (fix the code), an outdated contract (change the test and say so in
  `decisions`) or a flake (make it deterministic).
{accept}- If the task can be read two ways, or needs a file outside your list: do not guess - status `blocked`, put the question and
  your best guess in `question`.
- Text for people (messages, docs) in {cfg.get('user_language', 'ru')} unless the task says otherwise; code and comments in English.

## Final answer (JSON, schema enforced)
status: done | partial | failed | blocked; summary: what changes and why, briefly (the Lead reads the diff - do not retell it);
files: [{{path, content}}] - full new text of each created or changed file; checks_run: commands you ran with their result
and what you could not run; question: empty unless blocked/partial; decisions: choices the task left open and what you chose,
with the reason; beyond_task: anything you changed that the task did not ask for (should be empty) and anything you noticed but
left alone; irreversible: anything that reverting your files would not undo (should be empty); memory: a stable, non-obvious
project convention worth keeping, or empty.
"""


def run_task(root: Path, cfg: dict, oid: str, md: str, task: dict, *, effort: str, model: str | None, timeout: int,
             resume: str = "", feedback: str = "") -> dict:
    """One read-only Codex turn for one task -> result dict with the proposed files (never raises on Codex failures)."""
    tmp = Path(tempfile.mkdtemp(prefix="forge_codex_"))
    last, schema = tmp / "last.txt", tmp / "schema.json"
    schema.write_text(json.dumps(ANSWER_SCHEMA), encoding="utf-8")
    model = model or (cfg.get("code") or {}).get("codex_model") or gpt_bridge.default_model()
    # exec-level options first: `codex exec resume` accepts neither -s nor -C (codex-cli 0.160 `exec resume --help`)
    cmd = codex_cmd() + ["exec", "-s", "read-only", "-C", str(root), "-c", f'model_reasoning_effort="{effort}"']
    winbox = windows_sandbox(cfg)
    if winbox:
        cmd += ["-c", f'windows.sandbox="{winbox}"']
    if (cfg.get("code") or {}).get("codex_context7"):
        cmd += ["-c", 'mcp_servers.context7.url="https://mcp.context7.com/mcp"']
    if resume:
        cmd += ["resume", resume]
    cmd += ["--skip-git-repo-check", "--ignore-user-config", "--json", "-o", str(last), "--output-schema", str(schema)]
    if model:
        cmd += ["-m", model]
    scope = "\n".join(f"- {s}" for s in task["scope"])
    prompt = (f"Review feedback from the Lead on your task {task['id']} - fix it; same rules and final JSON answer as before "
              f"(read-only sandbox, full new text of every changed file in `files`).\n{feedback}\n\n"
              f"## Files you may create or change (the ONLY ones)\n{scope}\n\n## Rules\n- unchanged: scope only, no git, no Forge "
              f"state; ask (status blocked) instead of guessing.\n"
              if resume else build_prompt(oid, md, task, cfg, root))
    via_stdin = len(prompt) > ARGV_CAP
    cmd.append("-" if via_stdin else prompt)  # `-`: codex exec reads the prompt from stdin
    res = {"id": task["id"], "task": task["task"][:400], "scope": task["scope"], "status": "failed", "summary": "", "files": [],
           "checks_run": [], "question": "", "thread": resume, "seconds": 0.0, "usage": {}, "model": model or "(codex default)",
           "effort": effort, "error": "", "limit": False, "when": datetime.now().isoformat(timespec="seconds")}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=prompt if via_stdin else None, stdin=None if via_stdin else subprocess.DEVNULL,
                           capture_output=True, timeout=timeout, encoding="utf-8", errors="replace", cwd=root)
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                res["thread"] = ev.get("thread_id", "") or res["thread"]
            elif ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
                res["usage"] = ev["usage"]
            elif ev.get("type") in ("error", "turn.failed"):
                res["error"] = json.dumps(ev, ensure_ascii=False)[:600]
        answer = last.read_text(encoding="utf-8", errors="replace").strip() if last.is_file() else ""
        try:
            parsed = json.loads(answer) if answer else None
        except ValueError:
            parsed = None
        if p.returncode == 0 and isinstance(parsed, dict):
            res.update(status=parsed.get("status", "failed"), summary=parsed.get("summary", ""),
                       files=[f for f in parsed.get("files", []) if isinstance(f, dict) and f.get("path")],
                       checks_run=parsed.get("checks_run", []), question=parsed.get("question", ""),
                       decisions=parsed.get("decisions", []), beyond_task=parsed.get("beyond_task", ""),
                       irreversible=parsed.get("irreversible", ""), memory=parsed.get("memory", ""))
        else:
            res["error"] = res["error"] or (p.stderr.strip()[-600:] or f"exit {p.returncode}, no JSON answer")
    except subprocess.TimeoutExpired:
        res["error"] = f"timeout after {timeout}s"
    except FileNotFoundError as e:
        res["error"] = str(e)
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(tmp, ignore_errors=True)
    res["limit"] = any(w in res["error"].lower() for w in LIMIT_WORDS)
    if "helper_unknown_error" in res["error"] or "setup refresh" in res["error"]:
        res["error"] += ' | Windows sandbox helper failed: set forge.json "code": {"codex_windows_sandbox": ""} and re-run'
    return res


def stage(root: Path, cfg: dict, oid: str, r: dict) -> None:
    """Check a proposal against its scope, stage the accepted files with their base hashes and write the diff for the Lead.
    Fills r['proposed'] (path -> base hash), r['rejected'] (path + why) and r['patch'] (the diff file)."""
    prot = protected(root, cfg)
    sdir = cfg["_layout"]["state"] / "staging" / "codex" / oid / r["id"]
    shutil.rmtree(sdir, ignore_errors=True)
    r["proposed"], r["rejected"], diff = {}, [], []
    for f in r.pop("files", []):
        path = norm(str(f["path"]))
        if path.startswith("../") or Path(path).is_absolute() or ".." in Path(path).parts:
            r["rejected"].append({"path": path, "why": "outside the project"}); continue
        if is_protected(path, prot) or not in_scope(path, r["scope"]):
            r["rejected"].append({"path": path, "why": "Forge state" if is_protected(path, prot) else "outside the task scope"}); continue
        target = root / path
        why = real_target_problem(root, cfg, path)
        if why:
            r["rejected"].append({"path": path, "why": why}); continue
        raw = target.read_bytes().decode("utf-8", errors="replace") if target.is_file() else ""
        eol = "\r\n" if "\r\n" in raw else "\n"
        old = raw.replace("\r\n", "\n")
        new = str(f.get("content", "")).replace("\r\n", "\n")
        if old.endswith("\n") and new and not new.endswith("\n"):
            new += "\n"  # a model often drops the last newline; it is not a change
        if target.is_file() and new == old:
            continue  # unchanged; a NEW file is a change even when empty (an __init__.py)
        (sdir / path).parent.mkdir(parents=True, exist_ok=True)
        (sdir / path).write_bytes(new.replace("\n", eol).encode("utf-8"))  # the file keeps its own line endings
        r["proposed"][path] = sha(target)
        diff += difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                     fromfile=f"a/{path}" if target.is_file() else "/dev/null", tofile=f"b/{path}")
    patch = cfg["_layout"]["circle"] / "answers" / f"{oid}_{r['id']}.patch"
    if r["proposed"]:
        patch.write_text("".join(x if x.endswith("\n") else x + "\n\\ No newline at end of file\n" for x in diff),
                         encoding="utf-8", newline="\n")
        r["patch"] = patch.relative_to(root).as_posix()
    else:
        patch.unlink(missing_ok=True)
        r["patch"] = ""


def gpt_usage_log(cfg: dict, res: dict) -> None:
    log = cfg["_layout"]["state"] / "cache" / "gpt_usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"ts": res["when"], "tag": f"exec:{res['id']}", "model": res["model"], "effort": res["effort"],
                            "sandbox": "read-only", "seconds": res["seconds"], "ok": res["status"] == "done",
                            "usage": res["usage"], "error": res["error"][:200]}, ensure_ascii=False) + "\n")


def unexpected_writes(root: Path, cfg: dict, before: dict | None) -> list[str]:
    """Paths that changed while Codex ran read-only (should be none; the state folder is Forge's own runtime)."""
    after = git_tree(root)
    if before is None or after is None:
        return []
    state = cfg["_layout"]["state"].relative_to(root).as_posix() + "/" if cfg["_layout"]["state"] != root else "\0"
    changed = [p for p, fp in after.items() if before.get(p) != fp] + [p for p in before if p not in after]
    return sorted(p for p in changed if not p.lower().startswith(state.lower()))


def save_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def record(answers: Path, oid: str, results: list[dict], writes: list[str], kind: str = "proposal") -> None:
    ev = answers / f"{oid}_codex.json"
    data = load_json(ev, {"order": oid, "runs": []})
    data["runs"].append({"when": datetime.now().isoformat(timespec="seconds"), "kind": kind, "tasks": results,
                         "unexpected_writes": writes})
    save_json(ev, data)


def set_plan(plan_path: Path, updates: dict[str, dict]) -> None:
    """Merge per-task fields into the plan file (status, thread, ...); no plan file = nothing to update."""
    if not plan_path.is_file():
        return
    with _LOCK:
        plan = load_json(plan_path, {"tasks": []})
        for t in plan.get("tasks", []):
            t.update(updates.get(t["id"], {}))
        plan["updated"] = datetime.now().isoformat(timespec="seconds")
        save_json(plan_path, plan)


GRACE = 120  # seconds after a run's own timeout before a `running` task counts as cut off


def pid_alive(pid: int) -> bool:
    """True when the process may still run (unknown counts as alive - the deadline decides then)."""
    if not isinstance(pid, int) or pid <= 0:
        return True
    if pid == os.getpid():
        return True
    try:
        if os.name == "nt":  # os.kill(pid, 0) sends CTRL_C on Windows - ask the kernel instead
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return not ok or code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, AttributeError):
        return True


def reap_stale(plan_path: Path, now: datetime | None = None) -> list[str]:
    """Tasks left `running` by a run that was cut off (process gone, or past its deadline) -> `failed`; returns their ids."""
    if not plan_path.is_file():
        return []
    now = now or datetime.now()
    with _LOCK:
        plan = load_json(plan_path, {"tasks": []})
        cut = []
        for t in plan.get("tasks", []):
            if t.get("status") != "running":
                continue
            try:
                deadline = datetime.fromisoformat(t.get("deadline", ""))
            except ValueError:
                deadline = None
            gone = not pid_alive(t.get("pid", 0))
            if gone or deadline is None or now > deadline:
                why = "process gone" if gone else ("no deadline recorded" if deadline is None else f"past its deadline {t['deadline']}")
                t.update(status="failed", error=f"cut off ({why}) - the run did not finish; re-run the task (`wave` / `fix`)")
                cut.append(t["id"])
        if cut:
            plan["updated"] = now.isoformat(timespec="seconds")
            save_json(plan_path, plan)
    return cut


def failures(answers: Path, oid: str, tid: str) -> int:
    """How many proposal runs of this task ended failed or partial (K21: the second one stops the task)."""
    runs = load_json(answers / f"{oid}_codex.json", {"runs": []})["runs"]
    return sum(1 for run in runs if run.get("kind", "proposal") == "proposal"
               for t in run["tasks"] if t["id"] == tid and t.get("status") in ("failed", "partial"))


def next_step(root: Path | None, oid: str, tid: str, fails: int, limit_hit: bool = False) -> str:
    """What the Lead does with a failed task: HOLD after the second failure, else what the limits say (K37).
    A run that just hit Codex's limit counts Codex as exhausted, whatever the last log reading says."""
    if fails >= 2:
        return (f"HOLD: {tid} failed {fails} times - `order.py hold {oid} --why \"{tid}: Codex failed twice\"` and ask the owner "
                f"(K21); do not move it to another executor on your own")
    import limits
    codex = limits.codex_limits()
    if limit_hit:
        codex = {"provider": "codex", "seen_at": datetime.now().isoformat(timespec="seconds"),
                 "five_hour": {"used": 100.0, "resets_at": "", "reset_since_reading": False}, "seven_day": None}
    r = limits.route("code", root=root, claude=limits.claude_limits(), codex=codex)
    if r["decision"] == "CODEX":
        return f"next: `codex_exec.py fix {oid} {tid} --feedback \"...\"` - route keeps it on Codex ({r['reason']})"
    if r["decision"] == "HEAVY":
        return f"next: give {tid} to the `executor` agent (Claude), same task and scope - route: {r['reason']}"
    return f"next: {r['decision']} - {r['reason']}"


def preflight(root: Path, cfg: dict, *, force: bool = False) -> None:
    if run_by_codex():
        raise SystemExit("K29: refused inside a Codex session - the Lead (Claude) dispatches Codex, Codex does not dispatch itself")
    try:
        gpt_bridge.check_paths([root])
    except gpt_bridge.PolicyError as e:
        raise SystemExit(str(e) + " - tier CODEX is not available here: use FAST/HEAVY (Claude)")
    import limits
    cx = limits.codex_limits()
    lv = limits.level(limits.pressure(cx))
    if lv in ("critical", "exhausted") and not force and os.environ.get("FORGE_CODEX_FORCE") != "1":
        raise SystemExit(f"Codex: limit {lv} ({limits.fmt(cx, 'Codex')}) - "
                         f"route: python {Path(__file__).with_name('limits.py').as_posix()} route --kind code - "
                         "Claude executor (agent executor) instead, or run with --force (or set FORGE_CODEX_FORCE=1) to override")



def positive_int(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text!r}: a whole number of 1 or more")
    return n


def propose(root: Path, cfg: dict, oid: str, tasks: list[dict], *, slots: int, effort: str, model: str | None, timeout: int,
            plan_path: Path, resume: str = "", feedback: str = "") -> tuple[list[dict], list[str]]:
    """Run Codex read-only on the tasks (in parallel up to `slots`), stage what it proposes, record the evidence."""
    for i, a in enumerate(tasks):  # scopes of one wave must not overlap: two proposals never touch the same file
        for b in tasks[i + 1:]:
            if any(in_scope(s, b["scope"]) or in_scope(t, a["scope"]) for s in a["scope"] for t in b["scope"]):
                raise SystemExit(f"{a['id']} and {b['id']} share files in one wave - add `depends` or split the scopes")
    md = order_file(root, cfg, oid).read_text(encoding="utf-8")
    label = f"codex_exec {'fix' if resume else 'run'} {oid} {','.join(t['id'] for t in tasks)}"
    try:  # 1.1.2 (N028): never start Codex while the owner's Codex app has lost its folder rights
        acl = codex_safety.before_run(label)
    except codex_safety.CodexBlocked as e:
        raise SystemExit(str(e)) from None
    started = datetime.now()
    deadline = (started + timedelta(seconds=timeout + GRACE)).isoformat(timespec="seconds")
    set_plan(plan_path, {t["id"]: {"status": "running", "started": started.isoformat(timespec="seconds"), "deadline": deadline,
                                   "pid": os.getpid()} for t in tasks})  # before the snapshot: our own write is not Codex's
    print(f"{oid}: {len(tasks)} Codex task(s) started {started:%H:%M:%S} (deadline {deadline[11:]}) - "
          f"if this call is cut off: codex_exec.py status {oid}", flush=True)
    before = git_tree(root)
    results: list[dict] = []
    writes: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, slots)) as pool:
            results = list(pool.map(lambda t: run_task(root, cfg, oid, md, t, effort=effort, model=model, timeout=timeout,
                                                        resume=resume, feedback=feedback), tasks))
        writes = unexpected_writes(root, cfg, before)  # before the guard below may write its lesson into the project
    finally:  # re-check after every run, also when it was interrupted
        sys.stdout.flush()
        try:
            codex = " ".join(codex_cmd())
        except FileNotFoundError:
            codex = "(not found)"
        evidence = (cfg["_layout"]["circle"] / "answers" / f"{oid}_codex.json").relative_to(root).as_posix()
        alert = codex_safety.after_run(acl, label, {
            "tool": "codex_exec", "order": oid, "codex": codex, "windows_sandbox": windows_sandbox(cfg), "project": str(root),
            "started": started.isoformat(timespec="seconds"), "ended": datetime.now().isoformat(timespec="seconds"),
            "tasks": [{k: r.get(k) for k in ("id", "status", "thread", "seconds", "error", "usage", "model")} for r in results]},
            root=root, evidence=[evidence])
        for r in results if alert else []:
            r["acl_alert"] = alert["incident"]  # recorded in answers/N_codex.json next to the run
    for r in results:
        stage(root, cfg, oid, r)
        gpt_usage_log(cfg, r)
    set_plan(plan_path, {r["id"]: {"status": "proposed" if r["status"] == "done" and r["proposed"] else r["status"],
                                   "thread": r["thread"], "patch": r["patch"]} for r in results})
    record(cfg["_layout"]["circle"] / "answers", oid, results, writes)
    return results, writes


def report(results: list[dict], writes: list[str], oid: str, root: Path | None = None, answers: Path | None = None) -> None:
    for r in results:
        mark = {"done": "PROPOSED", "partial": "PARTIAL", "blocked": "BLOCKED"}.get(r["status"], "FAILED")
        tok = (r["usage"].get("input_tokens", 0) + r["usage"].get("output_tokens", 0)) if r["usage"] else 0
        print(f"{r['id']} {mark} {r['seconds']}s {tok // 1000}k tok - {r['summary'][:160] or r['error'][:160]}")
        if r.get("patch"):
            print(f"   read: {r['patch']} ({len(r['proposed'])} file(s)) -> then: codex_exec.py apply {oid} {r['id']}")
        for x in r.get("rejected", []):
            print(f"   rejected: {x['path']} ({x['why']})")
        if r["question"]:
            print(f"   question: {r['question'][:300]}")
        for d in r.get("decisions", [])[:6]:
            print(f"   decision: {d[:200]}")
        for key, label in (("beyond_task", "beyond the task"), ("irreversible", "IRREVERSIBLE"), ("memory", "memory")):
            if r.get(key):
                print(f"   {label}: {r[key][:300]}")
        if r["status"] not in ("done", "blocked"):
            fails = failures(answers, oid, r["id"]) if answers else 1
            print("   " + ("(Codex limit reached) " if r["limit"] else "") + next_step(root, oid, r["id"], fails, r["limit"]))
    if writes:
        print(f"K34d: {len(writes)} path(s) changed while Codex ran read-only - check them: " + "; ".join(writes[:8]))
    alert = next((r["acl_alert"] for r in results if r.get("acl_alert")), "")
    if alert:  # 1.1.2: the loud warning went to stderr during the run; the last word repeats it
        print(f"ВНИМАНИЕ: во время этого запуска Codex пропали права папки Packages (приложение Codex не запустится; причина "
              f"не доказана) - Codex заблокирован, пока владелец не исправит права; доказательства: {alert}")
    print(f"evidence: answers/{oid}_codex.json")


def latest(answers: Path, oid: str, tid: str) -> dict | None:
    runs = load_json(answers / f"{oid}_codex.json", {"runs": []})["runs"]
    return next((t for run in reversed(runs) if run.get("kind", "proposal") == "proposal" for t in run["tasks"] if t["id"] == tid), None)



def stale_problem(root: Path, cfg: dict, oid: str, tid: str, r: dict) -> str:
    """'' when every proposed file is still as Codex saw it and lands inside the project; else why `apply` must stop."""
    moved = [p for p, base in r["proposed"].items() if sha(root / p) != base]
    if moved:
        return (f"{oid} {tid}: {', '.join(moved)} changed since Codex proposed - re-run the task (`fix`), never "
                f"overwrite someone else's change")
    links = [f"{p} ({why})" for p in r["proposed"] if (why := real_target_problem(root, cfg, p))]
    return f"{oid} {tid}: refused - {'; '.join(links)}" if links else ""


def apply(root: Path, cfg: dict, oid: str, tids: list[str], plan_path: Path, skip_checks: str = "") -> list[dict]:
    """Write the staged proposal of each task into the project - only if the files are unchanged since the proposal."""
    answers = cfg["_layout"]["circle"] / "answers"
    done = []
    for tid in tids:
        r = latest(answers, oid, tid)
        if r is None or not r.get("proposed"):
            raise SystemExit(f"{oid} {tid}: no staged proposal - run the task first (`run` / `wave` / `fix`)")
        if r.get("applied"):
            raise SystemExit(f"{oid} {tid}: already applied at {r['applied']}")
        sdir = cfg["_layout"]["state"] / "staging" / "codex" / oid / tid
        problem = stale_problem(root, cfg, oid, tid, r)
        if problem:
            raise SystemExit(problem)
        missing = [p for p in r["proposed"] if not (sdir / p).is_file()]
        if missing:
            raise SystemExit(f"{oid} {tid}: staged copy missing for {', '.join(missing)} - re-run the task")
        task = plan_task(plan_path, tid)
        md = order_file(root, cfg, oid).read_text(encoding="utf-8")
        scope = list(r.get("scope") or task.get("scope") or [])
        need = tests_required(task, md, scope)
        commands = declared_checks(task)
        if skip_checks:
            if need or commands:
                journal_override(cfg, oid, f"{tid}: apply checks skipped (--skip-checks): {one_line(skip_checks)}")
        else:
            if need:
                named = declared_test_paths(task)
                absent = [w for w in named if not any(in_scope(p, [w]) for p in r["proposed"])]
                if absent:
                    raise SystemExit(f"{oid} {tid}: refused - {need}, but the patch does not change {', '.join(absent)}. "
                                     f"Send it back (`fix`) with them, or override with --skip-checks \"reason\"")
                if not named and not any(is_test_path(p) for p in r["proposed"]):
                    raise SystemExit(f"{oid} {tid}: refused - {need}, but the patch changes no test file ({', '.join(r['proposed'])}). "
                                     f"Send it back (`fix`) with the tests, or override with --skip-checks \"reason\"")
                if not commands:  # tests are required: they must also run, not only exist
                    test_cmd = (cfg.get("code") or {}).get("test", "")
                    if not (isinstance(test_cmd, str) and test_cmd.strip()):
                        raise SystemExit(f"{oid} {tid}: refused - {need}, but nothing would run them: declare `check` on the "
                                         f"task or `code.test` in forge.json, or override with --skip-checks \"reason\"")
                    commands = [test_cmd.strip()]
            note = tests_note(task, md, scope)
            if note:
                print(f"{tid}: warning - {note}", file=sys.stderr, flush=True)
            if commands:
                print(f"{tid}: running {len(commands)} check(s) on the patched copy ...", flush=True)
                bad = run_checks_on_patch(root, cfg, sdir, list(r["proposed"]), commands)
                if bad:
                    raise SystemExit(f"{oid} {tid}: refused - the declared check failed on the patched result; nothing was written.\n"
                                     f"{bad}\nSend it back (`fix`), or override with --skip-checks \"reason\"")
        problem = stale_problem(root, cfg, oid, tid, r)  # the checks took minutes: nobody may have written these files meanwhile
        if problem:
            raise SystemExit(problem)
        for p in r["proposed"]:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sdir / p, root / p)
        when = datetime.now().isoformat(timespec="seconds")
        data = load_json(answers / f"{oid}_codex.json", {"runs": []})
        for run in reversed(data["runs"]):  # mark the proposal that was applied
            hit = next((t for t in run["tasks"] if t["id"] == tid and t.get("proposed") and not t.get("applied")), None)
            if hit:
                hit["applied"] = when
                if skip_checks and (need or commands):
                    hit["checks_skipped"] = skip_checks
                elif need or commands:
                    hit["acceptance"] = {"tests_required": bool(need), "checks": commands}
                break
        save_json(answers / f"{oid}_codex.json", data)
        set_plan(plan_path, {tid: {"status": "applied"}})
        print(f"{tid} APPLIED {len(r['proposed'])} file(s): {', '.join(r['proposed'])}", flush=True)
        done.append({**r, "applied": when})
    return done


def record_answer(root: Path, cfg: dict, oid: str, plan_path: Path | None = None) -> None:
    """The order's answer from every APPLIED task (the files that really changed the project)."""
    runs = load_json(cfg["_layout"]["circle"] / "answers" / f"{oid}_codex.json", {"runs": []})["runs"]
    applied = [t for run in runs for t in run["tasks"] if t.get("applied")]
    if not applied:
        raise SystemExit(f"{oid}: nothing applied yet - `apply` the proposals you accepted first")
    files = sorted({p for t in applied for p in t["proposed"] if (root / p).exists()})
    model = applied[-1].get("model", "codex")
    plan = load_json(plan_path, {"tasks": []}).get("tasks", []) if plan_path else []
    left = [t["id"] for t in plan if t.get("status") != "applied"]
    status = "partial" if left else "done"
    summary = " | ".join(f"{t['id']}: {t['summary'][:200]}" for t in applied)[:1800]
    checks = "; ".join(c for t in applied for c in t.get("checks_run", []))[:900]
    cmd = [sys.executable, str(Path(__file__).with_name("order.py")), "answer", oid, "--executor", f"codex:{model}", "--tier", "CODEX",
           "--status", status, "--summary", (summary or "(no summary)") + (f" | not applied yet: {', '.join(left)}" if left else ""),
           "--files", ",".join(files)]
    if checks:
        cmd += ["--checks", checks]
    sys.stdout.flush()
    subprocess.run(cmd, cwd=root, check=False)


def main() -> int:
    if "--selftest" in sys.argv:
        try:
            print("codex:", " ".join(codex_cmd()))
        except FileNotFoundError as e:
            print(e)
        print(codex_safety.summary())
        try:
            root = find_root(Path.cwd())
            cfg = load_cfg(root)
            preflight(root, cfg)
            print(f"project {root.name}: tier CODEX available (K15 allowed), patch mode")
        except SystemExit as e:
            print(e)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh"])
    common.add_argument("--model"); common.add_argument("--timeout", type=int, default=1800)
    common.add_argument("--plan")
    common.add_argument("--force", action="store_true", help="override critical/exhausted Codex limits")
    r = sub.add_parser("run", parents=[common]); r.add_argument("id"); r.add_argument("--task", required=True)
    r.add_argument("--scope", required=True); r.add_argument("--context", default=""); r.add_argument("--tid", "--task-id", dest="tid", default="T1")
    w = sub.add_parser("wave", parents=[common]); w.add_argument("id"); w.add_argument("--slots", type=positive_int, default=3)
    f = sub.add_parser("fix", parents=[common]); f.add_argument("id"); f.add_argument("tid"); f.add_argument("--feedback", required=True)
    ap_ = sub.add_parser("apply"); ap_.add_argument("id"); ap_.add_argument("tids", nargs="*"); ap_.add_argument("--all", action="store_true")
    ap_.add_argument("--plan"); ap_.add_argument("--answer", action="store_true", help="record the order's answer from the applied files")
    ap_.add_argument("--skip-checks", default="", metavar="REASON", help="apply without the test-file and declared-check acceptance; the reason is journaled")
    s = sub.add_parser("status"); s.add_argument("id"); s.add_argument("--plan")
    a = ap.parse_args()
    root = find_root(Path.cwd())
    cfg = load_cfg(root)
    answers = cfg["_layout"]["circle"] / "answers"
    plan_path = Path(a.plan or answers / f"{a.id}_plan.json")
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    if a.cmd in ("status", "wave", "fix"):
        cut = reap_stale(plan_path)
        if cut:
            print(f"cut off earlier (process gone or past the deadline) -> failed: {', '.join(cut)}", flush=True)
    if a.cmd == "status":
        if not plan_path.is_file():
            runs = load_json(answers / f"{a.id}_codex.json", {"runs": []})["runs"]
            tasks = [t for run in runs if run.get("kind", "proposal") == "proposal" for t in run["tasks"]]
            print(f"no plan file (single `run` tasks): {len(tasks)} proposal(s), {sum(bool(t.get('applied')) for t in tasks)} applied "
                  f"- details in answers/{a.id}_codex.json")
            return 0
        plan = load_json(plan_path, {"tasks": []})
        for t in plan.get("tasks", []):
            print(f"{t['id']:<4} {t.get('status', 'pending'):<9} {', '.join(t.get('scope', []))[:70]}  {t['task'][:60]}")
        tasks = plan.get("tasks", [])
        running = [t["id"] for t in tasks if t.get("status") == "running"]
        print(f"{sum(t.get('status') == 'applied' for t in tasks)}/{len(tasks)} applied, "
              f"{sum(t.get('status') == 'proposed' for t in tasks)} waiting for the Lead"
              + (f", running now: {', '.join(running)}" if running else ""))
        return 0
    if a.cmd == "apply":
        if a.all:
            tids = [t["id"] for t in load_json(plan_path, {"tasks": []}).get("tasks", []) if t.get("status") == "proposed"]
        else:
            tids = a.tids
        if not tids:
            raise SystemExit("nothing to apply: name the tasks (after reading their .patch files) or --all")
        if "--skip-checks" in sys.argv and not a.skip_checks.strip():
            raise SystemExit("--skip-checks needs a reason in words (it is journaled)")
        apply(root, cfg, a.id, tids, plan_path, a.skip_checks.strip())
        if a.answer:
            record_answer(root, cfg, a.id, plan_path)
        return 0
    preflight(root, cfg, force=a.force)
    effort = a.effort or (cfg.get("code") or {}).get("codex_effort") or "medium"
    if a.cmd == "run":
        task = {"id": a.tid, "task": a.task, "scope": [x.strip() for x in a.scope.split(",") if x.strip()],
                "context": [x.strip() for x in a.context.split(",") if x.strip()]}
        results, writes = propose(root, cfg, a.id, [task], slots=1, effort=effort, model=a.model, timeout=a.timeout, plan_path=plan_path)
    elif a.cmd == "fix":
        task = next((t for t in load_json(plan_path, {"tasks": []}).get("tasks", []) if t["id"] == a.tid), None)
        prev = latest(answers, a.id, a.tid)
        thread = (task or {}).get("thread") or (prev or {}).get("thread", "")
        if not thread:
            raise SystemExit(f"{a.id} {a.tid}: no Codex thread recorded - run the task again with `run`")
        task = task or {"id": a.tid, "task": prev["task"], "scope": prev["scope"]}
        results, writes = propose(root, cfg, a.id, [task], slots=1, effort=effort, model=a.model, timeout=a.timeout,
                                  plan_path=plan_path, resume=thread, feedback=a.feedback)
    else:
        plan = load_json(plan_path, None)
        if not plan or not plan.get("tasks"):
            raise SystemExit(f"no tasks in {plan_path.as_posix()} - the Lead writes the plan first (see the docstring)")
        bad = plan_problems(plan["tasks"])  # a plan that no result can satisfy is refused before Codex spends a run on it
        if bad:
            raise SystemExit("wave refused: the plan cannot be accepted as written:\n- " + "\n- ".join(bad))
        applied = {t["id"] for t in plan["tasks"] if t.get("status") == "applied"}
        ready = [t for t in plan["tasks"] if t.get("status", "pending") in ("pending", "failed", "partial")
                 and all(d in applied for d in t.get("depends", []))]
        if not ready:
            waiting = [t["id"] for t in plan["tasks"] if t.get("status") == "proposed"]
            running = [t["id"] for t in plan["tasks"] if t.get("status") == "running"]
            left = [t["id"] for t in plan["tasks"] if t.get("status") not in ("applied", "proposed", "running")]
            why = [x for x in (
                f"apply or fix the proposals first ({', '.join(waiting)})" if waiting else "",
                f"still running ({', '.join(running)}) - wait for that run, or `status` once it was cut off" if running else "",
                f"waiting on dependencies ({', '.join(left)})" if left else "") if x]
            print("no task is ready: " + ("; ".join(why) or "all applied"))
            return 0
        slots, why = wave_capacity(a.slots)  # K28: never more parallel Codex runs than the live machine load allows
        if why:
            print(why)
        if slots <= 0:
            raise SystemExit(f"wave refused: {why or 'no free slots.'} Waiting is not allowed; re-check: "
                             f"python {(Path(__file__).resolve().parent / 'machine.py').as_posix()} load")
        results, writes = propose(root, cfg, a.id, ready, slots=slots, effort=effort, model=a.model, timeout=a.timeout,
                                  plan_path=plan_path)
    report(results, writes, a.id, root, answers)
    return 0 if all(r["status"] == "done" for r in results) and not writes and not any(r.get("acl_alert") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
