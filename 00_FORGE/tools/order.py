"""Order lifecycle — the only thing that edits 00_INDEX.md and STATE.md (K6, K9).

    order.py new "title" --tier HEAVY --changes "what it changes" [--goal ...] [--scope a.md,b.py] [--dod ...] [--domain invest]
    order.py take N001                 # OPEN -> TAKEN, STATE RUNNING
    order.py answer N001 --executor lead --status done --summary "..." [--files a,b] [--checks "gates green"]
    order.py report N001 --verdict APPROVE|CHANGES|REJECT --text "..."   # Lead's verdict (RU or EN)
    order.py close N001                # needs answer + report + green gates -> CLOSED, STATE READY
    order.py hold N001 --why "..."     # or: block / reopen (hold/block need --why)
    order.py list                      # table of orders

Every transition writes a journal line. Tiers: LOCAL FAST HEAVY LEAD GPT (GPT = ChatGPT, images only - K29).
0.8.0: K8 by model family; R24 reads review verdicts from evidence files; important orders need a triaged ChatGPT
audit (K30); commits take only the order's files; refused when run inside a Codex session (ChatGPT never leads, K29).
0.9.0: --domain (or forge.json domains) copies the skills-map card into the order: primary skills, quality bar,
verify-by-running section, forbidden lines (K32).
0.9.2 (K34, evidence discipline): `new --request "owner's words" --request-source "who, where, when" [--decision "a;b;c;d;e"]`
keeps the request verbatim (without it the Goal is marked a hypothesis); `take` snapshots the git working tree and `close`
lists changed files that the answer did not declare (a warning - other sessions share the folder); `answer` refuses paths
that do not exist (L010) and takes `--status blocked --question ... --ambiguous ... --guess ...` (the order goes BLOCKED).
0.9.3 (R-37, token accounting): `usage N --who X --usage "<usage block>" | --tokens N` books an agent run (the usage hook does
it automatically for Agent calls); `answer`/`done` take the same flags for the executor; `new/amend --estimate 400k`;
`close` prints and journals the order's tokens and the actual/estimate ratio (tools/usage.py).
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import (EXTERNAL_TIERS, IMAGE_EXT, TREE_CAP, family, find_root, git_tree, gpt_allowed, load_cfg,  # noqa: E402
                          now_hm, read_index, read_state, run_by_codex, slugify, today, write_index_row, write_state)
import usage  # noqa: E402  (R-37 token ledger, 0.9.3)
from order_checks import (NEWEST_SCAN_CAP, VERDICT_LINE, covers, ignores_case, newest_mtime, owner_words,  # noqa: E402,F401
                          owner_words_json, read_text_any, require_owner_words, review_verdict, scope_key, staged_names)
import order_checks  # noqa: E402

MUTATING = ("new", "take", "answer", "report", "close", "done", "amend", "hold", "block", "reopen", "usage")
if run_by_codex() and len(sys.argv) > 1 and sys.argv[1] in MUTATING:
    raise SystemExit("K29: ChatGPT/Codex never leads a Forge order - it does not create, take, answer, report or close orders. "
                     "It may audit, advise or make images when the Lead (Claude) asks (skill forge-gpt). Hand this back to Claude.")

ROOT = find_root()
CFG = load_cfg(ROOT)
CIRCLE = CFG["_layout"]["circle"]
PY = sys.executable


def journal(text: str, order_id: str = "-") -> None:
    f = CIRCLE / "journal" / f"{today()}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.is_file():
        f.write_text(f"# Journal {today()}\n\n", encoding="utf-8", newline="\n")
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {now_hm()} [{order_id}] {text}\n")


def next_id() -> str:
    ids = [int(r["id"][1:]) for r in read_index(ROOT)]
    return f"N{(max(ids) + 1 if ids else 1):03d}"


def row(order_id: str) -> dict:
    for r in read_index(ROOT):
        if r["id"] == order_id:
            return r
    raise SystemExit(f"{order_id} not in index")


def gates_green() -> bool:
    res = subprocess.run([PY, str(CFG["_kernel_dir"] / "gates" / "run_gates.py"), "--json"], capture_output=True, text=True, cwd=ROOT)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])["ok"]
    except (json.JSONDecodeError, IndexError, KeyError):
        print(res.stdout, res.stderr)
        return False


def same_actor(executor: str, by: str) -> bool:
    """K8 (0.8): Ollama-by-Ollama and GPT-by-GPT are one actor (a Codex sub-agent approving Codex work is self-approval,
    seen in mesh N035). Claude-by-Claude stays two actors only with verifier2 evidence (R24, checked in close)."""
    return family(executor) == family(by) and family(executor) in ("ollama", "gpt")


def taken_at(order_id: str) -> float:
    """Timestamp of the order's last 'taken' journal line - the current work cycle starts there (reopen needs a new take)."""
    from datetime import datetime
    best = 0.0
    for f in (CIRCLE / "journal").glob("*.md"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"- (\d\d:\d\d) \[(N\d{3})\] taken\b", line)
            if m and m.group(2) == order_id:
                try:
                    best = max(best, datetime.strptime(f"{f.stem} {m.group(1)}", "%Y-%m-%d %H:%M").timestamp())
                except ValueError:
                    pass
    return best


def review_files(order_id: str) -> dict[str, Path]:
    """K31: the evidence a second actor leaves - Ollama V1, Claude verifier2 (saved by the Lead), ChatGPT rung 3."""
    a = CIRCLE / "answers"
    return {"ollama": a / f"{order_id}_review.json", "verifier2": a / f"{order_id}_review_v2.md", "gpt": a / f"{order_id}_review_gpt.json"}


WAIVABLE = ("review", "tests", "dod", "plan", "codex")  # K19 (1.0): what an owner's waiver may lift; never K1 (money, deletion, sending)


WAIVER = "**Waiver (owner's words, K19):**"


def waivers_of(raw: list[str] | str | None) -> list[str]:
    """One `--waive "<rule>: <the owner's words>"` per waiver (repeat the flag); the words are kept whole, ';' included
    (N024 review: splitting on ';' broke verbatim quotes)."""
    out = []
    for w in [x.strip() for x in ([raw] if isinstance(raw, str) else raw or []) if x and x.strip()]:
        rule, _, words = w.partition(":")
        if rule.strip().lower() not in WAIVABLE or not words.strip():
            raise SystemExit(f"K19: a waiver is '<rule>: <the owner's words>' with rule in {', '.join(WAIVABLE)} - got {w!r}")
        out.append(f"{rule.strip().lower()}: {words.strip()}")
    return out


def waiver_line(raw: list[str] | str | None) -> str:
    return "".join(f"\n- {WAIVER} {w}" for w in waivers_of(raw))


def waived(order_md: str, rule: str) -> bool:
    """True when a waiver line of the order (header or an amendment) is a waiver OF `rule` - its rule word, not a mention
    inside the owner's words (N024 reviews: 'tests: ..., review: ...' used to lift the review)."""
    for line in order_md.splitlines():
        if WAIVER in line and line.split(WAIVER, 1)[1].strip().lower().startswith(rule + ":"):
            return True
    return False


def auto_lesson(order_id: str, source: str, what: str, detail: str, evidence: str = "") -> None:
    """R6/K12: every CHANGES/REJECT verdict, every red-gate close attempt, every overruled review and every owner fix
    becomes a lesson file (fix = to decide). One lesson per order and source."""
    existing = list((CIRCLE / "lessons").glob("*.json"))
    if any(order_id in f.read_text(encoding="utf-8") and f'"source": "{source}"' in f.read_text(encoding="utf-8") for f in existing):
        return
    ev = evidence or ((CIRCLE / "reports" / f"{order_id}.md").relative_to(ROOT).as_posix() if source == "review" else "state/gates_last.json")
    subprocess.run([PY, str(CFG["_kernel_dir"] / "tools" / "lesson.py"), "--failed", what, "--cause", detail or "(see report)",
                    "--fix", "(to decide: Lead proposes, owner approves)", "--applies", "project", "--source", source,
                    "--order", order_id, "--confidence", "low", "--evidence", ev],
                   cwd=ROOT, capture_output=True)


def estimate_arg(a) -> int | None:
    """--estimate 400k / 1.2M / 400000 -> int; refused when unreadable (a silent drop would lose the comparison at close)."""
    raw = getattr(a, "estimate", None)
    if raw in (None, ""):
        return None
    n = usage.parse_tokens(raw)
    if not n:
        raise SystemExit(f"--estimate: cannot read {raw!r} - give tokens like 400k, 1.2M or 400000")
    return n


def record_usage(a, who: str) -> None:
    """R-37: --usage "<usage block>" / --tokens N given to answer/done -> one run in answers/N_usage.json."""
    if getattr(a, "usage", None) or getattr(a, "tokens", None) is not None:
        print(usage.record(CFG, a.id, who, getattr(a, "usage", None), getattr(a, "tokens", None), getattr(a, "tool_uses", None),
                           getattr(a, "duration_ms", None), src="answer"))


def cmd_usage(a) -> None:
    row(a.id)
    print(usage.record(CFG, a.id, a.who, a.usage, a.tokens, a.tool_uses, a.duration_ms, a.estimate_kind, src="manual"))
    journal(f"usage recorded for {a.who}", a.id)


def cmd_new(a) -> None:
    if a.tier == "GPT":  # K29: ChatGPT executes only where it is better - raster images; everything important stays with Claude
        if not a.deliverable or not all(d.strip().lower().endswith(IMAGE_EXT) for d in a.deliverable.split(",") if d.strip()):
            raise SystemExit("K29: tier GPT is only for raster images (deliverable .png/.jpg/.webp); text, code, specs and "
                             "decisions are Claude's (FAST/HEAVY/LEAD). Reviews and advice from ChatGPT are not orders: use verify_gpt.py")
        if not gpt_allowed(CFG):
            raise SystemExit('K15: ChatGPT is not allowed in this project - ask the owner, then gpt_setup.py --allow (forge.json "gpt")')
    if a.tier == "CODEX" and not gpt_allowed(CFG):  # K29 (1.0): Codex writes the code - only where the owner opened the project
        raise SystemExit('K15: tier CODEX needs ChatGPT allowed in this project (forge.json "gpt") - ask the owner, or use FAST/HEAVY')
    waivers_of(getattr(a, "waive", None))  # K19: validate before anything is written
    if a.fixes:
        for f in [x.strip() for x in a.fixes.split(",") if x.strip()]:
            row(f)  # must exist
    # K32 (0.9): the order carries its domain card - skills to load, quality bar, verify-by-running, forbidden
    raw = a.domain if a.domain is not None else (CFG.get("domains") or [])  # given (even empty) wins over forge.json
    raw = raw.split(",") if isinstance(raw, str) else raw  # forge.json may hold "invest" or "invest,analytics" by hand
    doms = [x for x in dict.fromkeys(str(r).strip().lower() for r in raw) if x and x != "none"]
    from skills_map import order_block
    blk = order_block(doms) if doms else {"header": "", "quality": [], "section": "", "forbidden": []}
    oid = next_id()
    fname = f"{oid}_{slugify(a.title)}.md"
    important = bool(getattr(a, "important", False) or getattr(a, "external", False))
    est = estimate_arg(a)
    tpl = (CFG["_kernel_dir"] / "templates" / "order.md").read_text(encoding="utf-8")
    if blk["header"] and "{{DOMAIN_HEADER}}" not in tpl:
        raise SystemExit("K32: templates/order.md has no {{DOMAIN_HEADER}} placeholder - the domain card would be lost")
    for key, val in (("{{DOMAIN_HEADER}}", blk["header"] + "\n" if blk["header"] else ""),
                     ("{{DOMAIN_VERIFY}}", blk["section"] + "\n" if blk["section"] else ""),
                     ("{{DOMAIN_FORBIDDEN}}", "".join(f + "\n" for f in blk["forbidden"]))):
        tpl = tpl.replace(key + "\n", val).replace(key, val.rstrip("\n"))
    quality = [q.strip() for q in (a.quality or "").split(";") if q.strip()] + blk["quality"]
    # K34a (0.9.2): the owner's words verbatim with their source; without them the Goal is the Lead's wording = a hypothesis
    req = (a.request or "").strip()
    src = (a.request_source or "").strip()
    request = ("\n".join("> " + ln for ln in req.splitlines()) + "\n\n— source: "
               + (src or "not given — a **hypothesis** until the owner confirms these words at DoD (ask: who, where, when)")  # rung 3 #4
               if req else "(no verbatim request recorded) — the Goal below is the Lead's wording: a **hypothesis** until the owner confirms it at DoD")
    labels = ("What changes, and why this is the smallest change", "What is reused (skills, scripts, data)",
              "What appears new (files, checks, behaviour)", "Where it lands (folders, documents)", "Deliberately not touched")
    dec = [x.strip() for x in (a.decision or "").split(";") if x.strip()][:5]
    decision = "\n".join(f"{i + 1}. {lab}: {dec[i] if i < len(dec) else '(fill in)'}" for i, lab in enumerate(labels))
    tpl = tpl.replace("{{REQUEST}}", request).replace("{{DECISION}}", decision)
    body = (tpl.replace("{{ID}}", oid).replace("{{TITLE}}", a.title).replace("{{TIER}}", a.tier)
            .replace("{{DATE}}", today()).replace("{{GOAL}}", a.goal or a.title)
            .replace("{{SCOPE}}", "\n".join(f"- `{s.strip()}`" for s in (a.scope or "").split(",") if s.strip()) or "- (fill in)")
            .replace("{{DOD}}", "\n".join(f"- [ ] {d.strip()}" for d in (a.dod or "").split(";") if d.strip()) or "- [ ] gates green\n- [ ] (fill in)")
            .replace("{{CHANGES}}", a.changes)
            .replace("**Parent order:** -", "**Parent order:** -   **External:** " + ("yes" if getattr(a, "external", False) else "no")
                     + "   **Rung 3:** " + ("required" if important else "no")
                     + (f"   **Token estimate:** {usage.fmt(est)}" if est else "")
                     + waiver_line(getattr(a, "waive", None)))
            .replace("{{QUALITY}}", "\n".join(f"- {q}" for q in quality)
                     or "- (the Lead fills this: UX, correctness, look, edge cases, tone)")
            .replace("{{LANG}}", CFG.get("user_language", "ru"))
            .replace("{{DELIVERABLE}}", a.deliverable or f"{CFG['_layout']['read'].relative_to(ROOT).as_posix()}/{oid}_{slugify(a.title)}.md"))
    (CIRCLE / "orders" / fname).write_text(body, encoding="utf-8", newline="\n")
    idx = CIRCLE / "orders" / "00_INDEX.md"
    with idx.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"| {oid} | `{fname}` | {a.tier} | {a.changes} | OPEN |\n")
    for w in waivers_of(getattr(a, "waive", None)):
        journal(f"waiver (owner's words): {w}", oid)
    journal(f"new order {a.tier}: {a.title}" + (" [rung 3 required]" if important else "")
            + (f" [domain {'+'.join(doms)}]" if doms else ""), oid)
    if a.fixes:  # K12 (0.8): an owner's correction of a closed order is a lesson, not just a new order
        for f in [x.strip() for x in a.fixes.split(",") if x.strip()]:
            auto_lesson(f, "owner", f"{f} needed an owner correction after closing (fixed by {oid})", a.goal or a.title,
                        (CIRCLE / "orders" / fname).relative_to(ROOT).as_posix())
            journal(f"owner correction of {f} -> {oid} (lesson written)", oid)
    try:  # K26 (0.7.1): the plugin itself checks whether a swarm is the optimal shape for this unit
        n_files = (a.deliverable.count(",") + 1) if a.deliverable else 1
        n_dod = len([x for x in (a.dod or "").split(";") if x.strip()])
        if a.tier in ("HEAVY", "LEAD") and (n_files >= 2 or n_dod >= 8):
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from swarm_probe import decide
            d = decide(files=max(n_files, 3 if n_dod >= 10 else n_files))
            print(("K26: SWARM looks optimal (%s) — %s; write the manifest first (forge-swarm) and record 'mode: %s, because ...' in the order"
                   % (d["mode"], d["why"], d["mode"])) if d["swarm"] else
                  ("K26: single order is optimal — %s" % d["why"]))
    except Exception:
        pass
    print(f"{oid} created: circle/orders/{fname}")


CLARIFY = re.compile(r"\[NEEDS CLARIFICATION:?\s*([^\]]*)\]", re.I)


def clarifications(order_path: Path) -> list[str]:
    """Open `[NEEDS CLARIFICATION: question]` markers in an order (the Lead writes them while drafting the DoD)."""
    try:
        text = order_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [m.group(1).strip() or "(no question written)" for m in CLARIFY.finditer(text)]


def cmd_take(a) -> None:
    st = read_state(ROOT)
    if st.get("STATE") not in ("READY", "RUNNING"):
        raise SystemExit(f"STATE is {st.get('STATE')} — executors are blocked (K9)")
    r = row(a.id)
    # R26: LOCAL/GPT orders (external executors) may run in parallel with the Lead's one Claude order
    taken_rows = [x for x in read_index(ROOT) if x["state"] == "TAKEN"]
    busy = [x["id"] for x in taken_rows if x["tier"] not in EXTERNAL_TIERS]
    if r["tier"] not in EXTERNAL_TIERS and busy:
        raise SystemExit(f"already TAKEN (Claude tier): {busy} — close or hold it first; LOCAL/GPT/CODEX orders may run alongside")
    if r["tier"] in ("GPT", "CODEX") and not gpt_allowed(CFG):
        raise SystemExit('K15: ChatGPT is not allowed in this project (forge.json "gpt") - ask the owner first')
    if r["state"] not in ("OPEN", "HOLD"):
        raise SystemExit(f"{a.id} is {r['state']}, not OPEN/HOLD")
    if not CFG.get("mode"):
        raise SystemExit("K25: mode not set — ask the owner (cheap = Ollama drafts / expensive = Claude only), then: step.py mode <cheap|expensive>")
    if CFG["mode"] == "expensive" and r["tier"] == "LOCAL":
        raise SystemExit(f"{a.id}: LOCAL tier is disabled in expensive mode (K25) — re-tier to FAST/HEAVY/LEAD or switch mode")
    open_q = clarifications(CIRCLE / "orders" / r["file"])
    if open_q:  # 1.1.0 (E4, from spec-kit): a DoD with open [NEEDS CLARIFICATION] markers is not ready to execute (K19, K22)
        raise SystemExit(f"{a.id}: {len(open_q)} open [NEEDS CLARIFICATION] marker(s) - ask the owner, write the answer in place "
                         f"(`order.py amend`), then take:\n" + "\n".join(f"  - {q}" for q in open_q[:8]))
    write_index_row(ROOT, a.id, "TAKEN")
    current = st.get("CURRENT_ORDER", "-")
    if r["tier"] not in EXTERNAL_TIERS or current in ("-", "", None):
        current = a.id  # CURRENT_ORDER = the Lead's order when there is one, else the (first) external one
    write_state(ROOT, STATE="RUNNING", CURRENT_ORDER=current)
    journal("taken", a.id)
    try:  # K34d: what is already dirty when the work starts is not this order's change; fail-open - `take` never fails here
        tree = git_tree(ROOT)
        if tree is not None:
            snap = {"order": a.id, "taken": f"{today()} {now_hm()}", "dirty_at_take": len(tree), "skipped": False, "base": tree}
            body = json.dumps(snap, ensure_ascii=False, indent=1) + "\n"
            if len(tree) > TREE_CAP or len(body.encode("utf-8")) > 48 * 1024:  # stays under gate_size (64 KB)
                snap.update(skipped=True, base={}); body = json.dumps(snap, ensure_ascii=False, indent=1) + "\n"
                print(f"K34 scope: {len(tree)} uncommitted paths at take - the undeclared-change check is skipped for {a.id} "
                      f"(commit or ignore them first)")
            (CIRCLE / "answers" / f"{a.id}_scope.json").write_text(body, encoding="utf-8", newline="\n")
    except Exception as e:  # noqa: BLE001 - visibility aid only
        print(f"K34 scope: snapshot skipped ({type(e).__name__}: {e})")
    print(f"{a.id} TAKEN — STATE RUNNING")


def scope_check(order_id: str, files: list[str]) -> list[str] | None:
    """K34d (0.9.2): files changed since `take` that the answer does not declare - another session, or a missing --files
    entry (N011: the owner document never reached the commit). Visibility, never a block: the folder is shared.
    None = no snapshot (not git, or taken before 0.9.2), or too many dirty paths at take."""
    f = CIRCLE / "answers" / f"{order_id}_scope.json"
    if not f.is_file():
        return None
    try:  # fail-open: a broken snapshot or a git failure never blocks a close (N013 verifier2 #1)
        now = git_tree(ROOT)
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if now is None or d.get("skipped"):
        return None
    base = d.get("base", {})
    own = [p.relative_to(ROOT).as_posix() for p in (CIRCLE, CFG["_layout"]["state"]) if p != ROOT]  # the order's own records
    declared = [x.replace("\\", "/").rstrip("/") for x in files] + own
    changed = sorted(p for p, h in now.items() if base.get(p) != h)
    undeclared = [p for p in changed if not any(p == x or p.startswith(x + "/") for x in declared)]
    d.update(checked=f"{today()} {now_hm()}", changed=len(changed), undeclared=undeclared[:100])
    try:  # the record is a convenience: a locked or unwritable file never blocks the close (N013 rung 3 #3)
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass
    return undeclared


def cmd_answer(a) -> None:
    r = row(a.id)
    files = [f.strip() for f in (a.files or "").split(",") if f.strip()]
    missing = [f for f in files if not (ROOT / f).exists()]
    if missing:  # L010 (N011): garbled Cyrillic paths were accepted and the owner document never reached the commit
        raise SystemExit(f"L010: {len(missing)} declared path(s) do not exist - nothing recorded: {'; '.join(missing[:5])}. Pass the "
                         f"paths literally (a console code page garbles Cyrillic: never build --files through `python -c print`)")
    data = {"order": a.id, "executor": a.executor, "tier": a.tier or r["tier"], "status": a.status,
            "files_changed": files,
            "summary": a.summary, "checks": [c.strip() for c in (a.checks or "").split(";") if c.strip()],
            "when": f"{today()} {now_hm()}"}
    if a.status == "blocked":  # K34e: an unsure executor asks instead of guessing; the guess is often enough for the Lead
        if not all((x or "").strip() for x in (a.question, a.ambiguous, a.guess)):
            raise SystemExit("K34e: --status blocked needs --question, --ambiguous and --guess: a stop costs one message, "
                             "a silent guess inside the deliverable costs redoing everything built on it")
        data["blocked"] = {"question": a.question.strip(), "what_is_ambiguous": (a.ambiguous or "").strip(), "my_guess": a.guess.strip()}
    f = CIRCLE / "answers" / f"{a.id}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    journal(f"answer by {a.executor}: {a.status}", a.id)
    print(f"answers/{f.name} written")
    record_usage(a, a.executor)
    if a.status == "blocked":
        cmd_move(argparse.Namespace(id=a.id, why=f"executor asks: {a.question.strip()} | guess: {a.guess.strip()}"), "BLOCKED")
        print(f"Lead: answer the question (confirm the guess or decide), record it with `order.py amend {a.id} --why \"...\"`, "
              f"then `order.py reopen {a.id}` and `take` it again")


def cmd_report(a) -> None:
    r = row(a.id)
    require_owner_words(a)
    tpl = (CFG["_kernel_dir"] / "templates" / "report.md").read_text(encoding="utf-8")
    body = (tpl.replace("{{ID}}", a.id).replace("{{DATE}}", today()).replace("{{VERDICT}}", a.verdict)
            .replace("{{TITLE}}", r["changes"]).replace("{{TEXT}}", a.text).replace("{{BY}}", a.by)
            .replace("{{NOT_DONE}}", "\n".join(f"- {x.strip()}" for x in (getattr(a, "not_done", None) or "").split(";") if x.strip())
                     or "- (not recorded)"))
    extra = []
    if getattr(a, "verified_by", None):
        extra.append("VERIFIED-BY: " + a.verified_by)
    if getattr(a, "rung3", None):        # K30: the Lead's triage of every ChatGPT finding
        extra.append("RUNG3: " + a.rung3)
    if getattr(a, "rung3_skip", None):   # K30: why the ChatGPT audit did not run (K15, limits, unavailable)
        extra.append("RUNG3-SKIP: " + a.rung3_skip)
    if getattr(a, "overrule", None):     # K31: closing over a CHANGES review needs a written reason (and becomes a lesson)
        extra.append("OVERRULED: " + a.overrule)
    if owner_words(a) and family(a.by) == "owner":
        extra.append("OWNER-TEXT: " + owner_words_json(a))
    if extra:
        body = body.replace("BY: " + a.by, "BY: " + a.by + "\n" + "\n".join(extra), 1)
    f = CIRCLE / "reports" / f"{a.id}.md"
    f.write_text(body, encoding="utf-8", newline="\n")
    journal(f"report {a.verdict} by {a.by}", a.id)
    if owner_words(a) and family(a.by) == "owner":
        journal(f"owner's words (verbatim, one JSON string): {owner_words_json(a)}", a.id)
    if a.verdict in ("CHANGES", "REJECT"):
        auto_lesson(a.id, "review", f"{a.id} ({r['changes'][:60]}): verdict {a.verdict} by {a.by}", a.text[:300])
    if getattr(a, "overrule", None):
        auto_lesson(a.id, "overrule", f"{a.id}: a reviewer said CHANGES and the Lead overruled it", a.overrule[:300],
                    f.relative_to(ROOT).as_posix())
    print(f"reports/{f.name} written: {a.verdict}")


def cmd_close(a) -> None:
    r = row(a.id)
    if not (CIRCLE / "answers" / f"{a.id}.json").is_file():
        raise SystemExit(f"no answers/{a.id}.json — run: order.py answer {a.id} ...")
    if json.loads((CIRCLE / "answers" / f"{a.id}.json").read_text(encoding="utf-8")).get("status") == "blocked":
        raise SystemExit(f"{a.id}: K34e — the answer is 'blocked' (the executor asked a question): resolve it and record a new answer")
    rep = CIRCLE / "reports" / f"{a.id}.md"
    if not rep.is_file():
        raise SystemExit(f"no reports/{a.id}.md — run: order.py report {a.id} --verdict ...")
    rep_text = rep.read_text(encoding="utf-8")
    # 1.2.0: the report's verdict is its header line (template line 3), not a substring: a CHANGES report that quotes
    # 'VERDICT: APPROVE' in its text must not close (the same rule as for a review file)
    head = re.search(r"^VERDICT:\s*(\w+)\s*$", rep_text, re.M)
    if not head or head.group(1) != "APPROVE":
        raise SystemExit(f"{a.id}: report verdict is not APPROVE — fix and re-report, or hold/block")
    executor = json.loads((CIRCLE / "answers" / f"{a.id}.json").read_text(encoding="utf-8")).get("executor", "")
    by = next((l.split(":", 1)[1].strip() for l in rep_text.splitlines() if l.startswith("BY:")), "")
    if same_actor(executor, by):
        raise SystemExit(f"{a.id}: K8 — executor '{executor}' and reviewer '{by}' are the same actor ({family(executor)} by "
                         f"{family(by)}); a Claude (Lead/verifier2) or owner report is required to close "
                         f"(the owner's: --by owner --owner-text \"<the owner's words>\")")
    if family(by) == "gpt":
        raise SystemExit(f"{a.id}: K29 — ChatGPT's verdict is advisory; the closing report is written by Claude (Lead) or the owner")
    order_md = (CIRCLE / "orders" / r["file"]).read_text(encoding="utf-8")
    rv = review_files(a.id)
    verdicts = {k: review_verdict(p) for k, p in rv.items()}
    if family(by) == "ollama" and verdicts["ollama"] != "APPROVE":  # K31: a claimed Ollama approval needs its review file
        raise SystemExit(f"{a.id}: K31 — BY '{by}' claims an Ollama approval, but answers/{a.id}_review.json is missing or not APPROVE "
                         f"(run verify.py {a.id})")
    overruled = "OVERRULED:" in rep_text
    # K31: a CHANGES/REJECT from Ollama or verifier2 cannot be silently closed over (audit 2026-10-05: 9 such closes)
    for k in ("ollama", "verifier2"):
        if verdicts[k] in ("CHANGES", "REJECT") and not overruled:
            auto_lesson(a.id, "review", f"{a.id}: {rv[k].name} says {verdicts[k]} at close time", "close refused until re-review or overrule",
                        rv[k].relative_to(ROOT).as_posix())  # K12: every CHANGES verdict is a lesson (ChatGPT audit N009 #8)
            raise SystemExit(f"{a.id}: K31 — {rv[k].name} says {verdicts[k]}: fix and re-run that review, or record why it is wrong: "
                             f"report {a.id} ... --overrule \"reason\" (a lesson is written)")
    # R24/K31 (0.8): a Claude-made artifact closed on a Claude report needs EVIDENCE of a second actor - only THIS order's
    # review files count (a path in VERIFIED-BY could point at the order's own report or a ChatGPT file: N009 audits #1),
    # the evidence must be newer than the deliverable (no stale APPROVE), and an unknown reviewer name is treated as Claude.
    rung3_required = "**Rung 3:** required" in order_md
    owner_ok = family(by) == "owner"
    if owner_ok and not re.search(r"^OWNER-TEXT:\s*\S", rep_text, re.M):  # 1.2.0: also catches a hand-edited report
        raise SystemExit(f"{a.id}: owner confirmation without the owner's words - report {a.id} --verdict APPROVE --by owner "
                         f"--owner-text \"<the owner's words>\" (kept verbatim in the report and the journal)")
    claude_made = family(executor) in ("claude", "other")
    ans_files = json.loads((CIRCLE / "answers" / f"{a.id}.json").read_text(encoding="utf-8")).get("files_changed", [])
    gone = [f for f in ans_files if not (ROOT / f).exists()]
    if gone:  # N013 rung 3 #1: a declared result deleted after `answer` must not close silently (L010 class at close time)
        raise SystemExit(f"{a.id}: K34 — {len(gone)} declared file(s) no longer exist: {'; '.join(gone[:5])} — restore them or "
                         f"record a new answer without them")
    # 1.2.0 (DoD 3): evidence must be newer than the newest declared file - through a declared folder too - and never older than
    # this work cycle's `take` (an answer that declares nothing, or only a folder, no longer lets an old review pass)
    newest, cut = newest_mtime(ans_files)
    newest = max(newest, taken_at(a.id))
    if cut:
        print(f"{a.id}: freshness looked at the first {NEWEST_SCAN_CAP} files under the declared folders only - declare files, not a huge folder")
    fresh = lambda k: rv[k].is_file() and rv[k].stat().st_mtime >= newest - 1  # noqa: E731
    # an overrule only answers a real CHANGES/REJECT - an empty or verdict-less file is not a review (N009 verifier2 r2 B)
    passed = lambda k: fresh(k) and (verdicts[k] == "APPROVE" or (overruled and verdicts[k] in ("CHANGES", "REJECT")))  # noqa: E731
    if family(by) == "ollama" and not passed("ollama"):  # a claimed Ollama approval must be fresh too (r2 A)
        raise SystemExit(f"{a.id}: K31 — answers/{a.id}_review.json is older than the deliverable: re-run verify.py {a.id}")
    def review_problem(k: str) -> str:
        """Why a review file that EXISTS does not count: stale (re-run it) or unreadable verdict (fix the first line, no re-run)."""
        if not rv[k].is_file():
            return ""
        if not fresh(k):
            return "older than the deliverable: re-run it"
        if not verdicts[k]:
            return ("found, but its first non-empty line is not VERDICT: APPROVE|CHANGES|REJECT - move the verdict line to the top "
                    "(no re-run needed)" if rv[k].suffix == ".md" else "found, but it has no readable verdict: re-run it")
        return ""
    # important + Claude-made: verifier2 is mandatory whoever signs the report, unless the owner does (N009 verifier2 r2 A)
    review_waived = waived(order_md, "review")
    if review_waived:
        journal("close under the owner's waiver of the review (K19) - no second-actor evidence required", a.id)
    if rung3_required and not owner_ok and not review_waived and not passed("verifier2"):  # 1.0: Codex-made important work too
        raise SystemExit(f"{a.id}: K30/K31 — important order: Claude's verifier2 must review it — save its output to "
                         f"answers/{a.id}_review_v2.md (first line 'VERDICT: APPROVE'), newer than the deliverable; Ollama or "
                         f"ChatGPT do not replace it" + (f" ({review_problem('verifier2')})" if review_problem("verifier2") else ""))
    claude_by_claude = claude_made and family(by) in ("claude", "other")
    if claude_by_claude and not review_waived:  # 1.0 (K29): a Codex review is another family - it counts for ordinary orders
        if not (passed("verifier2") or passed("ollama") or (not rung3_required and passed("gpt"))):
            stale = [rv[k].name for k in ("verifier2", "ollama") if rv[k].is_file() and not fresh(k)]
            unread = [rv[k].name for k in ("verifier2",) if rv[k].is_file() and fresh(k) and not verdicts[k]]  # a text file: the line can be moved
            raise SystemExit(f"{a.id}: K8/R24 — a Claude-made artifact needs a second actor WITH evidence: save the verifier2 output to "
                             f"answers/{a.id}_review_v2.md (first line 'VERDICT: APPROVE'), or run verify.py {a.id} (Ollama, cheap mode), "
                             f"or a Codex review (verify_gpt.py {a.id} -> answers/{a.id}_review_gpt.json APPROVE; ordinary orders), "
                             f"or the owner's ok (`--by owner --owner-text \"<the owner's words>\"`, verbatim). Important orders need verifier2 (K30)"
                             + (f". Stale (older than the deliverable): {', '.join(stale)}" if stale else "")
                             + (f". Found, but no verdict on the first non-empty line (VERDICT: APPROVE|CHANGES|REJECT; move it to the "
                                f"top, no re-run needed): {', '.join(unread)}" if unread else ""))
    # K30: important orders get an independent ChatGPT audit on top of Claude's check; the Lead triages every finding
    gpt_ran = verdicts["gpt"] in ("APPROVE", "CHANGES", "REJECT")  # a failed/empty run is not an audit (ChatGPT audit N009 #4)
    if gpt_ran and "RUNG3-SKIP:" not in rep_text and rv["gpt"].stat().st_mtime < taken_at(a.id):  # K31 (0.9): an earlier cycle's audit is not this audit (N010 rung 3 #4)
        raise SystemExit(f"{a.id}: K30/K31 — {rv['gpt'].name} is older than the order's last 'taken' (an earlier work cycle): "
                         f"re-run verify_gpt.py {a.id} or record --rung3-skip \"reason\"")
    # fixes made after the audit are covered by verifier2: for important orders passed('verifier2') above already requires a
    # review newer than every deliverable, so it is newer than the audit too
    if rv["gpt"].is_file() and gpt_ran and "RUNG3:" not in rep_text and "RUNG3-SKIP:" not in rep_text:
        raise SystemExit(f"{a.id}: K30 — {rv['gpt'].name} exists: triage every ChatGPT finding in the report: "
                         f"report {a.id} ... --rung3 \"accepted N: ..., rejected M: why, checked K against a primary source\"")
    if rung3_required and claude_made and not ((gpt_ran and "RUNG3:" in rep_text) or "RUNG3-SKIP:" in rep_text or owner_ok):
        if rv["gpt"].is_file() and not gpt_ran:
            raise SystemExit(f"{a.id}: K30 — {rv['gpt'].name} has no verdict (the ChatGPT run failed): re-run verify_gpt.py {a.id} "
                             f"or record --rung3-skip \"reason\"")
        raise SystemExit(f"{a.id}: K30 — important order: run `verify_gpt.py {a.id}` (ChatGPT audit, advisory) and triage it with "
                         f"--rung3, or record why it cannot run: --rung3-skip \"reason\" (e.g. K15 not allowed, limit reached)")
    # R21: documents for third parties close only after the owner's explicit ok
    if "**External:** yes" in order_md and not owner_ok:  # only a report BY the owner counts (N009 verifier2 #5)
        write_state(ROOT, STATE="AWAITING_OWNER", CURRENT_ORDER=a.id)
        journal("awaiting owner ok (external document, K8/R21)", a.id)
        raise SystemExit(f"{a.id}: this document goes to third parties — STATE AWAITING_OWNER; after the owner's ok: "
                         f"order.py report {a.id} --verdict APPROVE --by owner --owner-text \"<the owner's words>\" --text \"ok\", then close")
    # gate_lessons: known failure classes from the lessons registry are checked on the deliverable before closing
    problems = lesson_checks(a.id)
    if problems:
        auto_lesson(a.id, "gate", f"close of {a.id} refused: known failure class repeated", "; ".join(problems)[:300])
        raise SystemExit(f"{a.id}: gate_lessons — " + "; ".join(problems) + " — fix the deliverable, then close again")
    und = scope_check(a.id, ans_files)
    if und:  # K34d: visibility, not a block - another session may be writing in the same folder
        print(f"K34 scope: {len(und)} path(s) changed since `take` but not declared in files_changed (another session, or a missing "
              f"--files entry - it will NOT be committed): {'; '.join(und[:5])}" + (" ..." if len(und) > 5 else ""))
        journal(f"scope: {len(und)} undeclared changed path(s), see answers/{a.id}_scope.json", a.id)
    write_index_row(ROOT, a.id, "CLOSED")
    st = read_state(ROOT)
    others = [x for x in read_index(ROOT) if x["state"] == "TAKEN" and x["id"] != a.id]
    if st.get("CURRENT_ORDER") == a.id:
        nxt = next((x["id"] for x in others if x["tier"] != "LOCAL"), others[0]["id"] if others else "-")
        write_state(ROOT, STATE="RUNNING" if others else "READY", CURRENT_ORDER=nxt)
    if not gates_green():
        write_index_row(ROOT, a.id, r["state"])
        if st.get("CURRENT_ORDER") == a.id:
            write_state(ROOT, STATE=st.get("STATE", "READY"), CURRENT_ORDER=a.id)
        auto_lesson(a.id, "gate", f"close of {a.id} refused: red gates", "see state/gates_last.json")
        raise SystemExit("gates RED — order stays open; fix, then close again")
    journal("closed", a.id)
    print(f"{a.id} CLOSED — gates green, STATE {'RUNNING' if others else 'READY'}")
    try:  # R-37: what the order cost in Claude tokens vs its estimate - visibility, never a block
        msg = usage.close_summary(CFG, a.id)
    except Exception as e:  # noqa: BLE001
        msg = f"{a.id} tokens: summary skipped ({type(e).__name__}: {e})"
    if msg:
        journal("usage: " + msg.split(" ", 1)[1], a.id)
        print(msg)
    else:
        print(f"{a.id} tokens: none recorded - name the order id in agent prompts (the usage hook books them) or paste a usage "
              f"block: order.py usage {a.id} --who <agent> --usage \"...\"")


def declared_deliverables(order_md: str) -> set[str]:
    """The order's Deliverable paths: the Deliverable section plus `deliverable += ...` amendments (0.9.2)."""
    sec = order_md.split("## Deliverable", 1)[1].split("\n## ", 1)[0] if "## Deliverable" in order_md else ""
    return set(re.findall(r"^- `([^`]+)`", sec, re.M) + re.findall(r"deliverable \+= `([^`]+)`", order_md))


def lesson_checks(order_id: str) -> list[str]:
    """gate_lessons (Sibyl 'registry of recovered failures'): built-in classes + lessons that carry a `check` regex."""
    ans = CIRCLE / "answers" / f"{order_id}.json"
    files = json.loads(ans.read_text(encoding="utf-8")).get("files_changed", []) if ans.is_file() else []
    texts = [(f, (ROOT / f).read_text(encoding="utf-8", errors="replace")) for f in files if (ROOT / f).is_file() and (ROOT / f).suffix.lower() in (".md", ".txt", ".html")]
    checks = [("latex tokens left in a deliverable (L: local_exec normalise)", r"\$\\(le|ge|rightarrow|times)\$"),
              ("deliverable is a plan edit, not the artifact (L003 class)", r"^#+\s*PLAN\.md|^Update the .* section"),
              ("answer-record JSON pasted instead of the deliverable (L003)", r'^\s*\{\s*"order"\s*:\s*"N\d{3}"')]
    for lf in (CIRCLE / "lessons").glob("*.json"):
        try:
            d = json.loads(lf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        c = d.get("check")
        if isinstance(c, dict) and c.get("pattern"):
            checks.append((f"{d.get('id')}: {d.get('what_failed', '')[:60]}", c["pattern"]))
    lang = CFG.get("user_language", "ru")
    problems = []
    r = next((x for x in read_index(ROOT) if x["id"] == order_id), None)
    order_md = (CIRCLE / "orders" / r["file"]).read_text(encoding="utf-8") if r and (CIRCLE / "orders" / r["file"]).is_file() else ""
    declared = declared_deliverables(order_md)
    for f, text in texts:
        for name, pat in checks:
            try:
                if re.search(pat, text, re.M | re.I):
                    problems.append(f"{f}: {name}")
            except re.error:
                continue
        read_rel = CFG["_layout"]["read"].relative_to(ROOT).as_posix() if CFG["_layout"]["read"] != ROOT else ""
        if lang == "ru" and len(text) > 400 and read_rel and f.replace("\\", "/").startswith(read_rel + "/"):  # R-24: only the user part must be RU (K13: Core 2 and the plugin are English)
            letters = [ch for ch in text if ch.isalpha()]
            # documents only: the owner reads .md (+ its .pdf); a .txt next to them is raw tool output (N012: contrast,
            # ffmpeg filter and QC dumps were refused as "not RU") - unless that .txt IS the order's declared Deliverable (N012 r3)
            doc = f.endswith(".md") or (f.endswith(".txt") and f.replace("\\", "/") in declared)
            if letters and sum("Ѐ" <= ch <= "ӿ" for ch in letters) / len(letters) < 0.3 and doc:
                problems.append(f"{f}: deliverable is not in the user's language (ru) — R1 class")
    return problems


def cmd_move(a, state: str) -> None:
    row(a.id)
    if state in ("HOLD", "BLOCKED") and not (a.why or "").strip():
        raise SystemExit(f"R-4 (0.8): {state} needs a reason — order.py {state.lower() if state == 'HOLD' else 'block'} {a.id} --why \"...\"")
    write_index_row(ROOT, a.id, state)
    st = read_state(ROOT)
    if st.get("CURRENT_ORDER") == a.id and state != "TAKEN":
        # orders still TAKEN alongside (LOCAL/GPT) keep the machine RUNNING (ChatGPT audit N009 #6)
        others = [x for x in read_index(ROOT) if x["state"] == "TAKEN" and x["id"] != a.id]
        nxt = next((x["id"] for x in others if x["tier"] not in EXTERNAL_TIERS), others[0]["id"] if others else "-")
        write_state(ROOT, STATE="RUNNING" if others else "READY", CURRENT_ORDER=nxt)
    journal(f"{state.lower()}: {a.why or ''}".strip(), a.id)
    print(f"{a.id} -> {state}")


def cmd_list(a) -> None:
    st = read_state(ROOT)
    print(f"STATE: {st.get('STATE')}  current: {st.get('CURRENT_ORDER')}  updated: {st.get('UPDATED')}")
    for r in read_index(ROOT):
        print(f"{r['id']}  {r['state']:<7} {r['tier']:<5} {r['changes'][:70]}")


STATUS = {"OPEN": "todo", "TAKEN": "doing", "HOLD": "doing", "BLOCKED": "todo", "CLOSED": "done"}
ACTOR = {"LOCAL": "ollama", "FAST": "claude", "HEAVY": "claude", "LEAD": "claude", "GPT": "gpt", "CODEX": "gpt"}


def sync_project_json() -> None:
    """R23: keep state/project.json `tasks` equal to the orders index so the dashboard is always current."""
    pj = CFG["_layout"]["state"] / "project.json"
    if not pj.is_file():
        return
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["tasks"] = [{"id": r["id"], "phase": d.get("phase_current", 0), "title": r["changes"], "status": STATUS.get(r["state"], "todo"),
                   "actor": ACTOR.get(r["tier"], "claude")} for r in read_index(ROOT)]
    d["updated"] = today()
    pj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def cmd_done(a) -> None:
    """R25: one command = answer (if missing) + report + close + commit. The Lead writes the verdict once."""
    ans = CIRCLE / "answers" / f"{a.id}.json"
    a.verdict = a.verdict or "APPROVE"
    # 1.2.0: every refusal that depends only on the arguments and the index comes before anything is written - a second `done`
    # after a refusal must not book the same --tokens twice or leave a half-made answer, report and journal behind
    declared = None if ans.is_file() else [f.strip() for f in (a.files or "").split(",") if f.strip()]
    if a.verdict == "APPROVE":
        require_owner_words(a)
        if a.commit is not None:
            row(a.id)
            check_staged(a.id, getattr(a, "allow_extra", None), declared)
    if not ans.is_file():
        if not a.summary:
            raise SystemExit(f"no answers/{a.id}.json yet — give --executor and --summary (or run `answer` first)")
        a.executor = a.executor or "lead:claude"; a.status = "done"; a.tier = None; a.checks = a.checks or ""
        cmd_answer(a)
    else:
        record_usage(a, a.executor or json.loads(ans.read_text(encoding="utf-8")).get("executor", "lead:claude"))
    cmd_report(a)
    if a.verdict != "APPROVE":
        print(f"{a.id}: verdict {a.verdict} recorded, order stays open"); return
    cmd_close(a)
    if a.commit is not None:
        sync_project_json()  # before the commit, so project.json is not left dirty (N009 verifier2 #7)
        check_staged(a.id, getattr(a, "allow_extra", None), final=True)
        commit_order(a.id, a.commit or f"{a.id}: {row(a.id)['changes']}")


TRAILER = {"claude": "Co-Authored-By: Claude <noreply@anthropic.com>",
           "gpt": "Executed-By: ChatGPT (Codex CLI), reviewed by Claude", "ollama": "Executed-By: Ollama (local), reviewed by Claude"}


def answer_files(order_id: str, files: list[str] | None = None) -> list[str]:
    """The files the order's answer declares (`files` = the not yet recorded answer of a `done` that creates it)."""
    if files is not None:
        return list(files)
    ans = CIRCLE / "answers" / f"{order_id}.json"
    return json.loads(ans.read_text(encoding="utf-8")).get("files_changed", []) if ans.is_file() else []


def order_paths(order_id: str, files: list[str] | None = None, warn: bool = True) -> list[str]:
    """K31/K14 (0.8): what a close commits - the order's own records and its declared files, never `git add -A`
    (in mesh-launch 0a42d3b a Codex-created AGENTS.md rode along in a Claude commit)."""
    r = row(order_id)
    c = CIRCLE.relative_to(ROOT).as_posix()
    paths = [f"{c}/orders/{r['file']}", f"{c}/orders/00_INDEX.md", f"{c}/STATE.md", f"{c}/reports/{order_id}.md",
             f"{c}/journal", (CFG["_layout"]["state"] / "project.json").relative_to(ROOT).as_posix()]  # journal = the shared, append-only log
    paths += [p.relative_to(ROOT).as_posix() for p in (CIRCLE / "answers").glob(f"{order_id}*")]
    paths += [p.relative_to(ROOT).as_posix() for p in (CIRCLE / "lessons").glob("*.json")       # lessons of / about this order (N009)
              if order_id in p.read_text(encoding="utf-8", errors="replace")]
    for f in answer_files(order_id, files):
        if not (ROOT / f).resolve().is_relative_to(ROOT.resolve()):  # never commit outside the project
            if warn:
                print(f"skipped (outside the project): {f}")
            continue
        paths.append(f)
        if f.lower().endswith(".md") and (ROOT / f).with_suffix(".pdf").is_file():  # K13: the PDF twin of a user document
            paths.append(str(Path(f).with_suffix(".pdf")).replace("\\", "/"))
    seen, out = set(), []
    for p in paths:
        if p not in seen and (ROOT / p).exists():
            seen.add(p); out.append(p)
    return out


def staged_scope(order_id: str, allow: str | None = None, files: list[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Staged files outside the order's scope (order_checks.staged_scope), with the order's own paths and Deliverable filled in."""
    if not staged_names():
        return [], [], []
    md = (CIRCLE / "orders" / row(order_id)["file"]).read_text(encoding="utf-8", errors="replace")
    own = order_paths(order_id, files, warn=False) + answer_files(order_id, files)
    return order_checks.staged_scope(allow, own, declared_deliverables(md))


def check_staged(order_id: str, allow: str | None, files: list[str] | None = None, final: bool = False) -> None:
    order_checks.check_staged(order_id, staged_scope(order_id, allow, files), journal, final)


def commit_order(order_id: str, msg: str) -> None:
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True).returncode != 0:
        print(f"{order_id}: closed; not a git repository - nothing committed (git init to keep a trail, K14)"); return
    ans = CIRCLE / "answers" / f"{order_id}.json"
    executor = json.loads(ans.read_text(encoding="utf-8")).get("executor", "") if ans.is_file() else ""
    paths = order_paths(order_id)
    added = subprocess.run(["git", "add", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    if added.returncode != 0:
        raise SystemExit(f"git add failed - nothing committed: {added.stderr.strip()[:300]}")
    trailer = TRAILER.get(family(executor), TRAILER["claude"])
    # pathspec: only these paths go in, even if someone else staged other files earlier (ChatGPT audit N009 #2)
    r = subprocess.run(["git", "commit", "-q", "-m", f"{msg}\n\n{trailer}", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip() or f"committed {len(paths)} path(s) of {order_id}")
    left = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()
    if left:
        print(f"not committed (outside {order_id}): {len(left)} path(s), e.g. " + ", ".join(x[3:] for x in left[:3]))


def cmd_amend(a) -> None:
    """R-20: nine mid-order owner clarifications in ank-launch N005 had nowhere to land; now they land here."""
    r = row(a.id)
    if r["tier"] == "GPT" and a.deliverable and not all(x.strip().lower().endswith(IMAGE_EXT) for x in a.deliverable.split(",") if x.strip()):
        raise SystemExit("K29: a GPT order may only add image deliverables (ChatGPT audit N009 #5)")
    f = CIRCLE / "orders" / r["file"]; md = f.read_text(encoding="utf-8")
    stamp = f"{today()} {now_hm()}"
    block = [f"- **{stamp}** — {a.why}"]
    for answer in getattr(a, "resolve", None) or []:
        m = CLARIFY.search(md)
        if not m:
            raise SystemExit(f"{a.id}: no open [NEEDS CLARIFICATION] marker left for: {answer[:80]}")
        q = m.group(1).strip()
        md = md[:m.start()] + f"[clarified {today()}: {answer}]" + md[m.end():]
        block.append(f"  - clarified: {q[:120]} -> {answer}")
        journal(f"clarified: {q[:60]} -> {answer[:60]}", a.id)
    if a.dod:
        block += [f"  - [ ] {x.strip()}" for x in a.dod.split(";") if x.strip()]
    if a.deliverable:
        block += [f"  - deliverable += `{x.strip()}`" for x in a.deliverable.split(",") if x.strip()]
    est = estimate_arg(a)
    if est:
        block.append(f"  - token estimate := {usage.fmt(est)}")
    for w in waivers_of(getattr(a, "waive", None)):
        block.append(f"  - {WAIVER} {w}")
        journal(f"waiver (owner's words): {w}", a.id)
    md = (md + "\n## Amendments\n" if "## Amendments" not in md else md) + "\n".join(block) + "\n"
    f.write_text(md, encoding="utf-8", newline="\n")
    journal(f"amended: {a.why[:80]}", a.id)
    print(f"{a.id} amended — {len(block) - 1} line(s), journal written")


def usage_flags(p) -> None:
    p.add_argument("--usage", help="R-37: the pasted <usage> block of the Agent result (subagent_tokens / tool_uses / duration_ms)")
    p.add_argument("--tokens", help="R-37: tokens, e.g. 40685 or 41k (when no usage block is at hand)")
    p.add_argument("--tool-uses", type=int); p.add_argument("--duration-ms", type=int)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new"); n.add_argument("title"); n.add_argument("--tier", default="LEAD", choices=CFG["tiers"])
    n.add_argument("--changes", required=True); n.add_argument("--goal"); n.add_argument("--scope", help="context files, comma-separated (read only)")
    n.add_argument("--dod"); n.add_argument("--deliverable", help="output path the executor writes (default <user part>/<id>_<slug>.md)")
    n.add_argument("--quality", help="quality bar lines, semicolon-separated (what an expert would call good; K25)")
    n.add_argument("--domain", help="K32: domain ids from domains.json, comma-separated (skills_map.py detect/list) - the order gets "
                                    "their skills, quality bar, verify-by-running and forbidden lines; default = forge.json domains; 'none' = no domain")
    n.add_argument("--external", action="store_true", help="R21: the deliverable goes to third parties -> closes only after the owner's ok")
    n.add_argument("--important", action="store_true", help="K30: owner-facing final, legal/factual, security/data/payments code or "
                   "mass edit -> verifier2 + a triaged Codex audit (implied by --external); ordinary code changes are not important (1.0)")
    n.add_argument("--fixes", help="K12: ids of closed orders this one corrects after the owner's feedback -> a lesson each")
    n.add_argument("--request", help="K34a: the owner's request word for word (normative: it wins over any derived text)")
    n.add_argument("--request-source", help="K34a: who said it, where and when (chat 2026-10-08, ticket, meeting transcript)")
    n.add_argument("--decision", help="K34: the 5-line decision for the owner, semicolon-separated: what changes; reused; new; where; not touched")
    n.add_argument("--estimate", help="R-37: expected Claude tokens for the whole order (Lead + agents), e.g. 400k - compared at close")
    n.add_argument("--waive", action="append", help="K19 (1.0): the owner lifts a rule, word for word: '<rule>: <words>' "
                                                     "(review, tests, dod, plan, codex); repeat the flag for several")
    for c in ("take", "close"):
        sub.add_parser(c).add_argument("id")
    an = sub.add_parser("answer"); an.add_argument("id"); an.add_argument("--executor", required=True)
    an.add_argument("--status", default="done", choices=["done", "partial", "failed", "blocked"]); an.add_argument("--summary", required=True)
    an.add_argument("--files"); an.add_argument("--checks"); an.add_argument("--tier", choices=CFG["tiers"])
    an.add_argument("--question", help="K34e (with --status blocked): what the executor needs to know")
    an.add_argument("--ambiguous", help="K34e: what exactly is unclear in the order"); an.add_argument("--guess", help="K34e: what the executor would do")
    usage_flags(an)
    rp = sub.add_parser("report"); rp.add_argument("id"); rp.add_argument("--verdict", required=True, choices=["APPROVE", "CHANGES", "REJECT"])
    rp.add_argument("--text", required=True); rp.add_argument("--by", default="lead")
    rp.add_argument("--owner-text", help="1.2.0: with --by owner - the owner's own words confirming the order; kept verbatim (as one JSON string) in the report and the journal")
    rp.add_argument("--verified-by", help="R24: who checked a Claude-made artifact (verifier2 -> evidence answers/N_review_v2.md); a note - close reads the files")
    rp.add_argument("--rung3", help="K30: triage of the ChatGPT audit (accepted / rejected with reason / checked at a source)")
    rp.add_argument("--rung3-skip", help="K30: why the ChatGPT audit of an important order did not run")
    rp.add_argument("--overrule", help="K31: why a CHANGES review is wrong (writes a lesson)")
    rp.add_argument("--not-done", help="K34: what was deliberately left out and why, semicolon-separated")
    am = sub.add_parser("amend", help="R-20 (0.7.0): change DoD/deliverable of an open order WITH a journal line — never drift a check silently")
    am.add_argument("id"); am.add_argument("--why", required=True); am.add_argument("--dod", help="lines to append, semicolon-separated")
    am.add_argument("--deliverable", help="extra deliverable path(s), comma-separated")
    am.add_argument("--estimate", help="R-37: set or correct the order's token estimate (the last one counts)")
    am.add_argument("--waive", action="append", help="K19 (1.0): the owner's waiver, word for word: '<rule>: <words>' "
                                                      "(review, tests, dod, plan, codex); repeat the flag for several")
    am.add_argument("--resolve", action="append", help="1.1.0 (E4): the owner's answer to the first open [NEEDS CLARIFICATION] "
                                                        "marker, written in its place; repeat the flag for the next markers")
    us = sub.add_parser("usage", help="R-37 (0.9.3): book an agent's tokens to the order - the pasted <usage> block or --tokens")
    us.add_argument("id"); us.add_argument("--who", default="agent", help="agent type, e.g. verifier2, executor-fast, Explore; lead = an estimate")
    usage_flags(us); us.add_argument("--estimate", dest="estimate_kind", action="store_true", help="the figure is an estimate, not a measurement")
    for c in ("hold", "block", "reopen"):
        m = sub.add_parser(c); m.add_argument("id"); m.add_argument("--why")
    sub.add_parser("list")
    dn = sub.add_parser("done", help="R25: answer (if missing) + report + close + optional commit in one command")
    dn.add_argument("id"); dn.add_argument("--verdict", default="APPROVE", choices=["APPROVE", "CHANGES", "REJECT"])
    dn.add_argument("--text", required=True, help="the Lead's verdict text (RU for the owner)"); dn.add_argument("--by", default="lead:claude")
    dn.add_argument("--owner-text", help="1.2.0: with --by owner - the owner's own words, verbatim (required for an owner confirmation; kept as one JSON string)")
    dn.add_argument("--allow-extra", help="1.2.0: comma-separated paths that are staged outside the order's scope and are left out of this commit "
                                          "knowingly (they stay staged, not committed); journaled")
    dn.add_argument("--executor"); dn.add_argument("--summary"); dn.add_argument("--files"); dn.add_argument("--checks")
    dn.add_argument("--commit", nargs="?", const="", help="commit the order's files after closing; optional message (default: '<id>: <title>')")
    dn.add_argument("--verified-by"); dn.add_argument("--rung3"); dn.add_argument("--rung3-skip"); dn.add_argument("--overrule")
    dn.add_argument("--not-done", help="K34: what was deliberately left out and why, semicolon-separated")
    usage_flags(dn)
    a = ap.parse_args()
    {"new": cmd_new, "take": cmd_take, "answer": cmd_answer, "report": cmd_report, "close": cmd_close, "list": cmd_list, "done": cmd_done, "amend": cmd_amend,
     "usage": cmd_usage,
     "hold": lambda a: cmd_move(a, "HOLD"), "block": lambda a: cmd_move(a, "BLOCKED"), "reopen": lambda a: cmd_move(a, "OPEN")}[a.cmd](a)
    if a.cmd in ("new", "take", "answer", "close", "done", "hold", "block", "reopen"):  # answer: `blocked` moves the order
        sync_project_json()
    return 0


if __name__ == "__main__":
    sys.exit(main())
