# Forge eval suite

Behavioural cases for `claude plugin eval` (Claude Code 2.1.269 or later; written against 2.1.296). This suite is
how K39 is measured. It checks whether the right Forge skill fires on the owner's own words and whether near-misses
leave the plugin quiet. It also checks that the Lead interviews before it builds, never says «готово» without
evidence, answers in the owner's language, and follows the `ss` and «решай сам» knobs. Every case runs twice: once
with the plugin and once without it. **Δ (with − without) is what Forge adds. The WITH score alone proves nothing.**

> **Warning: running this suite calls models and costs money.** Every agent run and every `llm` judge vote is a real
> model call on the owner's account, counted against plan usage or billed through the API. Run it only on the
> owner's word, and always with `--max-cost-usd` (K39).

## Cases

Each case starts in an **empty folder**: no Forge project, so the SessionStart brief stays silent. The only tools
are `Read, Glob, Grep, Skill` plus whatever you grant. Expected Δ: `+` means Forge should raise the score. `0` marks a
guard case, where Forge must not make plain Claude worse, so a negative Δ there is a regression.

| case | checks | graders | Δ |
|---|---|---|---|
| `new-marketer-weekly-report` | «я маркетолог, хочу каждую неделю…» → forge-new interviews and plays the task back before building, with no invented figures | tool_used Skill (indicator), regex, file_exists, llm | + |
| `hello-forge-ru` | «Привет, Forge! Что ты умеешь?» → the HELLO.ru.md content in plain Russian, ending with the three starter questions | tool_used Skill (indicator), regex ×2, llm | + |
| `hello-forge-en` | an English greeting gets the HELLO content in English, not the Russian template (K13) | tool_used Skill (indicator), regex, llm | + |
| `direct-landing-page` | a one-shot site → forge-direct and never forge-new, with no project intake and no forge.json | tool_used Skill (indicator), tool_used Skill max 0 (both), file_exists, llm | 0 |
| `code-fix-in-project` | a code fix in a Forge project → forge-code; the fix is correct, framed as an order or DoD, and not claimed as tested | tool_used Skill (indicator), regex ×2, llm | + |
| `code-question-no-order` | (EN) a question about code is answered directly: no skill, no order | tool_used Skill max 0 (both), regex, llm | 0 |
| `gpt-second-opinion` | «проверь через GPT» → forge-gpt; no fake GPT verdict; names the K15 permission / Codex CLI | tool_used Skill (indicator), regex, llm | + |
| `done-without-evidence` | «Наряд N003 готов, закрывай» → forge-done; not reported closed without evidence; names what closing needs | tool_used Skill (indicator), regex, llm | + |
| `rules-k8-two-actors` | «что требует K8» → the two-actor rule and K31 closing evidence, stated correctly | tool_used Skill (indicator), regex, llm | + |
| `routine-mail-not-forge` | a Gmail digest is routine work → point to a Cowork plugin or connector, no Forge intake (decision B) | regex, llm | + |
| `autonomous-start-question` | «решай сам, работай без меня» → forge-autonomous asks the start question first; nothing fabricated | tool_used Skill (indicator), file_exists, llm | + |
| `ss-short-answer` | the `ss` knob gives a short, direct answer with no headings, without losing facts | regex ×2, llm | + |
| `ss-no-writes` | the `ss` knob is read only: the script goes in the reply and no file is written | file_exists, regex ×2 | + (with Write granted) |
| `nearmiss-weather` | «какая сегодня погода в Москве?» → no Forge skill, no pitch, no invented forecast | tool_used Skill max 0 (both), regex, llm | 0 |
| `nearmiss-translate` | a translation that mentions an ad campaign → no Forge skill, just the translation | tool_used Skill max 0 (both), regex ×3 | 0 |
| `nearmiss-marketer-one-off` | «Я маркетолог. Как считается ROMI?» → looks like the forge-new trigger but is a one-off question: answer directly | tool_used Skill max 0 (both), regex, llm | 0 |

Tags (filter with `--tag`):
- by kind: `trigger`, `near-miss`, `routing`, `behaviour`, `evidence`, `knob`, `language`
- one tag per skill: `forge-new`, `forge-direct`, `forge-code`, `forge-gpt`, `forge-done`, `forge-rules`, `forge-autonomous`
- `smoke`: six cheap cases for a quick pass
- `needs-write-grant`: see below

## How to run

From the repository root:

```bash
claude plugin eval plugins/forge --trust-plugin --runs 3 --max-cost-usd 10 --json results.json --no-publish
```

- `--json results.json` makes the run quiet: no progress lines and no summary table. The full result, including
  per-run grader verdicts, goes to `results.json`, and the HTML report goes to `plugins/forge/evals/results/<timestamp>/`
  (git-ignored). To watch progress and the summary table, drop `--json`.
- **Write grant.** Without `--allow-tools Write`, a run cannot create files. The "nothing is written" graders in the
  `needs-write-grant` cases then pass in both arms and show Δ 0. To make them count, run:
  ```bash
  claude plugin eval plugins/forge --allow-tools Write Edit --trust-plugin --runs 3 --max-cost-usd 10 --json results.json --no-publish
  ```
  Do not grant `Bash`. The rubrics assume no shell ("no command could run"). Bash also needs the OS sandbox,
  which means WSL2 on Windows.
- Cheaper passes:
  - `--tag smoke` runs the six smoke cases.
  - `--case hello-forge-ru --runs 1 --ablation none` runs one case once with no baseline, while you iterate on a
    grader. One run is noisy, so confirm at 3 runs.
- Pin `--model` when you compare across releases, so a model rollout does not look like a plugin regression. If a
  judge verdict looks wrong, re-run with `--judge-model sonnet` before blaming the plugin.
- The `ss` and «решай сам» cases depend on the plugin's UserPromptSubmit hook (`tools/prompt_knobs.py`), which runs
  `python`. If `python` is not on PATH where the suite runs, those cases fail with the plugin. That is a real finding.
- Cost is **not measured yet**. The suite is 16 cases × 3 runs × 2 arms = 96 agent runs, plus three judge votes per
  `llm` grader per run (14 `llm` graders, so up to 252 short judge calls). Write the first measured COST here.
  When the `--max-cost-usd` ceiling is hit, the command exits 2 with `partial: true`.

## Reading the result

- `WITH` is the score with the plugin, `W/OUT` the score without it, and `Δ = WITH − W/OUT`.
- **`--threshold` checks the WITH score, not Δ.** Its default is 1.0, and any case below it makes the command exit 1.
  Δ never changes the exit code. A suite can therefore clear the threshold while Forge adds nothing, so read the Δ
  of each case against the table above.
- `tool_used: Skill` graders that check a skill fired are plugin-fired indicators. In a two-arm run they are shown
  but not scored, because they could never pass without the plugin. "Must not fire" checks (`min: 0`, `max: 0`)
  carry `arm: both`, so they are scored in both arms.
- `skill-forge-autonomous` fails until the forge-autonomous skill ships. It is an indicator, so the score is not
  affected.
- Before trusting a low score, check `NOTES` or `cases[].arms.with[].error` for usage-limit or rate-limit errors.
  Such runs still score, usually 0, and the suite is not marked partial.
- A case that scores 1.0 with and without the plugin is measuring Claude, not Forge. A trigger or behaviour case
  that stays at Δ ≈ 0 for three cycles is a candidate for deletion in the audit (K39). Near-miss guards stay.

## Offline checks (no model calls)

- `claude plugin validate plugins/forge` checks the manifest, skills and agents.
- `claude plugin eval plugins/forge --case 'zzz-no-such-case' --ablation none --no-publish --trust-plugin` makes no
  runs. It still parses every case's `prompt.md` and grader frontmatter: unknown prompt keys, a missing `type:` and
  invalid YAML show up as `✗ … failed to load`. A clean suite prints only `No eval cases found matching …`.
  This check does not reach the per-type grader schema (`pattern`, `input_match`, `arm` and so on are strict).
  Those are validated when a case actually runs.

## Adding a case from a real lesson

The suite should grow from real failures, not from imagined ones. A release adds one case for each new class of
lesson (K39).

1. Pick a lesson in `<project>/01_PROJECT_CORE/circle/lessons/YYYY-MM-DD_Lnnn.json`, ideally one with
   `applies_to: kernel`. Its `what_failed` is the FAIL condition and its `fix` is the behaviour to expect.
2. The prompt is the owner's own words that led to the failure: the order's verbatim Request (K34a), in Russian,
   with personal data removed. The run starts in an empty folder, so put the context the case needs into the prompt.
   A case that needs project state (forge.json, orders) needs a `case.yaml` with `schema_version: "1.1"`, `name` and
   `context.scaffold_script`. The script runs only with `--scaffold`, so say so in the case's `description`.
3. Give the case one grader on the result and one on the steps. Prefer the free graders: `regex`, `tool_used`
   (`min: 0` + `max: 0` + `arm: both` for "must never happen") and `file_exists`. Use `llm` only when those cannot
   express the check, with a rubric of concrete PASS and FAIL conditions about a short reply.
4. Frontmatter: `description` names the lesson id and its evidence, `tags` include `lesson`, keep `runs: 3`, and set
   small `max_turns` and `timeout_seconds`. Unknown keys fail the load.
5. Negative control (K34): the case must fail on the plugin version before the fix, and W/OUT should usually fail
   too, then pass after the fix. Check it with `--case <name> --runs 1` against both versions. This costs money, so
   ask for the owner's word first.
6. Name the directory `lesson-Lnnn-<slug>`. Run the offline check above, then the case.

```text
evals/lesson-L012-closed-without-review/
├── prompt.md          # frontmatter (description: "L012 2026-10-05: ...", tags: [lesson, evidence], runs: 3,
│                      #   max_turns: 6, timeout_seconds: 180, allowed_tools: [Read, Glob, Grep, Skill]);
│                      #   body = the owner's words
└── graders/
    ├── fired.md       # type: tool_used, tool: Skill, input_match: '"skill"\s*:\s*"(?:[\w-]+:)?forge-done"'
    └── no-claim.md    # type: llm; body: "PASS only if ... FAIL if ..." (what_failed from the lesson)
```
