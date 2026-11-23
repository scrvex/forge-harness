---
name: forge-new
description: Use when someone describes a recurring task they want a workspace for («я маркетолог, хочу…», «я инвестор, хочу стратегию»), greets Forge, or asks what Forge can do. Not for a single artifact (forge-direct) and not for routine work a Cowork plugin already covers.
---
# New project (K19–K25)

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief; in the dev workspace `C:/ai/agi/00_FORGE`).

0. **Is Forge the right tool?** Routine marketing / analytics / mail / CRM with standard tools → say so and point to the
   Cowork plugin or connector. A single artifact (page, game, document) → skill `forge-direct`. Forge is for complex
   recurring work with the person's own data and rules, where a checked process and accumulated lessons pay off.
1. Greeting / "what can you do" → answer with `<plugin>/templates/HELLO.ru.md` (plain words) + the first three questions.
2. Interview from `<plugin>/templates/brief.md` (user-language column) **until the picture is complete** (K22). If the
   person has materials, read them yourself (or `local_jobs.py extract` in cheap mode) and ask only the gaps.
3. Play the task back in one paragraph + proposed folders + first orders + red lines; wait for "да, собирай" or corrections.
4. **Ask the mode** (K25): cheap (Ollama does extraction, checks, boilerplate drafts) or expensive (Claude only).
   **Ask about Codex/ChatGPT** (K15/K29): may Codex work in this project - write code tasks the Lead hands it, review Claude's
   work, advise and make images? (Claude still leads, plans and judges.) Code project? → `--code "<test command>"`. Answer → `--gpt allowed|denied` below; personal
   finance or anything on the owner's deny list stays `denied`.
5. Write `plan.json` yourself (the Lead designs the architecture, K21) — shape and example: `<plugin>/templates/plan.example.json`:
   task_restated, domain, domains (1–3 ids from the skills map: `python <plugin>/tools/skills_map.py detect "<request>"`;
   they go to forge.json and every order gets their skills, quality bar and verify lines by default — K32), folders (user
   language, no numbers), automations (phrase → what → tier), first_orders (title, goal, dod[], quality[], folder, tier,
   context[], deliverable), red_lines, benefit (no invented numbers), required_from_user.
   Tier rule: LOCAL only for boilerplate a small model writes from the DoD alone; anything that shapes the result → HEAVY/LEAD.
6. `python <plugin>/tools/init_project.py C:/ai/<name> --name <name> --plan plan.json --mode <cheap|expensive> --gpt <allowed|denied> [--code "python -m pytest -q"] --request "..." [--sources f1,dir] --git`
   → `01_PROJECT_CORE/` (Core 2), the user part in the request language with numbered folders, guide .md + .pdf,
   CLAUDE.md, AGENTS.md (Codex's role: executor in patch mode, reviewer, adviser, images — never the Lead), first orders OPEN, gates green.
7. Open the new folder in Claude Code / Cowork (the plugin is already installed, hooks and skills apply). Work the first
   orders (`forge-route`, `forge-review`, `forge-done`); when they are closed → skill `forge-handover`.
