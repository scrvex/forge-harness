"""1.2.0 corpus consistency: the skills and agents are well formed and every reference inside them resolves.

Frontmatter (name == folder / file name, description present), `forge:<name>` / `forge-<name>` references, relative
markdown links, plugin file paths (`<plugin>/tools/x.py` anywhere in the text, code blocks included, and `tools/x.py` in
backticks), bare `x.py` file names, K-rule ids that exist in CONSTITUTION.md. Skills AND agents are checked. A failure lists
the exact broken items. Every check is a function over a text, and the last class feeds each of them a broken text to prove it
fails when it should (a check that was never seen red proves nothing).
Principle: the versioned file is primary, one fact - one place (ambatupipe skill-references test)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SKILLS = sorted(PLUGIN.glob("skills/*/SKILL.md"))
AGENTS = sorted(PLUGIN.glob("agents/*.md"))
DOCS = SKILLS + AGENTS
SKILL_NAMES = {p.parent.name for p in SKILLS}
AGENT_NAMES = {p.stem for p in AGENTS}
WORKFLOWS = {p.stem for p in PLUGIN.glob("workflows/*.js")}
# names with the forge prefix that are not skills or agents: the progress-band mod, the dev marketplace
NOT_A_SKILL = {"band", "dev"}
PATH_DIRS = ("tools", "gates", "hooks", "templates", "workflows", "agents", "skills", "evals", "tests")
BARE_HOMES = ("tools", "gates", "hooks", "workflows")

# `<plugin>/tools/x.py` is unambiguous: it is checked wherever it stands. An unprefixed `tools/x.py` is checked only as a
# backticked span (in a code block it may be an example from the user's project, e.g. ["src/a.py", "tests/test_a.py"]).
PREFIXED_PATH = re.compile(r"<plugin>/((?:%s)/[^\s`'\")\]},;|:]*)" % "|".join(PATH_DIRS))
SPAN_PATH = re.compile(r"`((?:%s)/[^\s`]*)(?=[\s`])" % "|".join(PATH_DIRS))
BARE_FILE = re.compile(r"`([A-Za-z0-9_-]+\.(?:py|js))`")
SKILL_REF = re.compile(r"\bforge([:-])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def rel(p: Path) -> str:
    return p.relative_to(PLUGIN).as_posix()


def frontmatter(text: str) -> dict | None:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    out = {}
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and re.fullmatch(r"[A-Za-z_-]+", k.strip()) and not ln.startswith(" "):
            out[k.strip()] = v.strip()
    return out


def rule_ids() -> set[int]:
    text = (PLUGIN / "CONSTITUTION.md").read_text(encoding="utf-8")
    return {int(x) for x in re.findall(r"\*\*K(\d+)\b", text)}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ---- the checks: each takes a text and returns the broken items (empty = fine) ----

def broken_frontmatter(text: str, expected_name: str, where: str) -> list[str]:
    fm = frontmatter(text)
    if fm is None:
        return [f"{where}: no frontmatter"]
    bad = []
    if not fm.get("name"):
        bad.append(f"{where}: no name")
    elif fm["name"] != expected_name:
        bad.append(f"{where}: name {fm['name']!r}, expected {expected_name!r}")
    if not fm.get("description"):
        bad.append(f"{where}: no description")
    return bad


def broken_skill_refs(text: str, where: str, skills: set[str] = SKILL_NAMES, agents: set[str] = AGENT_NAMES,
                      workflows: set[str] = WORKFLOWS) -> list[str]:
    qualified = skills | agents | workflows  # forge:<name> (plugin namespace; a skill's own name is forge-<x>)
    bad = []
    for m in SKILL_REF.finditer(text):
        name = m.group(2)
        if m.group(1) == ":":
            ok = name in qualified
        else:
            ok = name in NOT_A_SKILL or f"forge-{name}" in skills
            after = text[m.end():m.end() + 2]
            # a file or path that merely starts with forge-: `forge-x_y`, `forge-x.json`, `forge-x/ ...`;
            # a sentence-final dot is NOT such a case (`... run forge-reveiw.` is a typo)
            if after[:1] == "_" or (after[:1] == "." and after[1:2].isalnum()) or after == "/ ":
                ok = True
        if not ok:
            bad.append(f"{where}: {m.group(0)!r} is not a skill, agent or workflow")
    return sorted(set(bad))


def broken_links(text: str, base: Path, where: str) -> list[str]:
    bad = []
    for m in MD_LINK.finditer(strip_code_blocks(text)):
        target = m.group(1).split("#", 1)[0]
        if not target or re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        if not (base / target).exists():
            bad.append(f"{where}: link {m.group(1)!r} does not resolve")
    return bad


def _is_pattern(path: str) -> bool:
    """A placeholder, glob or folder (`evals/<case>/`, `tools/*.py`, `skills/`), not a file to look up."""
    return bool(re.search(r"[<>*{}$%\[]", path)) or path.endswith("/") or "..." in path


def broken_paths(text: str, where: str, plugin: Path = PLUGIN) -> list[str]:
    bad = []
    found = [m.group(1) for m in PREFIXED_PATH.finditer(text)] + [m.group(1) for m in SPAN_PATH.finditer(text)]
    for raw in found:
        path = raw.rstrip(".,;:")  # sentence punctuation
        if _is_pattern(path) or not path:
            continue
        if not (plugin / path).exists():
            bad.append(f"{where}: path {path!r} does not exist")
    for m in BARE_FILE.finditer(text):
        name = m.group(1)
        if not any((plugin / d / name).is_file() for d in BARE_HOMES):
            bad.append(f"{where}: file {name!r} is in no tools/, gates/, hooks/ or workflows/")
    return sorted(set(bad))


def broken_rule_ids(text: str, ids: set[int], where: str) -> list[str]:
    return sorted({f"{where}: K{m.group(1)} is not in CONSTITUTION.md" for m in re.finditer(r"\bK(\d+)\b", text)
                   if int(m.group(1)) not in ids})


def fail_list(bad: list[str]) -> str:
    return "\n" + "\n".join(bad)


class TestCorpus(unittest.TestCase):
    def test_corpus_found(self):
        self.assertGreaterEqual(len(SKILLS), 10)
        self.assertGreaterEqual(len(AGENTS), 4)

    def test_frontmatter(self):
        bad = []
        for p in DOCS:
            bad += broken_frontmatter(read(p), p.parent.name if p.name == "SKILL.md" else p.stem, rel(p))
        self.assertEqual(bad, [], fail_list(bad))

    def test_skill_and_agent_references_exist(self):
        bad = []
        for p in DOCS:
            bad += broken_skill_refs(read(p), rel(p))
        self.assertEqual(sorted(set(bad)), [], fail_list(sorted(set(bad))))

    def test_relative_markdown_links_resolve(self):
        bad = []
        for p in DOCS:
            bad += broken_links(read(p), p.parent, rel(p))
        self.assertEqual(bad, [], fail_list(bad))

    def test_file_paths_in_skills_and_agents_resolve(self):
        bad = []
        for p in DOCS:
            bad += broken_paths(read(p), rel(p))
        self.assertEqual(sorted(set(bad)), [], fail_list(sorted(set(bad))))

    def test_path_check_sees_the_main_style_of_reference(self):
        # `python <plugin>/tools/x.py ...` is how the skills name their scripts: the check must have material to work on
        n = sum(len(PREFIXED_PATH.findall(read(p))) for p in DOCS)
        self.assertGreaterEqual(n, 30)
        self.assertTrue(any(PREFIXED_PATH.search(read(p)) for p in AGENTS), "no agent names a plugin path: is the agent check idle?")

    def test_rule_ids_exist(self):
        ids = rule_ids()
        self.assertTrue(ids)
        bad = []
        for p in DOCS:
            bad += broken_rule_ids(read(p), ids, rel(p))
        self.assertEqual(sorted(set(bad)), [], fail_list(sorted(set(bad))))


class TestChecksFailOnBrokenText(unittest.TestCase):
    """Negative controls: each check, given a document with exactly one kind of breakage, reports it; given the correct text, not."""

    def test_frontmatter(self):
        good = "---\nname: forge-x\ndescription: d\n---\nbody\n"
        self.assertEqual(broken_frontmatter(good, "forge-x", "w"), [])
        self.assertTrue(broken_frontmatter(good, "forge-y", "w"))
        self.assertTrue(broken_frontmatter("no frontmatter\n", "forge-x", "w"))
        self.assertTrue(broken_frontmatter("---\nname: forge-x\n---\nbody\n", "forge-x", "w"))

    def test_typo_in_a_path_with_the_plugin_prefix(self):
        self.assertEqual(broken_paths("Run `python <plugin>/tools/order.py done N001` now.", "w"), [])
        bad = broken_paths("Run `python <plugin>/tools/oder.py done N001` now.", "w")
        self.assertEqual(len(bad), 1)
        self.assertIn("tools/oder.py", bad[0])

    def test_typo_inside_a_code_block(self):
        self.assertTrue(broken_paths("```\npython <plugin>/gates/run_gatez.py\n```\n", "w"))
        self.assertEqual(broken_paths("```\npython <plugin>/gates/run_gates.py\n```\n", "w"), [])

    def test_typo_in_a_backticked_path_and_before_punctuation(self):
        self.assertTrue(broken_paths("See `tools/reconcil.py`.", "w"))
        self.assertTrue(broken_paths("Use (<plugin>/tools/oder.py).", "w"))
        self.assertTrue(broken_paths("Use <plugin>/tools/oder.py.", "w"))
        self.assertEqual(broken_paths("Use <plugin>/tools/order.py.", "w"), [])

    def test_missing_skill_file_path(self):
        self.assertTrue(broken_paths("read `<plugin>/skills/forge-nope/SKILL.md`", "w"))
        self.assertEqual(broken_paths("read `<plugin>/skills/forge-done/SKILL.md`", "w"), [])

    def test_placeholders_globs_and_folders_are_not_looked_up(self):
        for text in ("`<plugin>/evals/<case>/`", "`tools/*.py`", "`skills/forge-*/`", "`evals/`", "<plugin>/templates/x.{md,json}"):
            with self.subTest(text=text):
                self.assertEqual(broken_paths(text, "w"), [])

    def test_unprefixed_path_outside_backticks_is_a_project_example(self):
        self.assertEqual(broken_paths('files: ["src/a.py", "tests/test_a.py"]', "w"), [])

    def test_bare_file_name(self):
        self.assertEqual(broken_paths("`order.py` does it", "w"), [])
        self.assertTrue(broken_paths("`oder.py` does it", "w"))

    def test_skill_references(self):
        self.assertEqual(broken_skill_refs("Before closing run forge-review.", "w"), [])
        self.assertEqual(broken_skill_refs("see forge:forge-review and forge-done", "w"), [])
        self.assertTrue(broken_skill_refs("Before closing run forge-reveiw.", "w"))          # a typo at the end of a sentence
        self.assertEqual(len(broken_skill_refs("see forge-reveiw, then forge-nope", "w")), 2)
        self.assertTrue(broken_skill_refs("call forge:forge-nope", "w"))
        self.assertEqual(broken_skill_refs("the file forge-band.json and forge-band_x", "w"), [])  # files that start with forge-

    def test_markdown_links(self):
        base = PLUGIN / "skills" / "forge-done"
        self.assertEqual(broken_links("[ok](SKILL.md)", base, "w"), [])
        self.assertTrue(broken_links("[gone](nothing-here.md)", base, "w"))
        self.assertEqual(broken_links("[web](https://example.com/x)", base, "w"), [])
        self.assertEqual(broken_links("```\n[gone](nothing-here.md)\n```", base, "w"), [])

    def test_rule_ids(self):
        ids = rule_ids()
        self.assertEqual(broken_rule_ids("see K1 and K8", ids, "w"), [])
        self.assertEqual(broken_rule_ids("see K9999", ids, "w"), ["w: K9999 is not in CONSTITUTION.md"])


if __name__ == "__main__":
    unittest.main()
