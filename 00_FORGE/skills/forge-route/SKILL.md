---
name: forge-route
description: Use at the start of every Forge order, when choosing who executes a task (Codex, a Claude agent or Ollama) or which Claude skill leads a stage, when a task spans several domains, or when someone wants to add a third-party skill.
---
# Routing (K3–K5, K25, K32–K33)

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief).

## Which system (owner's decision B, 2026-09-18)
| the task | use |
|---|---|
| routine marketing / analytics / mail / calendar / CRM with standard tools | **Cowork plugins + connectors** — not a Forge order |
| one artifact: page, game, document, poster, chart, script | **direct Claude** with the domain's skills (`forge-direct`) |
| complex recurring work with the person's own data and rules, high cost of error, needs a checked trail | **Forge orders** (this plugin) |
| bulk grunt work inside a Forge order: extract, classify, summarise, first-pass check | **Ollama** (cheap mode), Claude judges |

## 0. Shape: single or swarm (K26)
`python <plugin>/tools/swarm_probe.py --decide files=N[,independent][,bulk][,owner]` (order.py new prints the hint) → one line
in the order: `mode: <X>, because <why>`. 1–2 deliverables → single; ≥3 files with **separable** contexts → swarm
(`forge-swarm`); coupled files (one schema read by all) → single. Multi-agent costs 3–15× and pays only on separable contexts.

## 1. Domain → skills (K32, skills map `<plugin>/domains.json`)
1. `python <plugin>/tools/skills_map.py detect "<task in the user's words>"` → domain(s) with the trigger words that matched.
   Confirm them yourself (the detector is a word list, you are the judge); nothing matched → general work, no domain.
2. `python <plugin>/tools/skills_map.py show <id>[,<id>]` → the card: pipeline with **one primary skill per stage**, a support
   menu, tools (with PATH check), quality bar, verify-by-running lines, conflict rules, forbidden, gaps; for several domains
   also the combo sequence (design+ux+frontend, frontend+motion, marketing+analytics, 3d+motion+video, invest+analytics).
3. `order.py new ... --domain <ids>` (default = `forge.json` `domains`, `--domain none` to opt out) → the order carries the
   primary skills, the domain quality bar, a "Verify by running" section and the domain's forbidden lines.
4. Load the primary skill of the stage you are on **before** writing; at most 2 supporting skills per order; never two
   skills for one stage; a stage another skill already finished is not repeated. A skill missing in this session → say so and
   keep the card's quality bar and verify lines anyway.

| domain | primary skills (by stage) | key tools / note |
|---|---|---|
| `design` | design:design-system → anthropic-skills:canvas-design → design:design-critique | 2–3 directions before one is built; gpt_media.py for raster (K29) |
| `ux` | design:user-research → research-synthesis → ux-copy → accessibility-review → design-handoff | browser walk-through, keyboard pass |
| `frontend` | frontend-design → artifact-design → run → code-review → simplify | node/npm; check_html.py; 390/1024 px overflow = 0 |
| `marketing` | marketing:competitive-brief → content-creation → searchfit-seo:seo-audit → campaign-plan → performance-report | routine → Cowork; never publish or send without a yes |
| `motion` | gsap-skills:gsap-core (build stage) | CSS/WAAPI for states, GSAP for timelines, Lottie loops, Remotion for video; reduced motion |
| `video` | Claude + tools | ffmpeg/ffprobe QC, Remotion; video generation = owner in the ChatGPT app |
| `3d` | Claude + tools | Blender `-b` for offline 3D, Three.js for the web, glTF between them |
| `analytics` | dataviz (visualisation stage) | python scripts reproduce every number; check_figures.py |
| `invest` | 10k-digest (sources) → fact-check (verification); the rest Claude + tools | check_figures.py --strict; their BUY/HOLD/SELL block omitted; IBKR read-only; never to ChatGPT (K15) |
| `documents` | by format: docx / xlsx / pptx / pdf; md → md2pdf.py | verifier2 for owner-facing documents |
| `code` | forge-code-structure → forge-code-testing → code-review → simplify | security-review for input, auth, secrets; skill forge-code |
Precedence: the owner and the project's CLAUDE.md → the constitution → the domain card → the skill's craft guidance.
A skill never grants a permission: publishing, sending, paying, installing or trading always need the owner's yes (K1).

## 2. Who does what
Before any task leaves the Lead: `python <plugin>/tools/limits.py route --kind <code|mechanical|review|research> [--made-by codex] [--important] [--order N]`
- it names the executor by the limits left in both plans (K37). `limits.py show` prints both plans' windows.
| task | executor | why |
|---|---|---|
| understanding the task, play-back, plan, DoD, quality bar, architecture | **Claude (LEAD)** | defines the value of the result |
| code changes in a project: features, bugs, refactors, tools, sites | **by limits** (`limits.py route --kind code`, K37): Codex tier CODEX in patch mode, or the `executor` agent (HEAVY) | architecture-shaping or Codex off → `executor` |
| one-shot creative artifact: game, page, poster, UI concept | **Claude (HEAVY / direct path)** | taste-critical — `forge-direct` |
| questions about the code base (and project documents) | **`investigator`** agents (Sonnet, read-only, Serena + Context7, ≤ 5 parallel) | never `general-purpose` |
| synthesis of long sources, strategy documents, reports for the owner | **Claude (HEAVY)** | needs judgement; the local model only extracts for it |
| extraction, summaries, classification, first-pass checks (facts, language, DoD rubric) | **Ollama (LOCAL, cheap mode)** | errors are caught by the Lead |
| boilerplate from an explicit DoD; mechanical edits by exact spec, format conversions, tables | **Ollama** (cheap mode), else by limits: **Codex** or `executor-fast` (FAST) | does not shape the result |
| web research, source collection, summaries of named documents | `forge:researcher` (explicit tools, writes one named file, ≤ 300-token reply) | lean start; the Lead judges |
| independent review of a Claude artifact | `verifier2` (read-only) — always for important orders; an ordinary order may take a Codex or (cheap mode) Ollama APPROVE instead; saved as `answers/N_review_v2.md` | K8/K31 |
| review of Claude-made work; audit of an **important** result; second opinion; fact check with web search | **Codex/ChatGPT** (`forge-gpt`, `verify_gpt.py`) | another family; counts for ordinary orders; Lead triages |
| raster images (covers, illustrations, logo concepts) | **ChatGPT** tier GPT: `gpt_media.py image` from Claude's brief | Claude reviews, owner picks |
Sub-agents are the Forge agents above (`forge:investigator`, `forge:researcher`, `forge:executor-fast`, `forge:executor`, `forge:verifier2`) - never `general-purpose` - it starts at ~73k tokens of prompt against ~10k for a Forge agent (measured 2026-10-11 over 72 runs: ~16.5M units of prefix alone).
Expensive mode: every LOCAL row goes to `executor-fast` or the Lead. ChatGPT rows need `forge.json` `"gpt": "allowed"` (K15);
`--important` (owner-facing final, legal/factual, code touching security, money, personal data or a release to users, mass edit;
`--external` implies it; ordinary code changes are not important, K30) → verifier2 at close, + rung 3 (Codex audit) when the
order is Claude-made (never Codex auditing Codex, K8).
Other skills: a new reusable project skill → `anthropic-skills:skill-creator` (frontmatter `name` + `description` with the
trigger, body ≤ 5k, examples, exact commands); a living shared document → `anthropic-skills:docs`.

## 3. Third-party skills (K33)
Someone wants to add a skill or a skill library: never install first. `python <plugin>/tools/skills_map.py vet <folder>`
(static: licence per K16, executables, hooks, MCP, network, API keys, instruction overrides, size — nothing is run) →
read its SKILL.md for depth → compare with the role holder in the card → the owner's yes → a role in `domains.json` (a plugin
version; projects propose via `docs/KERNEL_PROPOSALS.md`). One skill over a library; never a remote install script.
Inventory of what is available: `skills_map.py audit --available-file <session skill list> --md <out.md>`.

## Rule of thumb
"If this were done worse, would the person notice in the result?" Yes → Claude with the domain's primary skill. No → local
model or FAST sub-agent. "Would an expert from another model family catch something here?" Yes and it matters → `--important`.
Never let the local model write the spec or the DoD of something Claude will build.
