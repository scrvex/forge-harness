---
name: forge-code-testing
description: Use when writing or changing tests, when a test or check fails, when a bug needs a reproduction, when deciding what a change must test, or when a critical module needs mutation or property-based testing («напиши тесты», «покрой тестами», «тест падает», «почему красное», «чего стоят эти тесты»).
---
# Testing code in Forge

The language and runner do not matter here; test layout follows the project and `forge-code-structure`.

## First
- Test behaviour at the public boundary, and take the expected value from somewhere other than the code under test: the
  DoD, the documentation of a format, the owner's rule, a hand calculation. An expected value copied from the code only
  confirms that the code does what it does.
- A bug fix starts with its reproduction, a test that fails on the current code for the reported reason; then the fix.
  The test proves the fix and keeps it fixed.
- Never get green by weakening a check: no skip or xfail, no deleted test, no loosened assertion or widened tolerance, no
  broader `except`, no ignore comment, no edit of a lint, type or test config, because green then means nothing. When the
  contract really changed, change the test and say so in `decisions`; said aloud it is a decision, silent it is weakening.
- Triage a failure before touching any code (table below), because each cause has a different fix.
- Trust a new check only after seeing it fail on the old code, a known-bad input or a planted mutant (K34b).
- Mock only what you do not own or what is slow or non-deterministic, so the test exercises the real code.

## What to test
- Each behaviour the DoD names, with a test that fails without the change, because the DoD is the answer from outside.
- Contracts that code outside your diff relies on: output format, exit codes, error messages someone parses.
- The inputs this boundary actually receives, at their edges: empty, missing, the largest size, malformed or wrongly typed
  data from outside, Cyrillic and other non-ASCII text, dates near midnight and across time zones. Bugs gather there.
- Failure paths of what the code calls: a timeout, an error status, a dropped connection, a partial reply. Nobody
  exercises them by hand, so nobody notices when they break.
- Rules that hold for every input: see property-based testing below.

## What not to test, and what checks it instead
- Internals (private functions, which method called which): a refactor that keeps behaviour would break those tests.
- Code with no logic of its own, such as a call passed straight through, a restated constant or a library doing its own
  job; the type checker or the vendor already covers it.
- Exact wording of prompts and messages, which only proves the string equals itself; assert on the structure the program
  relies on instead.
- Not covered by a unit test still means verified another way, named in the DoD: the domain's verify-by-running line (run
  it on a real input, open the page), or a separate measurement where speed or the quality of model output matters.

## The mock boundary
- Replace the network, paid or external APIs, the clock, randomness and processes you do not own. Prefer a temporary
  directory to a mocked filesystem and a real in-memory database to a mocked query, because they behave like the real thing.
- Mocking your own domain to test your own domain makes the test check the mock.
- Inject failures the dependency can really produce, so the test covers a state production can reach.
- Record one real reply from an external service (source and date noted) and test your parsing against it; a hand-written
  reply only encodes your assumptions.
- A test that needs many mocks or a long setup points at code with too many dependencies; say so in the reply.

## Test data
- Small and explicit, built inside the test or by a named factory, so the reader sees why the expected value is right.
- Deterministic (fixed seed, frozen clock, sorted output where order is not the contract, no reliance on test order or
  shared state), so a failure reproduces.
- Synthetic values in the real shape; no personal data or secrets in fixtures (K15), because fixtures land in git and in
  prompts.
- Each test cleans up what it creates (temporary paths, environment variables), so the next one starts clean.

## Failure triage
Work down the table, since the cheaper causes come first.

| cause | sign | action |
|---|---|---|
| environment | missing tool, path or permission | report it; `check_code.py` lists what it skipped |
| flake | same code, different results | remove the non-determinism (time, order, randomness, network, shared state, threads) |
| mirror test | expected value copied from the code | delete it and say why |
| outdated contract | the order or the owner changed the requirement on purpose | update the test, name the changed assertion in `decisions` |
| real bug | reproducible, and the test states the current requirement | fix the code |

A retry that turns a flake green hides a race that fires later, so it is never the fix.

## Mutation testing, for critical modules
Use it on money, security, data integrity, parsers and gates, or wherever coverage is high but trust is low.
- Run the tool on the changed module only (Python `mutmut`, JS/TS Stryker, Rust `cargo-mutants`); a whole-repo run is slow
  and its noise hides the signal.
- Each surviving mutant is a missing test (write one that kills it), an equivalent mutant (same behaviour, mark it) or
  dead code (delete it).
- A model can propose mutants aimed at the real risk (an off-by-one at a limit, a flipped condition, a dropped error
  branch) and flag likely equivalents. Keep a generated test only if it fails on the mutant and passes on the real code.
- The score is a signal, not a target: chasing the last mutants buys brittle tests.

## Property-based testing
Use Hypothesis (Python) or fast-check (JS/TS) where a rule holds for every input, not just for a few examples.
- Look for: round trip (decoding an encoded value returns it), idempotence (applying twice equals once), preserved
  invariants (output is an ordered permutation of the input, totals unchanged), independence from input order, agreement
  with a slow but obvious reference implementation, no crash on any valid input.
- Derive properties from DoD lines, so the test checks the requirement rather than the code: "every input row appears in
  the report" is a property, not an example.
- A shrunk counterexample means the code, the spec or the property is wrong; decide which. A spec gap goes back to the
  Lead as a DoD question, not a silent choice. Pin the counterexample as an explicit example.
- Keep runs reproducible in CI (a fixed seed or a derandomised profile) so a red run can be replayed.

## How it fits Forge
- Tests belong to the task that changes the behaviour, inside its scope, so code and test are reviewed together.
- `gates/check_code.py --order N` (or `--files`) runs syntax, ruff, pyright, tsc, eslint and forge.json `code.test`. It is
  green only if something ran, and it lists what it skipped. Skipping tests needs the owner's waiver («без тестов»),
  recorded in the order.
- Keep `code.test` fast, because it runs on every order; mutation runs and long property runs are separate commands
  named in the DoD.
- Codex in patch mode cannot write: it runs read-only tests and lists them in `checks_run`, and the Lead runs the full
  checks after applying. A red check goes back to the writer (`codex_exec.py fix` or the executor), not to a hiding patch.

## Never
Retrying until green · weakening a check · mocking your own domain · tests that call paid services or the owner's
accounts · personal data or secrets in fixtures · trusting a check never seen failing.

Ideas: ambatupipe code-testing (Apache-2.0), rewritten (K16).
