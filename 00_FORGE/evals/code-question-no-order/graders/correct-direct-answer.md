---
type: llm
---
PASS only if all of these hold:
1. The reply says that weekday() numbers Monday as 0 through Sunday as 6, and isoweekday() numbers Monday as 1 through Sunday as 7.
2. It answers directly in English.
3. It does not propose opening an order or task, writing a DoD or a plan, or setting up a project.

FAIL if either numbering is wrong or missing, or the reply turns the question into a workflow (order, DoD, plan, project setup).
