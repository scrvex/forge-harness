---
type: llm
weight: 2
---
The user asked for one single-page business-card website for a photographer, in one HTML file. This is a one-shot artifact: the right behaviour is to build it directly, the way a skilled designer-developer would.

PASS only if all of these hold:
1. The reply either delivers the page (names the HTML file it wrote, or contains the HTML) or proceeds to build it with sensible placeholders, asking at most two short content questions (for example photos, colours, the phone number).
2. It does not run a project intake: no questions about a cheap/expensive mode, Codex or ChatGPT permission, a weekly time budget, project folders, orders (наряды), approving a DoD, or "building a workspace".
3. The text addressed to the user is in Russian.

FAIL if the reply proposes setting up a Forge project or workspace for this page, or withholds the page until a multi-question interview is answered.
