"""Deterministic self-check of changed code - Forge 1.0 (K35 Verify for code; from agent-playbook's python-self-check).

Runs only what the project and this PC have, with the project's own config (pyproject.toml / ruff.toml / pyrightconfig.json /
tsconfig.json / eslint config), and says honestly what it skipped (K32: statuses from checks, never assumed):
- Python: syntax (`py_compile`, always) -> `ruff check` -> `pyright` (each if installed: PATH, `python -m`, or uv's tool folder);
- TypeScript / JavaScript: `tsc --noEmit` (tsconfig + local or global tsc) -> `eslint` (config + local eslint) -> `node --check`
  for plain .js/.mjs/.cjs when neither runs;
- JSON: parse;
- the project's test command: forge.json `"code": {"test": "python -m pytest -q"}` (skipped with `--no-tests` or a waiver).
Files = `--files a.py,b.ts`, else every changed/untracked file `git status` lists. `--order N007` writes
`answers/N007_checks.json`. Exit 1 if any check that ran failed, or if there were files and not one check could run on
them ("not checked" - a run that checked nothing is never green).

Usage: python check_code.py [--files a,b] [--order N007] [--no-tests] [--json]
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import find_root, git_tree, load_cfg  # noqa: E402

PY = (".py",)
TS = (".ts", ".tsx", ".mts", ".cts")
JS = (".js", ".jsx", ".mjs", ".cjs")
SKIP_DIRS = ("node_modules/", ".venv/", "venv/", "dist/", "build/", "__pycache__/", ".git/")


def tool(name: str, root: Path) -> list[str] | None:
    """Command prefix for a CLI tool: project-local node_modules/.bin, PATH, uv's tool folder, or `python -m` (ruff/pyright)."""
    local = root / "node_modules" / ".bin" / (name + (".cmd" if os.name == "nt" else ""))
    if local.is_file():
        return [str(local)]
    found = shutil.which(name)
    if found:
        return [found]
    uv_bin = Path.home() / ".local" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if uv_bin.is_file():
        return [str(uv_bin)]
    if name in ("ruff", "pyright"):
        r = subprocess.run([sys.executable, "-m", name, "--version"], capture_output=True, text=True)
        if r.returncode == 0:
            return [sys.executable, "-m", name]
    return None


def run(name: str, cmd: list[str] | str, root: Path, timeout: int = 600, shell: bool = False) -> dict:
    shown = (cmd if isinstance(cmd, str) else " ".join(cmd))[:300]
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           shell=shell)
        out = (p.stdout + p.stderr).strip()
        return {"check": name, "cmd": shown, "ok": p.returncode == 0, "exit": p.returncode, "tail": out[-1500:]}
    except subprocess.TimeoutExpired:
        return {"check": name, "cmd": shown, "ok": False, "exit": None, "tail": f"timeout after {timeout}s"}
    except OSError as e:
        return {"check": name, "cmd": shown, "ok": False, "exit": None, "tail": str(e)}


def skipped(name: str, why: str) -> dict:
    return {"check": name, "ok": None, "skipped": why}


def changed_files(root: Path, cfg: dict) -> list[str]:
    """Changed project files - not Forge's own circle and state (the gates check those)."""
    own = [p.relative_to(root).as_posix().lower() + "/" for p in (cfg["_layout"]["circle"], cfg["_layout"]["state"]) if p != root]
    tree = git_tree(root) or {}
    return sorted(p for p, fp in tree.items() if fp not in ("deleted", "dir") and not any(p.lower().startswith(o) for o in own))


def check(root: Path, files: list[str], cfg: dict, tests: bool = True) -> list[dict]:
    asked = files
    files = [f for f in files if (root / f).is_file() and not any(s in f.replace("\\", "/") + "/" for s in SKIP_DIRS)]
    out_dirs = [f for f in asked if (root / f).is_file() and f not in files]
    py = [f for f in files if f.endswith(PY)]
    ts = [f for f in files if f.endswith(TS)]
    js = [f for f in files if f.endswith(JS)]
    js_ = [f for f in files if f.endswith(".json")]
    out: list[dict] = []
    if py:
        out.append(run("python syntax", [sys.executable, "-m", "py_compile", *py], root))
        ruff = tool("ruff", root)
        out.append(run("ruff", ruff + ["check", "--force-exclude", *py], root) if ruff else skipped("ruff", "not installed (setup.py install)"))
        pyright = tool("pyright", root)
        if pyright and ((root / "pyrightconfig.json").is_file() or "[tool.pyright]" in _read(root / "pyproject.toml")):
            out.append(run("pyright", pyright + py, root))
        else:
            out.append(skipped("pyright", "not installed" if not pyright else "no pyrightconfig.json / [tool.pyright]: types not enforced here"))
    if ts or js:
        tsc = tool("tsc", root)
        if ts and (root / "tsconfig.json").is_file() and tsc:
            out.append(run("tsc", tsc + ["--noEmit", "-p", "."], root))
        elif ts:
            out.append(skipped("tsc", "no tsconfig.json" if not (root / "tsconfig.json").is_file() else "tsc not installed (npm i -D typescript)"))
        eslint = tool("eslint", root)
        has_cfg = any((root / n).is_file() for n in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", ".eslintrc.json",
                                                     ".eslintrc.js", ".eslintrc.cjs", ".eslintrc"))
        if eslint and has_cfg:
            out.append(run("eslint", eslint + [*ts, *js], root))
        else:
            out.append(skipped("eslint", "no eslint config" if not has_cfg else "eslint not installed in the project"))
        node = shutil.which("node")
        plain = [f for f in js if not f.endswith(".jsx")]
        if plain and node and not (eslint and has_cfg):
            out += [run(f"node --check {f}", [node, "--check", f], root) for f in plain]
    for f in js_:
        try:
            json.loads((root / f).read_text(encoding="utf-8"))
            out.append({"check": f"json {f}", "ok": True})
        except (ValueError, OSError) as e:
            out.append({"check": f"json {f}", "ok": False, "tail": str(e)[:300]})
    code_ext = PY + TS + JS + (".json",)
    if out_dirs:
        out.append(skipped("files in build/vendor folders", ", ".join(out_dirs[:8])))
    other = [f for f in files if not f.endswith(code_ext)]
    if other:
        out.append(skipped("files that are not code", ", ".join(other[:8])))
    test = (cfg.get("code") or {}).get("test", "")
    if not tests:
        out.append(skipped("tests", "skipped on request (--no-tests: the owner's waiver must be recorded in the order)"))
    elif test:  # the project's own command line, run by the shell (npm/npx are .cmd files on Windows; quoting stays the owner's)
        out.append(run("tests", test, root, timeout=1800, shell=True))
    elif py or ts or js:
        out.append(skipped("tests", 'no test command: forge.json "code": {"test": "..."}'))
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Forge code self-check (K35)")
    ap.add_argument("--files"); ap.add_argument("--order"); ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = find_root(Path.cwd())
    cfg = load_cfg(root)
    files = [f.strip() for f in a.files.split(",") if f.strip()] if a.files else changed_files(root, cfg)
    res = check(root, files, cfg, tests=not a.no_tests)
    missing = [f for f in files if not (root / f).is_file()] if a.files else []
    if missing:  # a file asked for and not checked is a failure, never a silent green (N024 review: "--files typo.py" was green)
        res.insert(0, {"check": "files", "ok": False, "tail": "not found: " + ", ".join(missing)})
    ran = [r for r in res if r.get("ok") is not None]
    if files and not ran:  # files to check and not one check ran on them: NOT CHECKED, never a green (N024 r2 F3, Codex #6)
        res.append({"check": "not checked", "ok": False, "tail": f"no check could run on {len(files)} file(s): "
                    + ", ".join(files[:8]) + " - install the tools (setup.py), add a config, or a test command"})
        ran = [res[-1]]
    if not ran:  # nothing changed and no test command: a run that checked nothing is never green (1.1.0, ambatupipe audit H6)
        res.append({"check": "nothing checked", "ok": False, "tail": "no changed files and no test command - name the files "
                    "(--files a,b) or set forge.json \"code\": {\"test\": \"...\"}"})
        ran = [res[-1]]
    ok = all(r["ok"] for r in ran)
    data = {"when": datetime.now().isoformat(timespec="seconds"), "files": files, "ok": ok, "checks": res}
    if a.order:
        f = cfg["_layout"]["circle"] / "answers" / f"{a.order}_checks.json"
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if a.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        for r in res:
            mark = "SKIP" if r.get("ok") is None else ("OK" if r["ok"] else "FAIL")
            print(f"{mark:<4} {r['check']}" + (f" - {r['skipped']}" if r.get("skipped") else ""))
            if r.get("ok") is False and r.get("tail"):
                print("     " + r["tail"][-600:].replace("\n", "\n     "))
        print(f"code check: {'green' if ok else 'RED'} ({len(ran)} ran, {len(res) - len(ran)} skipped, {len(files)} file(s))")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
