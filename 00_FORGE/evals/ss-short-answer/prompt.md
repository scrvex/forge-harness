---
description: >-
  The `ss` knob (K36, prompt_knobs hook): a short read-only answer - no padding, but no facts or risks lost.
  Without the plugin `ss` means nothing, so the plain answer is usually long.
tags: [knob, behaviour, smoke]
runs: 3
expected_outcome: >-
  A few sentences in Russian: CPA is the cost of a target action (order), CPC the cost of a click; for a shop
  CPA against margin matters more; the main caveat kept; no headings, no long lists.
max_turns: 3
timeout_seconds: 90
allowed_tools: [Read, Glob, Grep, Skill]
---

ss чем CPA отличается от CPC и на что интернет-магазину смотреть в первую очередь?
