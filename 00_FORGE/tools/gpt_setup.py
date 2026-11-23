"""Switch ChatGPT/Codex on/off for a Forge project (K15) and write its role file AGENTS.md (K29). 0 tokens, no network.

    gpt_setup.py [project_dir] --allow        # the owner said yes: forge.json "gpt": "allowed" + AGENTS.md (executor in patch mode, reviewer, adviser, images)
    gpt_setup.py [project_dir] --deny         # forge.json "gpt": "denied" + AGENTS.md that says ChatGPT is off here
    gpt_setup.py --deny-path C:/ai/invest     # the owner's personal deny list (~/.forge/gpt_policy.json): never sent anywhere

An existing AGENTS.md without the Forge marker (e.g. one the Codex app made by replacing "Claude" with "Codex" in
CLAUDE.md, which told Codex it was the Lead) is kept as <state>/AGENTS.md.bak-<date> before it is replaced.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import PLUGIN_DIR, kernel_version, layout  # noqa: E402

MARKER = "<!-- forge:agents"


def render_agents_md(root: Path, cfg: dict) -> str:
    lay = layout(root)
    rel = lambda p: p.relative_to(root).as_posix() if p != root else "."  # noqa: E731
    on = str(cfg.get("gpt", "")).lower() == "allowed"
    rep = {"{{PROJECT}}": cfg.get("project", root.name), "{{KERNEL_VERSION}}": kernel_version(), "{{LANG}}": cfg.get("user_language", "ru"),
           "{{GPT_STATE}}": "allowed - you execute the Lead's tasks, review, advise and make images when asked" if on else
           "NOT allowed - do not read this project's files or work here; tell the owner to decide (gpt_setup.py --allow)",
           "{{TECH}}": rel(lay["tech"]), "{{READ}}": rel(lay["read"]), "{{STATE}}": rel(lay["state"]), "{{CIRCLE}}": rel(lay["circle"])}
    text = (PLUGIN_DIR / "templates" / "AGENTS.project.md").read_text(encoding="utf-8")
    for k, v in rep.items():
        text = text.replace(k, v)
    return text


def write_agents_md(root: Path, cfg: dict) -> str:
    f = root / "AGENTS.md"
    note = "written"
    if f.is_file():
        old = f.read_text(encoding="utf-8", errors="replace")
        if MARKER not in old:
            bak = layout(root)["state"] / f"AGENTS.md.bak-{date.today().isoformat()}"
            bak.parent.mkdir(parents=True, exist_ok=True)
            bak.write_text(old, encoding="utf-8", newline="\n")
            note = f"replaced (old one kept: {bak.relative_to(root).as_posix()})"
        else:
            note = "updated"
    f.write_text(render_agents_md(root, cfg), encoding="utf-8", newline="\n")
    return note


def add_deny(path: str) -> Path:
    import gpt_bridge
    pf = gpt_bridge.policy_file()
    pol = gpt_bridge.load_policy()
    norm = str(Path(path).resolve()).replace("\\", "/")
    if norm not in pol.setdefault("deny", []):
        pol["deny"].append(norm)
    pol.setdefault("note", "Forge K15: paths the owner never sends to a cloud model (ChatGPT/Codex)")
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(pol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return pf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", default=".")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--allow", action="store_true"); g.add_argument("--deny", action="store_true")
    ap.add_argument("--deny-path", action="append", default=[], help="add to the owner's personal deny list")
    a = ap.parse_args()
    if a.allow or a.deny or a.deny_path:
        from forge_common import refuse_in_codex
        refuse_in_codex("gpt_setup")  # the owner's K15 switch is never flipped by ChatGPT itself
    for d in a.deny_path:
        print(f"deny list: {add_deny(d)} += {d}")
    if not (a.allow or a.deny):
        return 0
    root = Path(a.dir).resolve()
    if not (root / "forge.json").is_file():
        raise SystemExit(f"{root} is not a Forge project (no forge.json)")
    cfg_path = root / "forge.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["gpt"] = "allowed" if a.allow else "denied"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    note = write_agents_md(root, cfg)
    print(f"{root.name}: forge.json gpt = {cfg['gpt']}; AGENTS.md {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
