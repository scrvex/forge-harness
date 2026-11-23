---
type: regex
target: trace
pattern: 'timedelta\(\s*(?:days\s*=\s*)?d\.weekday\(\)\s*\)'
---
The correct fix `timedelta(days=d.weekday())` appears in the session (reply or a written file). The buggy line from the prompt (`d.weekday() + 1`) does not match.
