"""Shared helpers for plugin tools and gates: project root, config, file walking, index/state parsing.

A *project root* is the nearest ancestor directory (from cwd, then from this file) that contains `forge.json`.
`forge.json` names the project and lists excludes; kernel defaults live in the plugin's `kernel.json`.
Core 1 is the installed plugin (this folder) — never copied into a project (K23, plugin form since 0.6.0).
"""
from __future__ import annotations

import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):  # 0.8: stderr too - hook messages (check_file exit 2) reached Claude in cp1251
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import json
import re
from datetime import datetime
from pathlib import Path

INDEX_ROW = re.compile(r"^\|\s*(N\d{3})\s*\|\s*`([^`]+)`\s*\|\s*([A-Z]+)\s*\|([^|]*)\|\s*([A-Z_]+)[^|]*\|\s*$")
STATE_LINE = re.compile(r"^([A-Z_]+):\s*(.*)$")


def find_root(start: Path | None = None) -> Path:
    for base in (start or Path.cwd(), Path(__file__).resolve()):
        for d in [base, *base.parents]:
            if (d / "forge.json").is_file():
                return d
    raise SystemExit("forge.json not found — run inside a Forge project (or create one: init_project.py <dir> --plan plan.json)")


PLUGIN_DIR = Path(__file__).resolve().parents[1]  # Core 1 = the plugin folder (tools/ and gates/ live inside it)
TREE_CAP = 250  # dirty paths kept in a scope snapshot; more (or > 48 KB) = skipped with a note (answers/*.json stay < 64 KB)
# Core 2 folders every project has (init_project creates them, upgrade.py adds the missing ones - 0.9.2, R-28)
CORE_DIRS = ("circle/orders", "circle/answers", "circle/reports", "circle/journal", "circle/lessons", "circle/plans",
             "state", "state/staging", "state/swarm", "tools", "config", "docs")


def gitignore_lines(state: str = "01_PROJECT_CORE/state") -> list[str]:
    """What never enters git in a project: caches, the last gate run, swarm drafts (0.7.1). `state` = the project's
    layout["state"] (N013 r3 #4: a custom layout got the default paths)."""
    return ["__pycache__/", "*.pyc", f"{state}/cache/", f"{state}/gates_last.json", f"{state}/staging/", "*.tmp"]


BIG_FILE = 4 * 1024 * 1024  # larger files (video, .blend) are fingerprinted by size + mtime, not read


def _fingerprint(p: Path) -> str:
    """sha1[:16] of the content (read in chunks); a big file by size + mtime; never raises."""
    import hashlib
    try:
        if p.is_dir():
            return "dir"
        if not p.exists():
            return "deleted"
        st = p.stat()
        if st.st_size > BIG_FILE:
            return f"s{st.st_size}-m{st.st_mtime_ns}"
        h = hashlib.sha1()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:  # locked by another program (an open Office file on Windows): listed, never fatal
        try:
            st = p.stat()
            return f"unreadable-s{st.st_size}-m{st.st_mtime_ns}"
        except OSError:
            return "unreadable"


def git_tree(root: Path) -> dict[str, str] | None:
    """Fingerprint of the uncommitted working tree (K34d, 0.9.2 - "what cannot be forbidden is made visible"):
    {posix path relative to `root`: fingerprint | "deleted" | "dir"} for every path `git status` lists under `root`.
    Fail-open: None when git is missing, `root` is not in a repository or git fails (N013 verifier2 #1).
    -z keeps Cyrillic paths literal (L010); porcelain paths are relative to the repository root, so a project nested in a
    larger repository is limited to its own folder (`-- .`) and the prefix is stripped."""
    import subprocess
    try:
        pre = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=root, capture_output=True)
        if pre.returncode != 0:
            return None
        prefix = pre.stdout.decode("utf-8", "replace").strip()
        r = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."], cwd=root, capture_output=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    out: dict[str, str] = {}
    parts = r.stdout.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]; i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1  # -z: the old name of a rename/copy is the next field
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        out[path.rstrip("/")] = _fingerprint(root / path)
    return out
CORE2 = "01_PROJECT_CORE"
DEFAULT_LAYOUT = {"tech": CORE2, "circle": f"{CORE2}/circle", "state": f"{CORE2}/state", "tools": f"{CORE2}/tools",
                  "docs": f"{CORE2}/docs", "config": f"{CORE2}/config", "read": "02_For_user", "sources": "02_For_user/Sources",
                  "projects": f"{CORE2}/projects"}


def kernel_version() -> str:
    pj = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
    return json.loads(pj.read_text(encoding="utf-8")).get("version", "0") if pj.is_file() else "0"


def layout(root: Path) -> dict[str, Path]:
    """Where things live in this project (owner's law 2026-09-17: Core 2 in `01_PROJECT_CORE`, user folders in the
    user's language). Projects override any key in forge.json `layout`. The legacy `kernel` key is ignored (0.6.0)."""
    cfg = json.loads((root / "forge.json").read_text(encoding="utf-8"))
    lay = {**DEFAULT_LAYOUT, **{k: v for k, v in cfg.get("layout", {}).items() if k != "kernel"}}
    out = {k: (root / v if v else root) for k, v in lay.items()}
    out["dashboard"] = root / lay["dashboard"] if lay.get("dashboard") else out["read"]  # optional sub-folders of the user part
    out["scheme"] = root / lay["scheme"] if lay.get("scheme") else out["read"]
    return out


def load_cfg(root: Path) -> dict:
    cfg = json.loads((root / "forge.json").read_text(encoding="utf-8"))
    lay = layout(root)
    kernel = json.loads((PLUGIN_DIR / "kernel.json").read_text(encoding="utf-8")) if (PLUGIN_DIR / "kernel.json").is_file() else {}
    merged = {**kernel, **cfg}
    merged["_root"], merged["_kernel_dir"], merged["_layout"] = root, PLUGIN_DIR, lay
    merged["kernel_version"] = kernel_version()  # the plugin's version; the project records the one it was created with
    merged["project_kernel_version"] = cfg.get("kernel_version")
    merged["mode"] = cfg.get("mode") or None  # K25: None = not asked yet
    merged.setdefault("state_dir", lay["state"].relative_to(root).as_posix() if lay["state"] != root else "state")
    merged.setdefault("tool_dirs", [lay["tools"].relative_to(root).as_posix()] if lay["tools"] != root else ["tools"])
    return merged


def is_excluded(rel: Path, excludes: list[str]) -> bool:
    parts = rel.as_posix()
    return any(parts == e.rstrip("/") or parts.startswith(e.rstrip("/") + "/") for e in excludes)


def iter_files(cfg: dict, text_only: bool = True):
    root: Path = cfg["_root"]
    exts = set(cfg.get("text_ext", []))
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if is_excluded(rel, cfg.get("exclude", [])):
            continue
        if text_only and p.suffix.lower() not in exts:
            continue
        yield p, rel


def read_index(root: Path) -> list[dict]:
    """Rows of circle/orders/00_INDEX.md as dicts: id, file, tier, changes, state."""
    f = layout(root)["circle"] / "orders" / "00_INDEX.md"
    rows = []
    if not f.is_file():
        return rows
    for line in f.read_text(encoding="utf-8").splitlines():
        m = INDEX_ROW.match(line)
        if m:
            rows.append({"id": m[1], "file": m[2], "tier": m[3], "changes": m[4].strip(), "state": m[5], "raw": line})
    return rows


def write_index_row(root: Path, order_id: str, new_state: str) -> None:
    f = layout(root)["circle"] / "orders" / "00_INDEX.md"
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        m = INDEX_ROW.match(line)
        if m and m[1] == order_id:
            line = f"| {m[1]} | `{m[2]}` | {m[3]} | {m[4].strip()} | {new_state} |"
        out.append(line)
    f.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def read_state(root: Path) -> dict:
    f = layout(root)["circle"] / "STATE.md"
    st = {}
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = STATE_LINE.match(line)
            if m:
                st[m[1]] = m[2].strip()
    return st


def write_state(root: Path, **fields) -> None:
    st = read_state(root)
    st.update({k: str(v) for k, v in fields.items()})
    st["UPDATED"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    order = ["STATE", "UPDATED", "CURRENT_ORDER", "NOTE"]
    lines = [f"{k}: {st.get(k, '-')}" for k in order] + [f"{k}: {v}" for k, v in st.items() if k not in order]
    (layout(root)["circle"] / "STATE.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_hm() -> str:
    return datetime.now().strftime("%H:%M")


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:n] or "order"


# --- two model families (0.8.0, K29-K31) -------------------------------------------------------------------------------

EXTERNAL_TIERS = ("LOCAL", "GPT", "CODEX")  # executors outside Claude: they run alongside the Lead's one non-external order (R26)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def family(actor: str) -> str:
    """K8/K29: the model family behind an actor string - owner | ollama | gpt | claude | other.
    Order matters: 'verifier1:ollama' is Ollama, 'lead:Codex' is GPT, 'verifier2' / 'lead' / 'executor-fast' are Claude."""
    x = (actor or "").lower()
    if x.startswith("owner"):  # exact role, not a substring: "lead (owner said ok)" is still the Lead
        return "owner"
    if any(k in x for k in ("ollama", "gemma", "qwen", "llama")):
        return "ollama"
    if any(k in x for k in ("gpt", "codex", "chatgpt", "openai")):
        return "gpt"
    if any(k in x for k in ("lead", "claude", "subagent", "verifier2", "executor-fast", "sonnet", "opus", "haiku")):
        return "claude"
    return "other"


def run_by_codex() -> bool:
    """K29: True when the calling process runs inside an OpenAI Codex session (Codex sets these in its shells)."""
    import os
    return bool(os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID"))


def refuse_in_codex(what: str) -> None:
    """K29: state-changing Forge commands are the Lead's (Claude's) - refused inside a Codex session (N009 verifier2 #6)."""
    if run_by_codex():
        raise SystemExit(f"K29: '{what}' changes the project and is the Lead's (Claude's) job - refused inside Codex. "
                         f"Codex executes the Lead's tasks (codex_exec.py), reviews, advises and makes images when the Lead asks (skills forge-code, forge-gpt).")


def gpt_allowed(cfg: dict) -> bool:
    """K15: ChatGPT may see this project only after the owner's yes, recorded as forge.json "gpt": "allowed"."""
    return str(cfg.get("gpt", "")).lower() == "allowed"
