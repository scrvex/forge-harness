---
name: forge-gpt
description: Use when Codex or ChatGPT should act as the second model family - a review or audit of Claude-made work, a second opinion, a fact check with web search, or a raster image («проверь через GPT», «спроси у GPT», «второе мнение», «кодекс ревью», «сделай картинку через GPT») - or when an order is important or goes outside. Not for Codex writing code (forge-code), never for the DoD, the plan or decisions.
---
# Codex/ChatGPT in Forge — executor, reviewer and expert, never the Lead (K29–K31, owner's decisions 2026-10-05 and 2026-10-11)

`<plugin>` = the Forge plugin folder from the brief. ChatGPT works only if the owner allowed it for this project
(`forge.json` → `"gpt": "allowed"`, set by `python <plugin>/tools/gpt_setup.py --allow` after the owner's yes — K15).
The owner's deny list (`~/.forge/gpt_policy.json`, e.g. personal finance) is refused by the bridge.

## Who does what
| work | who |
|---|---|
| understanding the task, DoD, plan, architecture, owner-facing text, decisions, every close | **Claude** (Lead) |
| code tasks of a plan, mechanical work by exact spec | **by limits** (`limits.py route`, K37): Codex tier CODEX (`codex_exec.py`) or a Claude executor |
| checking Codex's work | **Claude** — the Lead's report; `verifier2` too for important orders |
| checking Claude's work, ordinary orders | **Codex** `verify_gpt.py N` (its APPROVE counts) or `verifier2` |
| checking Claude's work, important orders | **Claude `verifier2`** (mandatory) + **Codex** rung 3 (advisory, triaged) |
| second opinion on a plan, a risk, a wording, a design direction | **ChatGPT** `advise` |
| fact check of claims (law, registries, prices, dates) with web search | **ChatGPT** `factcheck`, then Claude checks disputed points at the source |
| raster images: covers, illustrations, logo concepts, mock-ups | **ChatGPT** `gpt_media.py image` (tier GPT), Claude writes the brief and reviews |
| video | not available in Codex CLI — Claude writes the script, ChatGPT may review it, the owner generates in the ChatGPT app |
| taste, money, sending outside | **owner** |

## Commands (all from inside the project)
- Audit (rung 3): `python <plugin>/tools/verify_gpt.py N007` → `answers/N007_review_gpt.json`. ChatGPT gets the order, the executor's
  answer and the deliverable copies (images attached), not Claude's review (no anchoring). 1–4 min; Plus limits apply.
- Second opinion: `python <plugin>/tools/verify_gpt.py advise "question" --files a.md` → `state/gpt/advice_*.json`.
- Fact check: `python <plugin>/tools/verify_gpt.py factcheck --claims claims.md --about "..." --asof YYYY-MM-DD --out f.json`.
- Image: `python <plugin>/tools/gpt_media.py image "brief" --out <state>/staging/N012/cover.png [--ref a.png] [--n 2]`;
  then open every image yourself (Read) against the DoD before showing the owner.

## Triage — mandatory for every ChatGPT finding (K30)
For each finding decide: **accept** (fix it), **reject** with a reason (wrong, out of scope, taste), or **check** against
a primary source when it disputes a fact. Record it: `order.py report N ... --rung3 "accepted 2 (…), rejected 1 (…: why), checked 1 at <url>"`.
Then close as usual. If the audit could not run (K15 off, limit reached, Codex down): `--rung3-skip "reason"`.
Never let a model vote settle a fact (pilot N008: ChatGPT was confidently wrong on Indonesian apostille, Claude's earlier
check was right). Codex's verdict never closes an order by itself (the Lead reports); Codex-by-Codex is one actor (K8).

## Cost and limits (pilot N008, ChatGPT Plus)
Audit of a document: 1–4 min, 0.5–0.9M input tokens at effort high (mostly cached); code 1 min. Use `--effort medium`
for ordinary documents, `high` for code and law. Rung 3 is mandatory only for important Claude-made orders; a Codex review of an ordinary order is optional and replaces verifier2.
