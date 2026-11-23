"""ChatGPT as an executor where it is better (K29): raster images - covers, illustrations, logo concepts, mock-ups.

    gpt_media.py image "brief written by Claude" --out <project>/<state>/staging/N012/cover.png [--ref a.png,b.png] [--n 1]
    gpt_media.py video ...      # not available in Codex CLI: prints what to do instead (exit 2)

Flow (K29): Claude (the Lead) writes the brief and the DoD; ChatGPT generates with Codex's image tool into a staging path;
Claude looks at every image (Read the file) against the DoD; the owner judges taste. Images land in Codex's own folder
(~/.codex/generated_images/...) - this script copies the new ones to --out (and --out_2.png ... for --n > 1) and writes
a sidecar <out>.json with the brief, model, seconds and usage. K15: --out and --ref must be in a project that allows ChatGPT.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import shutil
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpt_bridge  # noqa: E402
from forge_common import IMAGE_EXT  # noqa: E402

VIDEO_NOTE = ("Video generation is not available in Codex CLI (checked 2026-10-05: no video feature). What works instead:\n"
              "1) Claude writes the script/storyboard (HEAVY order); 2) ChatGPT reviews it as an expert: verify_gpt.py advise "
              "\"...\" --files script.md; 3) frames/covers: gpt_media.py image; 4) the clip itself - the owner generates it in "
              "the ChatGPT app from Claude's brief, then Claude checks the result against the DoD.")

PROMPT = """Use your image generation tool to create {n} image(s) for this brief. Follow it exactly; no text in the image
unless the brief asks for text; do not add watermarks. Generate each image once - do not iterate unless the tool fails.
{refs}Brief:
{brief}
After generating, reply with one line per image: what it shows (in Russian)."""


def generated_dir() -> Path:
    return gpt_bridge.CODEX_HOME / "generated_images"


def make_image(brief: str, out: str, refs: list[str] | None = None, n: int = 1, effort: str = "low") -> dict:
    dest = Path(out).resolve()
    if dest.suffix.lower() not in IMAGE_EXT:
        raise SystemExit(f"--out must be an image path ({', '.join(IMAGE_EXT)}), got {dest.name}")
    refs = [str(Path(r).resolve()) for r in (refs or [])]
    gpt_bridge.check_paths([dest.parent, *refs])
    wd = gpt_bridge.new_workdir("image")
    start = time.time()
    r = gpt_bridge.run(PROMPT.format(n=n, brief=brief, refs="Reference images are attached: keep their style/proportions where the brief says so.\n" if refs else ""),
                       wd, sandbox="workspace-write", effort=effort, images=refs, tag="image", log_near=dest.parent, timeout=600)
    # only this run's thread folder: the owner may be generating images in the Codex app at the same time
    gdir = generated_dir() / r["thread"] if r.get("thread") else None
    new = sorted((p for p in (gdir.rglob("*") if gdir and gdir.is_dir() else []) if p.is_file() and p.suffix.lower() in IMAGE_EXT
                  and p.stat().st_mtime >= start - 1), key=lambda p: p.stat().st_mtime)
    new += sorted(p for p in wd.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT)  # in case it saved into the work folder
    saved = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(new[:n]):
        target = dest if i == 0 else dest.with_name(f"{dest.stem}_{i + 1}{dest.suffix}")
        if src.suffix.lower() != target.suffix.lower():  # keep the real format: no PNG bytes behind a .jpg name (N009 #7)
            target = target.with_suffix(src.suffix.lower())
        shutil.copy2(src, target)
        saved.append(str(target))
    meta = {"brief": brief, "refs": refs, "saved": saved, "source": [str(p) for p in new[:n]], "said": r["answer"][:800],
            **{k: r[k] for k in ("ok", "seconds", "model", "effort", "usage", "error")}, "by": "gpt:codex", "role": "executor (K29: images)"}
    Path(saved[0] if saved else dest).with_suffix(Path(saved[0] if saved else dest).suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return meta


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    if sys.argv[1] == "video":
        print(VIDEO_NOTE); return 2
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["image"]); ap.add_argument("brief")
    ap.add_argument("--out", required=True); ap.add_argument("--ref", default=""); ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    m = make_image(a.brief, a.out, [x for x in a.ref.split(",") if x], a.n, a.effort)
    print(f"images: {len(m['saved'])} saved ({m['seconds']}s) -> {', '.join(m['saved']) or 'NONE'}")
    if not m["saved"]:
        print("  no image came back:", (m["error"] or m["said"])[:300])
    print("next: Claude opens every image and checks it against the DoD; the owner chooses (taste is the owner's call)")
    return 0 if m["saved"] else 1


if __name__ == "__main__":
    sys.exit(main())
