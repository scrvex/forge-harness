---
name: forge-direct
description: Use when the user wants one creative or code artifact with no recurring process - a game, a page, a UI, a poster, a chart, a script or a document («сделай мне …») - where Claude's own quality is the result. Not for recurring work with the person's own data and rules (Forge orders).
---
# Direct path (K25: quality-critical work stays with Claude)

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief).

Why: in cycle 3 the spec of a game was drafted by the 12B local model and the DoD had two lines — the result was far
below what Claude produces directly with a design skill loaded ("Phosphor Snake"). Bureaucracy and weak input, not the
model, cost the quality. Outside a Forge project just do it the way plain Claude does; inside one, keep the trace minimal:

1. **Load the matching Claude skill first** (K32, `forge-route` §1): `python <plugin>/tools/skills_map.py detect "<request>"`
   → `skills_map.py show <id>` → load the primary skill of the stage you start with (HTML/UI/game → `artifact-design`;
   charts → `dataviz`; .docx/.xlsx/.pptx/.pdf → `docx`/`xlsx`/`pptx`/`pdf`; poster/art → `canvas-design`) and keep the card's
   quality bar and verify-by-running lines in front of you. Pass `--domain <id>` to the order below.
2. **One order, tier HEAVY (or LEAD)**: `python <plugin>/tools/order.py new "<title>" --tier HEAVY --changes "<file>"
   --deliverable <user folder>/<file> --dod "<3-6 checkable lines>" --quality "<what an expert calls good: UX, feel, look, edge cases>"`
   then `order.py take N`. Claude writes the DoD and the quality bar itself — never the local model.
3. **Build in one focused pass** with the skill's guidance; keep the whole artifact in one file unless the user asked otherwise.
4. **Verify by running**: open it (browser tab / run the script / render the document) and check every DoD and quality line
   yourself; for interactive artifacts do the scripted behaviour check (start, controls, edge cases). Fix, re-run.
   HTML: `python <plugin>/gates/check_html.py file.html` (encoding, viewport, external scripts, size).
5. Second actor (K8/R24): sub-agent `verifier2` (read-only) on the artifact + order, then
   `order.py done N --text "..." --by verifier2:claude --commit` (or `--by owner --owner-text "<the owner's words>"` after the owner's ok).
6. Report to the user in their language: where the file is, how to open it, what was assumed. No Ollama in this path
   even in cheap mode; the local model may only summarise or extract afterwards if needed.
