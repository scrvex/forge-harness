---
name: forge-code-structure
description: Use when writing, changing or reviewing code in a Forge project - a feature, a fix, a refactor, a script, a task for Codex or the executor agent («напиши код», «почини», «отрефактори», «куда это положить») - or when deciding where new code goes, what to name it or how far a change may reach.
---
# Code canon for Forge projects

Read by the Lead when planning and reviewing, by executors when writing, and carried in Codex's prompt. The project's own
rules (CLAUDE.md, the `core` memory, its linter configs) win over this file; this file wins over habit.

## First
- Stay inside the declared scope: change only the files the task lists. If a file outside must change, stop and answer
  `blocked` with the question and your best guess, because other tasks of the wave own the other files.
- Match the surrounding code (naming, error style, comment density, formatting, test layout) even where this canon would
  choose otherwise: one codebase in two styles costs every reader. Propose the canon change separately.
- A bug seen in passing is named (file, symbol, symptom) under "beyond the task" in your reply, and the Lead opens a new
  order for it; fixed inside the current change, it puts unreviewed behaviour outside the DoD into the diff.
- Validate input where it enters (CLI arguments, files, HTTP, environment, replies of external APIs) and trust the types
  inside: one check per entry point beats defensive code everywhere.
- Never swallow an error. Catch the specific type you can handle and let the rest propagate, since a hidden failure
  returns later as wrong data.
- Build for the current task: no interface, factory, plugin hook or settings object before a second real use exists,
  because speculative structure costs every reader and usually guesses the future wrong.
- Add no dependency the task does not name, and never install one yourself (the owner installs, K35f): each one brings
  a licence, upkeep and attack surface.
- Library and framework APIs come from Context7 or the installed source, not memory, as signatures change between versions.

## Layout and boundaries
- Draw module lines around knowledge the rest of the code should not need (a file format, a vendor API, a pricing rule),
  not around the order in which things happen, so a change to that knowledge stays in one module.
- Dependencies point inward: domain logic imports no web framework, database driver, HTTP client or CLI parser, and
  adapters at the edge import the domain. The core then runs in a test without the outside world.
- I/O stays at the edges: the core takes plain values and returns plain values, and thin code around it reads and
  writes, so the logic is tested without files or network.
- Keep a helper, constant or type beside the code that uses it and promote it to a shared module only when a second
  module needs it, because shared modules turn into dumping grounds.
- Lay out a new command, parser or endpoint the way its existing siblings are laid out, so readers find things by analogy.
- Split a file when its parts change for different reasons. Name a file after what it holds, since `utils` or `misc`
  invites unrelated code.
- A long-lived resource (connection, worker, background task) has one owner that starts it and stops it on shutdown,
  so nothing leaks or dies mid-write.

## Names and functions
- Use the domain's words and one name per concept across code, tests and docs; two names read as two things.
- Name the outcome, not the activity: `process_items` or `update2` tells a reader nothing about what changed.
- No private abbreviations; a name the reader must decode costs more than the letters it saves.
- When behaviour changes, rename in one pass (code, tests, comments, docs), so no stale name survives.
- A function is small enough that one sentence names what its test checks; splitting a short, single-purpose function
  further only adds jumps for the reader.
- Pass dependencies in (arguments, constructor) rather than reaching for globals, so a test swaps them without patching.
- Let types rule out impossible states (enums, validated constructors) instead of re-checking them in every caller.

## Errors
- Messages are actionable: what failed, with which value, what was expected, what to do. They are read without a debugger.
- Load and validate configuration when the program starts, so a wrong value stops it before any work is done.
- Retry only transient failures of external calls, with a bound and a timeout; a retried logic error just fails slower.
- `assert` belongs in tests; runtime checks raise a real error, because asserts vanish under optimisation flags.
- Users see a stable message or code; stack traces and internals go to the log, where they help and leak nothing.

## Simplicity and legacy code
- Tolerate some duplication until real uses show the common shape; an abstraction cut from one example has the wrong seams.
- Before writing a mechanism, look for it in the project, then in its existing dependencies, then in the standard
  library, since every new mechanism is one more thing to maintain.
- Make code faster only where a measurement shows it is slow; guessed bottlenecks are usually elsewhere.
- No dead code (unreachable branches, parameters nobody passes, commented-out blocks), because readers assume every
  line runs.
- Before deleting code that looks unused, look for dynamic uses (packaging entry points, names built from strings,
  fixtures), which a symbol search misses.
- Change in small reversible steps: restructure without changing behaviour, then change behaviour, each step green, so a
  bad step is reverted alone.
- Before changing untested code that matters, pin today's behaviour with a characterisation test (`forge-code-testing`).
- Code you only read and found sound stays untouched; "nothing to change" is a valid finding (K34c).

## Comments
- A comment says why: the constraint the code works around, the regulation behind a number, the reason two calls must
  run in this order. What the code does, the code already says.
- Who changed what and when belongs to git; in a comment it goes stale.
- A comment changes or goes together with the code it describes, because a wrong comment is worse than none.
- Code, comments and identifiers in English (K13); text the owner reads in the owner's language.

## Configuration and secrets
- Configuration lives in one place and is read at startup; a value with domain meaning gets a named constant, so it
  changes in one spot.
- Secrets come from the environment or the OS store and never appear in code, tests, fixtures, logs, commits or a prompt
  to a cloud model (K15), because a leaked secret cannot be recalled. Ship `.env.example` with names only and keep
  `.env` in `.gitignore`.
- Every external call has an explicit timeout, since some library defaults wait forever.

## Change rules
- Integration files (a registry, re-exports, a routes table, `package.json`, a lockfile, shared config) belong to one task
  per wave, named by the Lead, since parallel tasks proposing full texts of one file overwrite each other.
- Anything another program or saved data reads (a CLI flag, a database column, a file format, an API route, an
  environment variable name) keeps its name in a refactor; renaming it is its own order with a migration.
- When behaviour a user can see changes, the README, `--help` or changelog describing it changes in the same task, or the
  docs start lying.
- Lint, type and test configs stay as they are, so the checks keep meaning what they meant (`forge-code-testing`).

## Never
Edits outside the scope · an unrelated fix inside the current change · a swallowed exception · a secret in code or in a
prompt · a dependency the task did not name · an API written from memory · a renamed field that other programs read ·
git commands (they are the Lead's).

Ideas: ambatupipe code-structure (Apache-2.0), rewritten (K16).
