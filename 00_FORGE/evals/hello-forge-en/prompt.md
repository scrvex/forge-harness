---
description: >-
  English greeting to Forge. forge-new fires; the HELLO content must come back in the user's language (K13),
  not as the Russian template pasted verbatim.
tags: [trigger, forge-new, behaviour, language]
runs: 3
expected_outcome: >-
  forge-new fires. The reply is in English, explains what Forge does (interview, workspace, checked orders,
  automation, lessons) and asks starter questions about the user's shop.
max_turns: 6
timeout_seconds: 150
allowed_tools: [Read, Glob, Grep, Skill]
---

Hi Forge - what can you do for me? I run a small online shop.
