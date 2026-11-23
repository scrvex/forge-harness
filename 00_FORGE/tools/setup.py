"""Forge 1.0 installer and doctor - what the code stack needs on this PC (from agent-playbook's installer; K33, K35).

`doctor` changes nothing: it lists every piece with its status and the exact command that fixes it.
`install` runs the missing steps one by one, printing each command first (the owner runs it - an agent's sandbox may
refuse to install software, which is right). Re-running is safe: present pieces are skipped.

Pieces:
  uv            Python tool manager (Serena, ruff, pyright live in its tool folder)       python -m pip install --user uv
  serena        symbol-level code navigation + project memories (MCP, user scope)         uv tool install -p 3.13 serena-agent
  context7      current library docs (MCP over HTTP, user scope, no key needed)            claude mcp add --transport http ...
  codex plugin  OpenAI's Codex plugin for Claude Code: /codex:review, /codex:rescue        claude plugin install codex@openai-codex
  ruff, pyright Python lint and types for gates/check_code.py                              uv tool install ruff / pyright
  forge         this plugin, user scope, at the source's version                           claude plugin update forge@forge-dev
  forge-band    the progress band above the prompt (a mod; Claude Code only)                claude plugin install forge-band@forge-dev
Also checked: Python, git, Node.js >= 18.18, the Codex CLI, env CLAUDE_CODE_EFFORT_LEVEL (it silently overrides agent effort).
1.1.0 (from ambatupipe's preflight): `python` on PATH is Python 3.10+ (every Forge hook runs `python`); Claude Code settings
that silently switch the hooks off (disableAllHooks, allowManagedHooksOnly) or answer the DoD question for the owner
(askUserQuestionTimeout); CLAUDE_CODE_EFFORT_LEVEL in a settings.json `env` block too; the Forge status line (limits for K37).
1.1.2 (N028): Windows - %LOCALAPPDATA%\\Packages keeps SYSTEM and Administrators (the Codex app starts only then); the fix is
printed for the owner, never run.

Usage: python setup.py doctor | python setup.py install [--only serena,context7]
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
HOME = Path.home()
UV_BIN = HOME / ".local" / "bin"
EXE = ".exe" if os.name == "nt" else ""
CONTEXT7_URL = "https://mcp.context7.com/mcp"


def sh(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def claude() -> str | None:
    """claude on PATH, else the desktop app's newest bundled CLI (%APPDATA%/Claude/claude-code/<ver>/<hash>/claude.exe)."""
    found = shutil.which("claude")
    if found:
        return found
    hits = glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe"))
    return max(hits, key=os.path.getmtime) if hits else None


def uv() -> list[str] | None:
    if shutil.which("uv"):
        return [shutil.which("uv")]
    return [sys.executable, "-m", "uv"] if sh([sys.executable, "-m", "uv", "--version"])[0] == 0 else None


def tool_bin(name: str) -> Path | None:
    p = UV_BIN / (name + EXE)
    if p.is_file():
        return p
    w = shutil.which(name)
    return Path(w) if w else None


def installed_plugins() -> dict:
    f = HOME / ".claude" / "plugins" / "installed_plugins.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError):
        return {}


def plugin_version(key: str, scope: str = "user") -> str:
    return next((e.get("version", "?") for e in installed_plugins().get(key, []) if e.get("scope") == scope), "")


def mcp_servers() -> str:
    c = claude()
    if not c:
        return ""
    return sh([c, "mcp", "list"], timeout=120)[1]


def source_version() -> str:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def marketplace_root() -> Path | None:
    """The forge-dev marketplace folder (the plugin's parent holds .claude-plugin/marketplace.json in the workspace)."""
    m = PLUGIN.parent / ".claude-plugin" / "marketplace.json"
    return PLUGIN.parent if m.is_file() else None


def settings_files() -> list[Path]:
    """Claude Code settings that apply to sessions started here: user (CLAUDE_CONFIG_DIR or ~/.claude), project, local."""
    user = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
    cwd = Path.cwd()
    return [user / "settings.json", cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]


def read_settings() -> list[tuple[Path, dict]]:
    out = []
    for f in settings_files():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((f, data))
    return out


def settings_pieces(found: list[tuple[Path, dict]]) -> list[dict]:
    """1.1.0: settings that silently disable Forge's guards or answer the owner's DoD question for them."""
    out = []
    off = [f"{f} {k}" for f, d in found for k in ("disableAllHooks", "allowManagedHooksOnly") if d.get(k)]
    out.append({"name": "hooks on", "ok": not off, "install": [],
                "note": "hooks run (K15 guard, check_file, brief, stop runbook)" if not off else
                "OFF: " + "; ".join(off) + " - the K15 guard, the file gate and the brief do not run",
                "manual": "remove disableAllHooks / allowManagedHooksOnly (an organization's managed setting: ask its admin)"})
    timeout = [f"{f}: {d['askUserQuestionTimeout']}" for f, d in found if d.get("askUserQuestionTimeout")]
    out.append({"name": "question wait", "ok": not timeout, "install": [],
                "note": "questions wait for the owner (good)" if not timeout else
                "askUserQuestionTimeout " + "; ".join(timeout) + " - the DoD question (K19) can go on without the owner",
                "manual": "remove askUserQuestionTimeout from settings.json"})
    eff_env = [f"{f}: env.CLAUDE_CODE_EFFORT_LEVEL={d['env']['CLAUDE_CODE_EFFORT_LEVEL']}" for f, d in found
               if isinstance(d.get("env"), dict) and d["env"].get("CLAUDE_CODE_EFFORT_LEVEL")]
    if eff_env:
        out.append({"name": "effort env", "ok": False, "install": [], "note": "; ".join(eff_env) + " overrides agent effort",
                    "manual": "remove CLAUDE_CODE_EFFORT_LEVEL from the settings.json env block"})
    sl = next((d.get("statusLine") for f, d in found if d.get("statusLine")), None)
    forge_sl = isinstance(sl, dict) and "statusline.py" in str(sl.get("command", ""))
    out.append({"name": "status line", "ok": True, "install": [],
                "note": "Forge status line (Claude limits for K37)" if forge_sl else
                ("your own status line (Claude limits come from the band or limits.py set-claude)" if sl else
                 "optional: add the Forge status line - Claude's limits for K37 without the band: "
                 + json.dumps({"statusLine": {"type": "command", "command": f'python "{(PLUGIN / "tools" / "statusline.py").as_posix()}"'}}))})
    return out


def python_piece() -> dict:
    """Every Forge hook runs `python`: it must be Python 3.10+ on this PATH (on Linux/macOS often only python3 exists)."""
    py = shutil.which("python")
    ver = sh([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])[1] if py else ""
    try:
        ok = bool(py) and tuple(int(x) for x in ver.split(".")[:2]) >= (3, 10)
    except ValueError:
        ok = False
    fix = ("install Python 3.12 and tick 'Add python.exe to PATH'" if os.name == "nt" else
           "sudo apt install python-is-python3  (or: ln -s \"$(command -v python3)\" ~/.local/bin/python)")
    return {"name": "python", "ok": ok, "note": f"{py} {ver}" if py else "`python` is not on PATH - the Forge hooks cannot run",
            "install": [], "manual": fix}


def pieces() -> list[dict]:
    """Every piece: name, ok, note, install (list of argv lists, run in order) - computed from checks, never assumed."""
    c = claude() or "claude"
    u = uv()
    out = [python_piece()]
    node = shutil.which("node")
    nv = sh([node, "--version"])[1] if node else ""
    try:
        node_ok = bool(nv) and tuple(int(x) for x in nv.lstrip("v").split(".")[:2]) >= (18, 18)
    except ValueError:
        node_ok = False
    out.append({"name": "node", "ok": node_ok, "note": nv or "not on PATH (needed by the Codex plugin)",
                "install": [], "manual": "winget install OpenJS.NodeJS.LTS"})
    try:
        import gpt_bridge  # noqa: F401  (same folder)
        codex = gpt_bridge.find_codex()
        cv = sh([codex, "--version"])[1]
    except Exception as e:  # FileNotFoundError or import trouble
        codex, cv = "", str(e)
    out.append({"name": "codex cli", "ok": bool(codex), "note": cv[:80], "install": [],
                "manual": "install the ChatGPT Codex app and log in (codex login)"})
    out.append({"name": "uv", "ok": bool(u), "note": "" if u else "missing",
                "install": [[sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"]]})
    uvc = u or [sys.executable, "-m", "uv"]
    serena = tool_bin("serena")
    out.append({"name": "serena", "ok": bool(serena), "note": str(serena or "missing"),
                "install": [uvc + ["tool", "install", "-p", "3.13", "serena-agent"]]})
    for t in ("ruff", "pyright"):
        b = tool_bin(t)
        out.append({"name": t, "ok": bool(b), "note": str(b or "missing"), "install": [uvc + ["tool", "install", t]]})
    mcp = mcp_servers()
    serena_cmd = str(UV_BIN / ("serena" + EXE))
    out.append({"name": "serena mcp", "ok": "serena" in mcp, "note": "registered" if "serena" in mcp else "not registered",
                "install": [[c, "mcp", "add", "--scope", "user", "serena", "--", serena_cmd, "start-mcp-server",
                             "--context", "claude-code", "--project-from-cwd"]]})
    out.append({"name": "context7 mcp", "ok": "context7" in mcp, "note": "registered" if "context7" in mcp else "not registered",
                "install": [[c, "mcp", "add", "--scope", "user", "--transport", "http", "context7", CONTEXT7_URL]]})
    cp = plugin_version("codex@openai-codex")
    out.append({"name": "codex plugin", "ok": bool(cp), "note": cp or "not installed",
                "install": [[c, "plugin", "marketplace", "add", "openai/codex-plugin-cc"],
                            [c, "plugin", "install", "codex@openai-codex", "--scope", "user"]]})
    mk = marketplace_root()  # the dev workspace's marketplace is forge-dev; the GitHub one (scrvex/Forge) is forge
    fkey = next((k for k in ("forge@forge-dev", "forge@forge") if plugin_version(k)), "forge@forge-dev" if mk else "forge@forge")
    fv, sv = plugin_version(fkey), source_version()
    out.append({"name": "forge", "ok": fv == sv, "note": f"installed {fv or '-'} / this source {sv}",
                "install": ([[c, "plugin", "marketplace", "update", "forge-dev"]] if mk else [])
                + [[c, "plugin", "update" if fv else "install", fkey, "--scope", "user"]]})
    bkey = next((k for k in ("forge-band@forge-dev", "forge-band@forge") if plugin_version(k)), fkey.replace("forge@", "forge-band@"))
    bv = plugin_version(bkey)
    out.append({"name": "forge-band", "ok": bool(bv), "note": bv or "not installed (progress band; Claude Code only)",
                "install": [[c, "plugin", "install", bkey, "--scope", "user"]]})
    eff = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    out.append({"name": "effort env", "ok": not eff, "note": f"CLAUDE_CODE_EFFORT_LEVEL={eff} overrides agent effort" if eff else "not set (good)",
                "install": [], "manual": "remove CLAUDE_CODE_EFFORT_LEVEL from the environment / settings.json env"})
    out += settings_pieces(read_settings())
    if os.name == "nt":
        out.append({"name": "codex sandbox", "ok": True, "install": [],
                    "note": 'code tasks (codex_exec) run with windows.sandbox="elevated" - if they fail with "helper_unknown_error", set '
                            'forge.json "code": {"codex_windows_sandbox": ""} (no override) and re-run the task; ChatGPT audits, '
                            'advice and fact checks (read-only) run "unelevated", image runs stay "elevated" (1.1.2; env FORGE_GPT_WINDOWS_SANDBOX = '
                            'elevated | unelevated | none overrides)'})
    acl = packages_acl_piece()
    if acl:
        out.append(acl)
    return out


def packages_acl_piece() -> dict | None:
    """1.1.2 (N028): the Codex app's folder keeps SYSTEM and Administrators (read by SID). The repair is the owner's own
    command - never an install step: Forge does not change permissions."""
    import codex_safety  # same folder
    r = codex_safety.check()
    if r["state"] == "skipped" and os.name != "nt":
        return None
    blk = codex_safety.read_block()
    if r["state"] == "broken":
        note = (f"BROKEN: {r['path']} lacks {', '.join(r['missing'])} - the Codex desktop app cannot start (0x80070005); "
                "Forge refuses Codex runs until it is fixed")
        if blk:
            note += f"; broken by the Codex run '{blk.get('run', '?')}' {blk.get('when', '?')} - evidence {blk.get('incident', '?')}"
    elif r["state"] == "ok":
        note = "SYSTEM and Administrators present (the Codex app can start)" + (
            f"; the block set by the run '{blk.get('run', '?')}' is lifted at the next Codex run" if blk else "")
    else:
        note = r["note"] + ("; Codex stays blocked until the ACL can be read or the block file is removed: "
                            f"{codex_safety.block_file()}" if blk else "")
    piece = {"name": "packages acl", "ok": r["state"] != "broken" and not (blk and r["state"] != "ok"), "note": note,
             "install": [], "manual": f"in PowerShell, by the owner (Forge never changes permissions): {r['fix']}"}
    if r["state"] == "unknown" and piece["ok"]:
        piece["mark"] = "??  "  # not readable: neither OK nor missing
    return piece


def doctor() -> int:
    bad = 0
    for p in pieces():
        mark = p.get("mark") or ("OK  " if p["ok"] else "MISS")
        bad += not p["ok"]
        print(f"{mark} {p['name']:<13} {p['note']}")
        if not p["ok"]:
            for cmd in p["install"]:
                print("       fix: " + " ".join(f'"{x}"' if " " in x else x for x in cmd))
            if p.get("manual"):
                print("       by hand: " + p["manual"])
    print(f"\n{bad} piece(s) missing" + (" - run: python setup.py install" if bad else " - the code stack is ready"))
    return 0


def install(only: set[str]) -> int:
    failed = 0
    for p in pieces():
        if p["ok"] or (only and p["name"] not in only):
            continue
        if not p["install"]:
            print(f"SKIP {p['name']}: by hand - {p.get('manual', '')}")
            continue
        for cmd in p["install"]:
            print("$ " + " ".join(cmd))
            code, out = sh(cmd, timeout=900)
            print(("   " + out[-800:].replace("\n", "\n   ")) if out else "   (no output)")
            if code != 0:
                failed += 1
                print(f"   FAILED ({code}) - continue with the next piece")
                break
    print("\nNow restart Claude (new sessions load the MCP servers and plugins); then: python setup.py doctor")
    return 1 if failed else 0


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if len(sys.argv) < 2 or sys.argv[1] not in ("doctor", "install"):
        print(__doc__)
        return 0
    if sys.argv[1] == "doctor":
        return doctor()
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else set()
    return install(only)


if __name__ == "__main__":
    sys.exit(main())
