"""Check ONE file right after it is written (R31 / M5): UTF-8, no BOM, LF, size <= max_kb.

    check_file.py path/to/file            # exit 1 + message if the file breaks an encoding/size rule
    echo '{"tool_input": {"file_path": "..."}}' | check_file.py --hook     # Claude Code PostToolUse hook (Write|Edit)

As a hook it prints the problem to stderr and exits 2 so the Lead sees it immediately and rewrites the file with
LF/UTF-8 — instead of discovering it at commit time. Files outside the project's text extensions are ignored.
1.1.0: as a hook it also runs the project's lesson guards on the written file (tools/guards.py, `on: write`, matched against
"<path relative to the project>\n<text>"): the file is already written, so `block` reaches the Lead as exit 2 (stderr) and
`warn` as exit 0 with the message in hook additionalContext (the write goes on, the Lead still sees the warning).
1.2.0: as a hook it is silent (exit 0, no output) for a file outside any Forge project (no forge.json up the tree)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def project_root(path: Path) -> Path | None:
    """The nearest folder with forge.json at or above the file (not the plugin's own tree, unlike forge_common.find_root)."""
    try:
        start = (path if path.is_absolute() else Path.cwd() / path).resolve().parent
    except OSError:
        return None
    return next((d for d in (start, *start.parents) if (d / "forge.json").is_file()), None)


def check(path: Path, cfg: dict | None) -> list[str]:
    exts = set((cfg or {}).get("text_ext", [".md", ".py", ".json", ".txt", ".yaml", ".yml", ".csv", ".ps1", ".sh", ".html", ".svg"]))
    if not path.is_file() or path.suffix.lower() not in exts:
        return []
    raw = path.read_bytes(); problems = []
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("UTF-8 BOM")
    if b"\r" in raw:
        problems.append("CR line endings (use LF)")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append(f"not UTF-8 at byte {e.start}")
    limit = int((cfg or {}).get("max_kb", 64)) * 1024
    if len(raw) > limit and path.suffix.lower() != ".html":
        problems.append(f"{len(raw) // 1024} KB > {limit // 1024} KB (split it)")
    return problems


def main() -> int:
    hook = "--hook" in sys.argv
    if hook:
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))  # 1.1.1: UTF-8, not the Windows locale
            target = data.get("tool_input", {}).get("file_path") or data.get("tool_input", {}).get("path") or ""
        except (json.JSONDecodeError, ValueError):
            return 0
    else:
        target = next((x for x in sys.argv[1:] if not x.startswith("--")), "")
    if not target:
        return 0
    path = Path(target)
    cfg = None
    root = project_root(path)
    if hook and root is None:
        return 0  # the hook is installed user-wide: outside a Forge project it says nothing
    if root is not None:
        try:
            from forge_common import load_cfg  # noqa: E402
            cfg = load_cfg(root)
        except SystemExit:
            cfg = None
    problems = check(path, cfg)
    if problems:
        msg = f"check_file: {path}: " + "; ".join(problems) + " — rewrite the file with UTF-8/LF (gate_encoding will be red)"
        print(msg, file=sys.stderr)
        return 2 if hook else 1
    if hook and cfg and path.is_file() and path.stat().st_size <= 512 * 1024:  # D3: lesson guards on what was written
        try:
            from guards import verdict
            try:
                rel = path.resolve().relative_to(cfg["_root"].resolve()).as_posix()
            except ValueError:
                rel = path.as_posix()
            action, msg = verdict(cfg, "write", rel + "\n" + path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            action, msg = "", ""
        if action == "block":
            print(f"BLOCK - undo or rewrite this change: {path}: {msg}", file=sys.stderr)
            return 2
        if action:  # warn: the Lead is told, the write is not treated as an error
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": f"Forge guard WARN: {path}: {msg}"}}, ensure_ascii=False))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
