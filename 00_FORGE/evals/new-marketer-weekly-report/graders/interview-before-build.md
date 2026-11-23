---
type: llm
weight: 2
---
The user is a marketer who described a recurring weekly ad report built from their own CSV exports (Yandex Direct, VK Ads). The correct first move is an interview, not a build.

PASS only if all of these hold:
1. The reply is written in Russian.
2. It asks the user at least two clarifying questions about the task - for example: their role and what they already know; what the weekly report must show, who reads it and which decisions it feeds; how much time they can give and how fast results are needed; which exports exist, their columns and where they are stored; red lines (money, sending data outside, personal data). Alternatively it restates the task in its own words and explicitly waits for the user's confirmation before building.
3. It does not present a finished deliverable: no complete script, no ready report, and no claim that a project, folders or files have been created.
4. It contains no invented figures (spend, CPA, ROMI, conversions) presented as the user's real results.

FAIL if any condition is not met - in particular if the reply jumps straight to a complete solution (a full script or a ready spreadsheet recipe) without asking anything first, or claims that something was built.
