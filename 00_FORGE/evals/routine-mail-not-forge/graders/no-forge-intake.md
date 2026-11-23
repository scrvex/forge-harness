---
type: llm
weight: 2
---
The user wants a daily morning digest of unread Gmail messages. This is routine mail work that standard tools already cover (a Cowork plugin or the Gmail connector with a scheduled task, Gmail's own features, an existing automation service). The method under test (Forge) is meant only for complex recurring work with the person's own data and rules.

PASS only if all of these hold:
1. The reply recommends a ready-made route as the main path - a Cowork plugin or Gmail connector with a scheduled run, or another standard automation tool.
2. It does not start a Forge project intake: no questions about a cheap/expensive mode, Codex or ChatGPT permission, project folders, orders (наряды) or a weekly time budget, and no claim that a project or workspace is being built.
3. The reply is in Russian.

FAIL if it proposes building a Forge workspace or orders for this, or if its main answer is a custom script the user would have to maintain, with no ready-made tool named.
