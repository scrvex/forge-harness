---
description: >-
  The `ss` knob is read-only (K36): the script belongs in the reply and no file is written. Meaningful only when
  the run is granted Write (--allow-tools Write); without the grant both arms pass trivially.
tags: [knob, behaviour, needs-write-grant]
runs: 3
expected_outcome: >-
  The script is shown inline (pandas or the csv module) with a line on how to run it; no file is created.
max_turns: 4
timeout_seconds: 120
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

ss набросай скрипт на Python, который склеивает все csv из папки exports в один файл all.csv
