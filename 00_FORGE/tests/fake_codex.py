"""A stand-in for `codex exec` in tests (FORGE_CODEX points here), patch mode. Behaviour by env FAKE_CODEX_MODE:
ok (default) - proposes every scope file listed in the prompt; stray - also proposes stray.txt and forge.json (outside the
scope / Forge state); write - writes a file to disk despite the read-only sandbox; fail - exits 1 with a usage-limit message;
blocked - proposes nothing and asks a question. On `resume` the first scope file gets a second version.
1.1.2: FAKE_CODEX_ACL = an SDDL written over the FORGE_PACKAGES_SDDL stand-in while it "runs" (a sandbox that rewrites the
Codex app's folder rights). Prints Codex's --json events and writes the final JSON answer to the -o file, like the real CLI."""
import json
import os
import re
import sys
from pathlib import Path

args = sys.argv[1:]
if "resume" in args and any(x in ("-s", "--sandbox", "-C", "--cd") for x in args[args.index("resume"):]):
    print("error: unexpected argument found after `exec resume` (the real CLI: -s/-C are exec-level options)", file=sys.stderr)
    sys.exit(2)  # like codex-cli 0.160: `codex exec resume --help` lists neither -s nor -C
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
root = Path(args[args.index("-C") + 1])
out = Path(args[args.index("-o") + 1])
sandbox = args[args.index("-s") + 1]
prompt = sys.stdin.read() if args[-1] == "-" else args[-1]  # `-`: the real CLI reads the prompt from stdin
if os.environ.get("FAKE_CODEX_PROMPT_OUT"):
    Path(os.environ["FAKE_CODEX_PROMPT_OUT"]).write_text(prompt, encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "th-fake-1"}))
if os.environ.get("FAKE_CODEX_ACL") and os.environ.get("FORGE_PACKAGES_SDDL"):
    Path(os.environ["FORGE_PACKAGES_SDDL"]).write_text(os.environ["FAKE_CODEX_ACL"], encoding="utf-8")
if mode == "fail":
    print("You've hit your usage limit. Try again later.", file=sys.stderr)
    sys.exit(1)
block = re.search(r"## Files you may create or change[^\n]*\n(.*?)\n## ", prompt, re.S)
scope = [ln[2:].strip() for ln in (block.group(1).splitlines() if block else []) if ln.startswith("- ")]
files = []
if mode in ("ok", "stray", "write"):
    value = "43" if "resume" in args else "42"
    files = [{"path": s, "content": "" if s.endswith("__init__.py") else f"value = {value}  # sandbox {sandbox}\n"} for s in scope]
if mode == "stray":
    files += [{"path": "stray.txt", "content": "not mine\n"}, {"path": "forge.json", "content": "{}\n"}]
if mode == "write":
    (root / "breach.txt").write_text("written despite read-only\n", encoding="utf-8")
answer = {"status": "blocked" if mode == "blocked" else "done", "summary": f"fake codex proposes {len(files)} file(s)",
          "files": files, "checks_run": ["python -B -c 'import ast' (fake)"],
          "question": "which module?" if mode == "blocked" else "",
          "decisions": ["kept the module layout"] if files else [], "beyond_task": "", "irreversible": "", "memory": ""}
out.write_text(json.dumps(answer), encoding="utf-8")
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 300}}))
