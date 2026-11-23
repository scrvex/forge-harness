# Definition of Done — checklist for writing a DoD (the single owner approval point, K19)

A good DoD line is checkable by a script, a reviewer or the owner in under a minute.
- [ ] Output artifact named with its path
- [ ] Gates green (`run_gates.py`)
- [ ] Verifier rung stated (0 / 1 / 2) and reached
- [ ] Scope respected (no files outside the order's Scope changed — `git status` shows only them)
- [ ] For owner-facing results: RU summary ≤ 10 lines in the report
- [ ] For code: runs with the documented command; for docs: ≤ max_kb, EN, ASCII file name
- [ ] Lessons recorded if any gate went red or a reviewer found something
