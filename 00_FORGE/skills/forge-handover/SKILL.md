---
name: forge-handover
description: Use right after the first orders of a new Forge project are closed and its guide is filled, to tell the owner how to continue or detach the project.
---
# Handover

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief).

Send `<plugin>/templates/HANDOVER.ru.md` (fill the project name and mode), then one line each: where the results
are, which automation phrases exist (the guide's table), what data is still missing (K22), and what to improve next.
Mark the "Сборка проекта" phase done in `<state>/project.json`. The project is autonomous: the folder can be moved or
put in its own repository — the plugin stays installed in Claude Code / Cowork and applies to any folder with `forge.json`.
