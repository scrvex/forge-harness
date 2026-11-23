# Tests — how they are named

498 tests, standard-library `unittest` only, no third-party packages.

```bash
python3 -m unittest discover -s 00_FORGE/tests
```
Expected: `Ran 498 tests … OK` with 1–2 skipped depending on the OS (~2 min). The same command runs in CI on every push.

**Naming.** A `test_<version>.py` file holds the tests added for that release of the plugin, so the number is a
version, not a ticket id. Each check is written both ways: the case it must catch *and* the case it must let pass.

| File | Covers |
|---|---|
| `test_kernel.py`, `test_skills_map.py`, `test_swarm.py` | kernel, the skills map (domains → pipeline), the swarm/acceptance cycle |
| `test_092.py` | 0.9.2 — evidence discipline, upgrade and replay guards |
| `test_093.py`, `test_093_transcripts.py` | 0.9.3 — the per-order token meter that reads Claude Code transcripts |
| `test_100.py`, `test_101.py`, `test_101_text.py` | 1.0.0–1.0.1 — limit-aware delegation (Codex vs a Claude agent), no default executor |
| `test_110.py` | 1.1.0 — fixes from the 2026-10-13 review (frontmatter, silent hooks, cut-off runs) |
| `test_112.py` | 1.1.2 — Codex runs never break the owner's desktop app |
| `test_120_*.py` | 1.2.0 — reconcile vs git, formal verdict reading, weakened-check scan, gate docs, hook fixes, live machine load |

The full version history is in [`../CHANGELOG.md`](../CHANGELOG.md).
