---
description: >-
  One creative artifact with no recurring process. forge-direct may fire; forge-new must not; no Forge project
  intake and no forge.json - Claude's own quality is the result (K25).
tags: [trigger, routing, forge-direct, needs-write-grant]
runs: 3
expected_outcome: >-
  Claude builds the page directly (or asks at most a couple of content questions while using placeholders).
  No interview about mode, Codex, folders or orders; no forge.json. Expected Δ about 0: the plugin must not make
  a one-shot request worse.
max_turns: 6
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

Сделай мне одностраничный сайт-визитку для фотографа Анны Соколовой: шапка с именем, галерея из 6 фото-заглушек, блок «Обо мне» и контакты (телефон и Telegram). Всё в одном html-файле.
