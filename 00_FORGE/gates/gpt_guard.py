"""K15 guard as a PreToolUse hook - Forge 1.0: nothing reaches ChatGPT/Codex from a project the owner did not open to it.

Forge's own bridges (gpt_bridge, verify_gpt, gpt_media, codex_exec) check K15 themselves. This hook covers the doors around
them: the official Codex plugin for Claude Code (`codex:*` agents, skills and commands) and a bare `codex` in the shell.
A call is refused when the session's folder, or a folder the command names (`cd X`, `pushd X`, `Set-Location X`,
`-C X` / `--cd X`, any absolute path in it), is under the owner's deny list (`~/.forge/gpt_policy.json`, e.g. personal
finance) or inside a Forge project whose forge.json does not say "gpt": "allowed". Outside Forge projects nothing is
refused (the owner's own use). An unreadable policy file refuses (the guard cannot tell what is denied). Any other
internal error fails open: a broken guard never blocks the session, and the bridges still enforce K15.
Limit: a folder reached in ways the command does not spell (a variable, a script that changes directory) is not seen.

1.1.0: the same hook runs the project's lesson guards on shell commands (tools/guards.py, `on: bash`): `block` denies the
command with the guard's message, `warn` lets it run and adds the message to the Lead's context. One process per call.

Hook input (stdin JSON): tool_name, tool_input, cwd. Output on refusal: the PreToolUse deny decision.
Usage: python gpt_guard.py --hook   |   python gpt_guard.py --check <folder>   (prints allowed / the reason)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):  # 1.1.1: the deny reason names folders - UTF-8 for Claude Code, not the Windows locale
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CODEX_CLI = re.compile(r"(?i)(^|[\s;&|(\"'`/\\])codex(\.exe|\.cmd)?(?=\s|$|[\"'`;&|)])|codex-companion")
DIR_ARG = re.compile(r"""(?ix)(?:^|[\s;&|(])(?:cd|pushd|set-location|sl|chdir)\s+(?:-path\s+)?("[^"]+"|'[^']+'|[^\s;&|)]+)
                         |(?:^|\s)(?:-C|--cd)[\s=]+("[^"]+"|'[^']+'|[^\s;&|)]+)""")
ABS_PATH = re.compile(r"""(?i)("[a-z]:[\\/][^"]*"|'[a-z]:[\\/][^']*'|\b[a-z]:[\\/][^\s"';&|)]*)""")


def uses_codex(tool: str, inp: dict) -> bool:
    if tool in ("Agent", "Task"):
        return str(inp.get("subagent_type", "")).lower().startswith("codex:")
    if tool in ("Skill", "SlashCommand"):
        return str(inp.get("skill") or inp.get("command") or "").lstrip("/").lower().startswith("codex:")
    if tool in ("Bash", "PowerShell"):
        return bool(CODEX_CLI.search(str(inp.get("command", ""))))  # Forge's bridges have no bare `codex` token
    return False


def named_folders(command: str, cwd: str) -> list[Path]:
    """Folders a shell command names: cd/pushd/Set-Location/-C/--cd targets (relative to cwd) and absolute paths."""
    out = []
    for m in DIR_ARG.finditer(command):
        raw = (m.group(1) or m.group(2) or "").strip("\"'")
        if raw and not raw.startswith("$"):
            msys = re.match(r"^/([a-zA-Z])(/.*)?$", raw)  # Git Bash spells C:\ai as /c/ai
            if msys:
                raw = f"{msys.group(1)}:{msys.group(2) or '/'}"
            out.append(Path(raw) if Path(raw).is_absolute() else Path(cwd) / raw)
    for m in ABS_PATH.finditer(command):
        out.append(Path(m.group(1).strip("\"'")))
    return out


def _norm(p: str | Path) -> str:
    return str(Path(p).resolve()).replace("\\", "/").rstrip("/").lower()


def refusal(folder: str | Path) -> str:
    """'' when ChatGPT may be used from `folder`, else the reason (K15)."""
    pol = Path(os.environ.get("FORGE_GPT_POLICY") or Path.home() / ".forge" / "gpt_policy.json")
    try:
        deny = json.loads(pol.read_text(encoding="utf-8")).get("deny", []) if pol.is_file() else []
        if not isinstance(deny, list) or not all(isinstance(d, str) for d in deny):
            raise TypeError("'deny' must be a list of folder paths")
    except (OSError, ValueError, AttributeError, TypeError) as e:  # cannot tell what is denied -> refuse, never guess (N024 r1/r2)
        return f"K15: the owner's policy file {pol} is unreadable ({type(e).__name__}: {e}) - fix it before anything goes to ChatGPT/Codex"
    here = _norm(folder)
    for d in deny:
        n = _norm(d)
        if here == n or here.startswith(n + "/"):
            return f"K15: {folder} is on the owner's deny list ({pol}) - nothing from here goes to ChatGPT/Codex"
    start = Path(folder).resolve()
    for d in [start, *start.parents]:
        cfg = d / "forge.json"
        if cfg.is_file():
            try:
                allowed = str(json.loads(cfg.read_text(encoding="utf-8")).get("gpt", "")).lower() == "allowed"
            except (OSError, ValueError):
                allowed = False
            if allowed:
                return ""
            return (f'K15: ChatGPT/Codex is not allowed in the Forge project {d.name} (forge.json "gpt") - ask the owner; '
                    f"after a yes: gpt_setup.py --allow. Use a Claude executor (FAST/HEAVY) instead")
    return ""


def decide(tool: str, inp: dict, cwd: str) -> str:
    """The refusal reason for this call, or '' to let it pass."""
    if not uses_codex(tool, inp):
        return ""
    folders = [Path(cwd)] + (named_folders(str(inp.get("command", "")), cwd) if tool in ("Bash", "PowerShell") else [])
    for f in folders:
        why = refusal(f)
        if why:
            return why
    return ""


def main() -> int:
    if "--check" in sys.argv:
        folder = sys.argv[sys.argv.index("--check") + 1] if len(sys.argv) > sys.argv.index("--check") + 1 else "."
        print(refusal(folder) or "allowed")
        return 0
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")  # 1.1.1: UTF-8, not the Windows locale
        tool, inp, cwd = data.get("tool_name", ""), data.get("tool_input") or {}, data.get("cwd") or os.getcwd()
        why = decide(tool, inp, cwd)
        if why:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": why}}, ensure_ascii=False))
            return 0
        if tool in ("Bash", "PowerShell"):  # D3 (1.1.0): lessons that became guards
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
            from guards import check_hook
            action, msg = check_hook("bash", str(inp.get("command", "")), cwd)
            if action == "block":
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                         "permissionDecisionReason": msg}}, ensure_ascii=False))
            elif action == "warn":
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}},
                                 ensure_ascii=False))
    except Exception as e:  # fail-open on the guard's own bugs: the bridges enforce K15 on their own
        print(f"gpt_guard: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
