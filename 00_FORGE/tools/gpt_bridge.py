"""Bridge to ChatGPT through the Codex CLI (`codex exec`) - Forge 0.8 (K15, K29-K31; from the N008 pilot in the workspace).

ChatGPT in Forge is an independent auditor, an adviser and an image maker - never the Lead (K29). One call = one
non-interactive Codex turn in an isolated work folder:
- stdin closed (codex otherwise waits on stdin forever); hard timeout; ephemeral session;
- `--ignore-user-config` (no owner plugins/MCP/AGENTS.md leak in) + a `windows.sandbox` mode (with no mode every shell
  command is blocked on Windows): "unelevated" for read-only runs - audits, advice, fact checks (1.1.2, owner's yes; an
  audit and a fact check were measured in N028/N029: they work and change no ACL; advice shares the audit's read path and
  was not run separately) - and "elevated" for runs that write (images), which were not measured; env
  FORGE_GPT_WINDOWS_SANDBOX = elevated | unelevated | none overrides both; read-only sandbox unless the caller writes (images);
- optional JSON-schema answer, last message via `-o`, usage parsed from `--json` events, optional images (`-i`);
- K15 before anything is sent: the project's forge.json must say "gpt": "allowed" (the owner's yes) and no path may be
  under the owner's deny list `~/.forge/gpt_policy.json` (env FORGE_GPT_POLICY overrides the location);
- refused inside a Codex session (no ChatGPT-calls-ChatGPT loops);
- every call is appended to `<state>/cache/gpt_usage.jsonl` (or `~/.forge/gpt_usage.jsonl` outside a project);
- 1.1.2 (N028): every call is bracketed by codex_safety - refused while %LOCALAPPDATA%\\Packages lacks SYSTEM/Administrators
  (the owner's Codex app cannot start then); a call during which they vanish is named, blocked and recorded.
Known limit: Codex's read-only sandbox may still read other files on disk - isolation is by folder and instruction.

Usage: python gpt_bridge.py --selftest            # codex found? policy? (no network)
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import gpt_allowed, layout, run_by_codex  # noqa: E402
import codex_safety  # noqa: E402

WORK_ROOT = Path(tempfile.gettempdir()) / "forge_gpt_work"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
WINDOWS_SANDBOX = {"read-only": "unelevated"}  # `windows.sandbox` by run sandbox (N029, owner's yes); any other run: "elevated"


OVERRIDES = {"elevated": "elevated", "unelevated": "unelevated", "none": "", "": ""}


def windows_sandbox(sandbox: str) -> str:
    """The `windows.sandbox` mode for a run with this sandbox; "" = none passed (not Windows, or the override says so).
    FORGE_GPT_WINDOWS_SANDBOX = elevated | unelevated | none is the owner's rollback without a code edit; `none` passes no
    mode, which blocks every shell command on Windows (N028 measurement) - a diagnostic setting, not a fix."""
    if os.name != "nt":
        return ""
    raw = os.environ.get("FORGE_GPT_WINDOWS_SANDBOX")
    if raw is not None:
        value = raw.strip().strip('"').lower()
        if value in OVERRIDES:
            return OVERRIDES[value]
        print(f"gpt_bridge: FORGE_GPT_WINDOWS_SANDBOX={raw!r} is not elevated, unelevated or none - ignored", file=sys.stderr)
    return WINDOWS_SANDBOX.get(sandbox, "elevated")


class PolicyError(RuntimeError):
    pass


def policy_file() -> Path:
    return Path(os.environ.get("FORGE_GPT_POLICY") or Path.home() / ".forge" / "gpt_policy.json")


def load_policy() -> dict:
    f = policy_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"deny": []}


def find_codex() -> str:
    """FORGE_CODEX > codex on PATH > newest %LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe (desktop app install)."""
    env = os.environ.get("FORGE_CODEX")
    if env and Path(env).is_file():
        return env
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    found = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe"))
    if found:
        return max(found, key=os.path.getmtime)
    raise FileNotFoundError("codex CLI not found: install the Codex app (ChatGPT login) or set FORGE_CODEX")


def default_model() -> str | None:
    """The first listed model of this account (Codex's own catalogue); None = let Codex choose."""
    if os.environ.get("FORGE_GPT_MODEL"):
        return os.environ["FORGE_GPT_MODEL"]
    try:
        models = json.loads((CODEX_HOME / "models_cache.json").read_text(encoding="utf-8")).get("models", [])
        listed = [m for m in models if m.get("visibility") == "list" and m.get("slug")]
        return min(listed, key=lambda m: m.get("priority", 99))["slug"] if listed else None
    except (OSError, ValueError):
        return None


def _norm(p: str | Path) -> str:
    return str(Path(p).resolve()).replace("\\", "/").rstrip("/").lower()


def _under(n: str, root: str) -> bool:
    return n == root or n.startswith(root + "/")


def project_root(path: str | Path) -> Path | None:
    for d in [Path(path).resolve(), *Path(path).resolve().parents]:
        if (d / "forge.json").is_file():
            return d
    return None


def check_paths(paths: list[str | Path]) -> None:
    """K15: every path must belong to a Forge project whose owner allowed ChatGPT (or be a bridge work folder),
    and none may sit under the owner's deny list."""
    deny = [_norm(d) for d in load_policy().get("deny", [])]
    work = _norm(WORK_ROOT)
    for p in paths:
        n = _norm(p)
        if any(_under(n, d) for d in deny):
            raise PolicyError(f"K15: {p} is on the owner's deny list ({policy_file()}) - never sent to a cloud model")
        if _under(n, work):
            continue
        root = project_root(p)
        if root is None:
            raise PolicyError(f"K15: {p} is not inside a Forge project - nothing outside projects goes to ChatGPT")
        cfg = json.loads((root / "forge.json").read_text(encoding="utf-8"))
        if not gpt_allowed(cfg):
            raise PolicyError(f'K15: ChatGPT is not allowed in {root.name} - ask the owner, then gpt_setup.py --allow (forge.json "gpt")')


def usage_log(near: str | Path | None) -> Path:
    root = project_root(near) if near else None
    if root:
        return layout(root)["state"] / "cache" / "gpt_usage.jsonl"
    return Path.home() / ".forge" / "gpt_usage.jsonl"


def new_workdir(tag: str) -> Path:
    wd = WORK_ROOT / f"{tag}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    wd.mkdir(parents=True, exist_ok=False)
    return wd


def run(prompt: str, workdir: str | Path, *, schema: dict | None = None, sandbox: str = "read-only", effort: str = "high",
        model: str | None = None, images: list[str | Path] | None = None, timeout: int = 900, tag: str = "",
        log_near: str | Path | None = None) -> dict:
    """One Codex turn in `workdir` -> {ok, exit, seconds, answer, parsed, usage, model, effort, error, thread}."""
    if run_by_codex():
        raise PolicyError("K29: refused inside a Codex session - ChatGPT is called by the Lead (Claude), not by itself")
    workdir = Path(workdir).resolve()
    check_paths([workdir] + list(images or []))
    codex = find_codex()  # before the guard: a missing CLI is no started run (no open "before" in the rights log)
    label = f"gpt_bridge {tag or 'call'}"
    try:  # 1.1.2 (N028): never start Codex while the owner's Codex app has lost its folder rights
        acl = codex_safety.before_run(label)
    except codex_safety.CodexBlocked as e:
        print(str(e), file=sys.stderr, flush=True)
        raise PolicyError("Codex blocked by tools/codex_safety.py (the rights of %LOCALAPPDATA%\\Packages, the Codex app's "
                          "folder) - the reason and the owner's fix are printed above") from None
    model = model or default_model()
    tmp = Path(tempfile.mkdtemp(prefix="forge_gpt_io_"))
    last = tmp / "last.txt"
    cmd = [codex, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "-s", sandbox, "-C", str(workdir),
           "-c", f'model_reasoning_effort="{effort}"', "--json", "-o", str(last)]
    if model:
        cmd += ["-m", model]
    winbox = windows_sandbox(sandbox)
    if winbox:
        cmd += ["-c", f'windows.sandbox="{winbox}"']
    for im in images or []:
        cmd += ["-i", str(im)]
    if schema is not None:
        sp = tmp / "schema.json"
        sp.write_text(json.dumps(schema), encoding="utf-8")
        cmd += ["--output-schema", str(sp)]
    cmd.append(prompt)
    t0 = time.time()
    res = {"ok": False, "exit": None, "seconds": 0.0, "answer": "", "parsed": None, "usage": {}, "model": model or "(codex default)",
           "effort": effort, "error": "", "thread": ""}
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
        res["exit"] = p.returncode
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                res["thread"] = ev.get("thread_id", "")
            if ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
                res["usage"] = ev["usage"]
            if ev.get("type") in ("error", "turn.failed"):
                res["error"] = json.dumps(ev, ensure_ascii=False)[:600]
        if last.is_file():
            res["answer"] = last.read_text(encoding="utf-8", errors="replace").strip()
        if schema is not None and res["answer"]:
            try:
                res["parsed"] = json.loads(res["answer"])
            except ValueError as e:
                res["error"] = res["error"] or f"answer is not JSON: {e}"
        res["ok"] = p.returncode == 0 and bool(res["answer"]) and (schema is None or res["parsed"] is not None)
        if not res["ok"] and not res["error"]:
            res["error"] = p.stderr.strip()[-600:]
    except subprocess.TimeoutExpired:
        res["error"] = f"timeout after {timeout}s"
    finally:  # 1.1.2: the usage line and the rights re-check also follow an interrupted run
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(tmp, ignore_errors=True)
        log = Path.home() / ".forge" / "gpt_usage.jsonl"
        try:
            log = usage_log(log_near)  # a malformed forge.json must not skip the rights re-check below
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "tag": tag, "workdir": str(workdir),
                                    "model": res["model"], "effort": effort, "sandbox": sandbox, "seconds": res["seconds"],
                                    "exit": res["exit"], "ok": res["ok"], "usage": res["usage"], "error": res["error"][:200]},
                                   ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - the usage line is bookkeeping; the re-check must still run
            pass
        alert = codex_safety.after_run(acl, label, {
            "tool": "gpt_bridge", "tag": tag, "order": next(iter(re.findall(r"\bN\d{3}\b", tag)), ""), "codex": cmd[0],
            "workdir": str(workdir), "sandbox": sandbox, "windows_sandbox": winbox, "exit": res["exit"], "seconds": res["seconds"],
            "thread": res["thread"], "usage": res["usage"], "error": res["error"][:600]},
            root=project_root(log_near) if log_near else project_root(workdir), evidence=[str(log)])
        if alert:
            res["acl_alert"] = alert["incident"]
    return res


def main() -> int:
    if "--selftest" not in sys.argv:
        print(__doc__); return 0
    try:
        print("codex:", find_codex())
    except FileNotFoundError as e:
        print(e)
    print("model:", default_model() or "(codex default)")
    print(f"windows.sandbox: read-only runs {windows_sandbox('read-only') or '-'}, writing runs {windows_sandbox('workspace-write') or '-'}")
    print(codex_safety.summary())
    print("deny list:", policy_file(), load_policy().get("deny", []))
    root = project_root(Path.cwd())
    if root:
        try:
            check_paths([root]); print(f"project {root.name}: ChatGPT allowed (K15)")
        except PolicyError as e:
            print(e)
    print("inside Codex:", run_by_codex())
    return 0


if __name__ == "__main__":
    sys.exit(main())
