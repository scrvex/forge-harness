---
name: forge-instruction-writing
description: Use when writing or editing text a model will execute - an order, DoD lines, a task or prompt for Codex, a skill, an agent file, CLAUDE.md or AGENTS.md, a tool description - or when an executor keeps misreading an instruction («напиши промпт», «поправь скилл», «перепиши инструкцию», «почему агент делает не то»).
---
# Writing instructions a model executes

Covers every text whose reader is a model: orders and DoD, Codex tasks, skills, agent files, CLAUDE.md, AGENTS.md, tool
descriptions and tool messages. Text a person reads goes by `forge-human-explanation`.

## The rules that matter most
- Name the reader first: the Lead, the `executor` agent, Codex in patch mode, a local model. Its model, effort and what
  else it sees decide how much the text has to carry.
- A rule exists only where its reader reads it. Codex sees its prompt and nothing else of Forge (order sections, its task,
  the project's rules files); a sub-agent sees its agent file and the brief, not this session. A rule kept anywhere else
  is followed by nobody.
- Describe the result and how it will be checked, and let the model find the way: a model given a script keeps following
  it after it stops fitting. Keep numbered steps for fragile sequences, where the wrong order breaks something (checks
  before a commit, a backup before a migration), and for checklists that must be walked in full.
- Put the reason beside each rule, in one clause, because the reason is what lets the model judge a case the rule did not
  foresee; a rule without one is applied word for word, including where it makes no sense.
- Write rules in a plain, calm voice. Recent models follow instructions closely: capitals, alarm words and stacked
  "critical" push them to apply a rule beyond its scope, and the prompt's style leaks into the output. State a hard
  requirement as a plain "do X", since "try to" invites partial compliance. Thinking depth is the agent's `effort`
  setting, not a sentence asking it to think hard.
- No output quotas. "At most five findings" or "under ten lines" works as a filter: the model drops real findings to fit.
  Say who reads the result and what they decide with it; the length follows.
- Treat every example as a template the model will imitate, down to its length and wording. Vary them when you give
  several, label them as illustrations, and use only true facts: an invented figure in an example teaches that invented
  figures are fine.
- Say when to report and when to call a tool, as conditions. "Keep updates minimal" or "avoid tool calls" make a model
  go quiet and guess; "tell the owner when a check turns red or a decision is theirs" and "query Context7 for every
  library API the code calls" produce the behaviour.

## Orders, DoD and executor tasks
- A DoD line is an observable fact: a command and its expected output, a behaviour on a named input, a file with a named
  property. "Clean code" belongs in the quality bar for a reviewer, since no script can check it.
- A task for Codex or the `executor` carries what to change, the acceptance, the scope files and the read-only context,
  because that is all it gets; "as discussed" points at nothing it can open.
- Name what is deliberately left alone (K34a), or the executor fills the silence with its own guesses.
- Tell the executor what to do when the text reads two ways: answer `blocked` with the question and its best guess (K34e);
  otherwise it picks a reading and hides the choice inside the code.
- Name who acts on each rule (Lead, executor, reviewer, owner) and which rule wins a conflict; an ownerless rule is done
  twice or not at all.
- State a fact rather than ordering a lookup: "settings live in `config/`" is read once, "find where settings live" is
  work repeated on every run.
- Ask for the wanted action rather than banning the unwanted one, because a ban leaves open what to do instead; keep a
  ban only where no alternative action exists, with its reason.
- Use one word for one thing across order, plan and skills (order, task, wave, scope): a synonym reads as a second thing.
- Skip what the model already does well (general virtues, language idioms): such lines cost context and get applied where
  they do not fit. Keep what only you know: the owner's decisions, where files live, the quality bar, interfaces between
  tasks, why a limit exists.
- Keep each fact in one file and link to it, because two copies diverge at the first edit. When the reader cannot follow
  a link (Codex reads only its prompt), carry the text and note in the original that a copy exists, so both change together.

## Skills and agent files
- `description` says when to load: triggers, the owner's own phrasings, what the skill is not for. It sits in context
  every turn, and a description that summarises the process tempts the model to follow the summary and skip the body.
- Put the most important rules first, because after compaction only the head of a skill comes back. Details go lower or
  into a file read on demand (progressive disclosure).
- An agent file is the agent's whole brief, since the agent sees nothing else: model, effort, tools, scope, and the reply
  the Lead needs, which is what the diff does not show (the Lead reads the diff itself).
- Tool descriptions and tool messages are instructions too: describe a tool so a model picks it correctly without trying
  it, and make an error message say what to do next.
- Edit a rule in place instead of stacking an addendum under it, because two rules on one subject compete. After a
  rewrite, compare old and new and say what was removed on purpose. History (dates, incidents, measurements) goes to the
  lesson or changelog; the reason stays in the rule.

## Test it before trusting it
An instruction is code for a model, and untested code is a guess.
1. Baseline: run a realistic task without the new text (a fresh sub-agent or `claude -p`) and record how the model goes
   wrong, in its own words, including the excuses it gives for skipping a step.
2. Write counters for those failures only; each rule's reason names the failure it prevents.
3. Re-run the same task with the text: the recorded failures are gone and nothing new broke; a new excuse gets a counter.
   One run is noise, so compare paired runs on the same task.
4. For a plugin skill keep the cases in `evals/<case>/` (`case.yaml`, or `prompt.md` plus `graders/*.md`) and run
   `claude plugin eval <plugin>`: it adds a no-plugin baseline arm and reports the delta. No gain over the baseline means
   cost without effect.
5. Record the commands and what they showed as evidence, with the expected result written before the run (K34c).

## Prune
- Keep a rule only while a failure needs it. When the current model passes the baseline without it, delete it: old
  counters make newer models over-correct, and every line is paid for on every load.
- A rule that fires where it should not is a defect to fix, like a failing test, not a reason to add a second rule.

## Checklist before shipping
- The reader is named and the text sits where that reader looks.
- Each rule has an observable condition, a result with its check, a reason and an owner.
- Steps only for order-sensitive work; no output quotas; no capitals, alarm words or hedges on hard requirements.
- Examples are several, varied, labelled, and free of invented facts.
- Reporting and tool use are written as conditions.
- `description` says only when to load; the head of the body stands on its own.
- No restated defaults; every environment fact kept; each fact in one place.
- Baseline and re-run done, and the observed failure is gone.
- Terms match the ones the project already uses.

## Never
Shouting or stacked emphasis · a quota that filters findings · an invented fact in an example · a rule placed where its
reader never looks · a skill shipped without a run against a baseline · a counter kept after its failure is gone.

Ideas: ambatupipe instruction-writing (Apache-2.0), rewritten (K16).
