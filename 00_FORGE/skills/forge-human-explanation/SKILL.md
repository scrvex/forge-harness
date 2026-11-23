---
name: forge-human-explanation
description: Use when answering a person in plain language - the owner or a user of a Forge project - about a result, a report, a choice to make, why something failed or how it works («объясни», «простыми словами», «что получилось», «что мне делать», «напиши отчёт»), or when writing or proofreading a document or page a person will read.
---
# Explaining to a person

Assume the reader was not in the session: they have this message and whatever it links, nothing more. Write for that
reader, then proofread with the lists below.

## First
- Pick the form before writing:
  - chat, for a question, a short result or a choice to make now;
  - a document, for anything the reader keeps, forwards or re-reads: `.md` in the user folder plus a PDF from `md2pdf.py`,
    opened once to check every page and the Cyrillic (K13);
  - an HTML page, when the answer has structure a page shows better than prose (a comparison table, a diagram, steps with
    states, numbers over time). Load `artifact-design` first, and `dataviz` for charts. The chat message still carries
    the main conclusion, the decision asked of the reader and the link.
- Lead with the answer, then why, then what the reader does next, so a reader who stops after the first paragraph still
  has what they need.
- Write in the reader's language (Russian for the owner) and in their words rather than the project's. A code name, if
  needed, goes in brackets after the plain description.
- Give every number its source and period (K32) and a reference point (last month, the target, the other option), so
  the reader can tell whether it is good or bad.
- Name what is unknown, what would change the answer and which data would help (K22), and mark assumptions as such: a
  confident answer over a gap misleads more than an honest gap.
- Point only at what the reader can open. A task id, an order number or "the earlier run" means nothing without the
  thing itself.

## Shape
- Headings and captions state the conclusion ("Codex costs less for this order"), not the topic ("Costs"), so a skim
  already gives the result.
- Lists hold items of the same kind. An argument (a cause, a condition, a step that depends on the one before) is
  written in sentences, because bullets drop the connecting words.
- Keep detail at the level where the reader decides; the rest goes to an appendix or a folded block.
- When the reader must decide, say what is being decided, which option you recommend and why, and what the other option
  costs.
- Length follows the reader and the subject: a sentence stays only if it changes what the reader knows or does.

## AI-writing habits to remove
Each one makes the text longer and less trusted.
- Opening filler: praise of the question, "Sure!", «Отличный вопрос». Start with the answer.
- Announcing a point instead of making it: "it's important to note", "it's worth mentioning".
- Signposting: "in this section we will look at", "let's dive in". Headings do that job.
- Rule-of-three padding: a third item or adjective added for rhythm rather than truth.
- Hedging stacks: "might potentially be possible". One qualifier with its reason, or none.
- Intensifiers: "truly", "extremely", "absolutely", "crucial". Without them the claim reads firmer.
- Dash chains: asides bolted onto one sentence, dash after dash. Split into sentences; in Russian the dash in «X — это Y»
  is grammar and stays.
- Staged reversals: "this isn't X, it's Y" for drama. State Y.
- Bold everywhere, or a bold label opening every bullet, until nothing stands out. Bold only what must not be missed.
- Fake enthusiasm: "Great news!", emoji, «Надеюсь, это поможет!». Keep the tone level.
- A closing summary that repeats the text. End with the next step, or just end.
- Unexplained jargon and internal names: "gate L010 is red" becomes what failed, in the reader's words.
- Passive voice hiding the actor: "it was decided", "it is recommended". Say who.
- Invented specificity: exact-looking figures, dates, names or quotes with no source. Give the source or drop the claim,
  and label an estimate as an estimate.
- Empty significance: "a key role", "a game-changer", "far-reaching consequences". Name the consequence.
- Universal claims from a few cases: "always", "everyone" after three runs. Give the real count.
- Status as mood: "all looks good". Say what was checked and what it showed.

## Proofread before sending
- Does the first sentence answer the question?
- Could the reader act without a follow-up? Answer the question they would ask next.
- Cold read: imagine someone who missed the whole session; every term or reference they would stop on gets explained or
  replaced.
- Numbers carry source, period and a reference point; assumptions and unknowns are named.
- The language is the reader's; terms they need are defined once, terms they do not need are gone.
- One pass over the habits list, and every hit is rewritten.
- A failure or a skipped check is said plainly, not softened into "mostly works".
- A document's PDF was opened and every page looked at; a page's conclusion and decision are also in the chat.

## Never
Invented figures or quotes · English to the owner · a softened failure · a page for a one-line answer · a document for
third parties sent without the owner's explicit ok (K8) · pointing the reader at something they cannot open.

Ideas: ambatupipe human-explanation (Apache-2.0; its cliché catalogue comes from stop-slop, MIT), rewritten (K16).
