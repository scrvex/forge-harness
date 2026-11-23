"""1.2.0 tests (N031, ambatupipe principles): a verdict is read formally (first line of the review file, first header line of
the report), a review must be newer than the declared files (a declared folder counts through its files, and nothing declared
still means "newer than the take"), an owner confirmation carries the owner's words, and a commit refuses files staged outside
the order's scope unless --allow-extra names them. Each guard is tested both ways: what it must stop and what it must leave alone.
Windows habits (BOM, CRLF, UTF-16, backslashes, case) are given as data, not through os.name."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import TOOLS, Project, run  # noqa: E402


def git(p: Project, *a: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=cwd or p.dir, capture_output=True, text=True, encoding="utf-8", errors="replace")


def journal_text(p: Project) -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in (p.circle / "journal").glob("*.md"))


def touch(path: Path, delta: float) -> None:
    t = time.time() + delta
    os.utime(path, (t, t))


def case_insensitive_fs(folder: Path) -> bool:
    (folder / "CaseProbe.tmp").write_text("x", encoding="utf-8")
    try:
        return (folder / "caseprobe.tmp").exists()
    finally:
        (folder / "CaseProbe.tmp").unlink()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def fresh_project(self) -> None:
        self.p.cleanup()
        self.p = Project()

    def answered(self, files: bool = True, spec: str = "out.md", important: bool = False) -> None:
        p = self.p
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x", *(["--important"] if important else [])); p.order("take", "N001")
        if files:
            (p.dir / "out.md").write_text("# Doc\n\nbody of the deliverable\n", encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", *(["--files", spec] if files else []))

    def lead_report(self) -> None:
        self.p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")

    def v2(self, text: str | bytes) -> Path:
        f = self.p.circle / "answers" / "N001_review_v2.md"
        f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))  # exact bytes: no newline translation, a BOM stays
        return f


class FormalVerdict(Base):
    def test_verdict_inside_the_body_is_not_a_verdict(self) -> None:
        self.answered(); self.lead_report()
        self.v2("# Review of N001\n\nThe reviewer thinks:\nVERDICT: APPROVE\n")  # the line is there, but not first
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("second actor", r.stderr)

    def test_first_line_verdict_closes(self) -> None:  # negative control
        self.answered(); self.lead_report()
        self.v2("\nVERDICT: APPROVE\n\n| DoD | met |\n")  # a leading blank line is fine
        self.p.order("close", "N001")

    def test_changes_in_the_first_line_still_blocks(self) -> None:
        self.answered(); self.lead_report()
        self.v2("VERDICT: CHANGES\n\nVERDICT: APPROVE\n")  # the first line decides, a later APPROVE does not rescue it
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("K31", r.stderr)

    def test_a_tail_after_the_token_is_not_the_verdict(self) -> None:
        self.answered(); self.lead_report()
        self.v2("VERDICT: APPROVE, but CHANGES are needed in 3 places\n")  # verifier2.md: output exactly `VERDICT: <token>`
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("second actor", r.stderr)
        self.v2("VERDICT: APPROVE.\n")  # negative control: a final full stop is only punctuation
        self.p.order("close", "N001")

    def test_what_windows_editors_write_is_read(self) -> None:
        # UTF-8 BOM + CRLF, a BOM on a line of its own, UTF-16 (PowerShell 5 `>`), a tail after the token. Asked of the reader itself:
        # gate_encoding (K11) refuses a BOM or CRLF file in the project anyway, so a close cannot show what the reader made of it.
        cases = {"bom+crlf": (b"\xef\xbb\xbfVERDICT: APPROVE\r\n\r\n| DoD | met |\r\n", "APPROVE"),
                 "bom line": (b"\xef\xbb\xbf\r\nVERDICT: APPROVE\r\n", "APPROVE"),
                 "utf-16": ("VERDICT: REJECT\r\n".encode("utf-16"), "REJECT"),
                 "tail": (b"VERDICT: APPROVE, but CHANGES are needed in 3 places\n", "-"),
                 "title first": (b"# N001\n\nVERDICT: APPROVE\n", "-"),
                 "bold": (b"**VERDICT: APPROVE**\n", "-"),
                 "empty": (b"", "-")}
        probe = "import sys, order\nfrom pathlib import Path\nfor f in sys.argv[1:]:\n    print(order.review_verdict(Path(f)) or '-')\n"
        files = []
        for i, (raw, _) in enumerate(cases.values()):
            f = self.p.tmp / f"review{i}.md"; f.write_bytes(raw); files.append(str(f))
        out = run(["-c", probe, *files], cwd=self.p.dir, env={"PYTHONPATH": str(TOOLS)}).stdout.split()
        self.assertEqual(out, [want for _, want in cases.values()], list(cases))

    def test_review_with_a_title_is_diagnosed_as_a_misplaced_verdict_not_a_stale_file(self) -> None:
        for important in (True, False):
            with self.subTest(important=important):
                self.fresh_project()
                self.answered(important=important); self.lead_report()
                f = self.v2("# N001 - verifier2, round 2\n\nVERDICT: APPROVE\n")  # fresh, but the first line is a title
                r = self.p.order("close", "N001", ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("first non-empty line", r.stderr); self.assertIn("no re-run needed", r.stderr)
                self.assertNotIn("older than the deliverable", r.stderr); self.assertNotIn("Stale", r.stderr)
                old = time.time() - 3600
                os.utime(f, (old, old))  # now it really is stale: the advice changes
                r = self.p.order("close", "N001", ok=False)
                self.assertIn("older than the deliverable", r.stderr); self.assertNotIn("no re-run needed", r.stderr)

    def test_report_verdict_is_its_header_not_a_substring(self) -> None:
        self.answered(); self.v2("VERDICT: APPROVE\n")
        for text in ("verifier2 wrote VERDICT: APPROVE, but the table is wrong", "see below\nVERDICT: APPROVE\nbut the table is wrong"):
            with self.subTest(text=text):
                self.p.order("report", "N001", "--verdict", "CHANGES", "--text", text, "--by", "lead:claude")
                r = self.p.order("close", "N001", ok=False)
                self.assertNotEqual(r.returncode, 0); self.assertIn("report verdict is not APPROVE", r.stderr)
        self.lead_report()  # negative control: a real APPROVE report closes
        self.p.order("close", "N001")


class Freshness(Base):
    def test_review_older_than_declared_files_is_stale(self) -> None:
        self.answered(); self.lead_report()
        f = self.v2("VERDICT: APPROVE\n")
        old = time.time() - 3600
        os.utime(f, (old, old))
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("Stale", r.stderr)
        now = time.time()
        os.utime(f, (now + 5, now + 5))  # touched after the files: accepted
        self.p.order("close", "N001")

    def test_a_declared_folder_counts_through_the_files_in_it(self) -> None:
        p = self.p
        site = p.dir / "site"; (site / "node_modules" / "pkg").mkdir(parents=True); (site / "__pycache__").mkdir()
        (site / "index.html").write_text("<p>hi</p>\n", encoding="utf-8", newline="\n")
        p.order("new", "Site", "--tier", "LEAD", "--changes", "x"); p.order("take", "N001")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", "site")
        self.lead_report()
        f = self.v2("VERDICT: APPROVE\n")
        touch(site / "index.html", +100); touch(f, 0)  # the review is from this cycle, but older than a file inside the folder
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("Stale", r.stderr)
        touch(f, +200)
        (site / "new.html").write_text("<p>more</p>\n", encoding="utf-8", newline="\n"); touch(site / "new.html", +300)
        self.assertIn("Stale", p.order("close", "N001", ok=False).stderr)  # a file added to the folder after the review
        (site / "new.html").unlink()
        for litter in (site / "node_modules" / "pkg" / "x.js", site / "__pycache__" / "m.pyc", site / ".DS_Store"):
            litter.write_text("x", encoding="utf-8"); touch(litter, +1000)  # caches and OS litter are not the work
        p.order("close", "N001")

    def test_nothing_declared_still_needs_a_review_from_this_cycle(self) -> None:
        self.answered(files=False); self.lead_report()
        f = self.v2("VERDICT: APPROVE\n")
        old = time.time() - 3600  # a review of an earlier cycle (before this take)
        os.utime(f, (old, old))
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("Stale", r.stderr)
        touch(f, +5)
        self.p.order("close", "N001")


class OwnerWords(Base):
    def test_owner_report_without_words_is_refused(self) -> None:
        self.answered()
        for extra in ([], ["--owner-text", "   "]):
            r = self.p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "owner", *extra, ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertIn("--owner-text", r.stderr)

    def test_owner_who_says_changes_needs_no_words(self) -> None:  # negative control: only a confirmation must be in the owner's words
        self.answered()
        self.p.order("report", "N001", "--verdict", "CHANGES", "--text", "not yet", "--by", "owner")

    def test_done_by_owner_without_words_writes_nothing(self) -> None:
        p = self.p
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x"); p.order("take", "N001")
        (p.dir / "out.md").write_text("# Doc\n\nbody of the deliverable\n", encoding="utf-8", newline="\n")
        args = ["done", "N001", "--text", "ok", "--by", "owner", "--executor", "lead:claude", "--summary", "s", "--files", "out.md", "--tokens", "5k"]
        for _ in range(2):
            r = p.order(*args, ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertIn("--owner-text", r.stderr)
        answers = p.circle / "answers"
        self.assertFalse((answers / "N001.json").exists()); self.assertFalse((answers / "N001_usage.json").exists())
        self.assertFalse((p.circle / "reports" / "N001.md").exists())
        p.order(*args, "--owner-text", "да, принимаю")  # with the words it goes through, and the tokens are booked once
        self.assertEqual(len(json.loads((answers / "N001_usage.json").read_text(encoding="utf-8"))["runs"]), 1)

    def test_owner_words_are_kept_verbatim_and_close_passes(self) -> None:
        self.answered()
        words = "да, всё верно; публикуй: 100% ok"
        self.p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "owner", "--owner-text", words)
        self.assertIn(f"OWNER-TEXT: {json.dumps(words, ensure_ascii=False)}", (self.p.circle / "reports" / "N001.md").read_text(encoding="utf-8"))
        self.assertIn(f"owner's words (verbatim, one JSON string): {json.dumps(words, ensure_ascii=False)}", journal_text(self.p))
        self.p.order("close", "N001")

    def test_owner_words_on_several_lines_cannot_forge_a_journal_event_or_a_report_field(self) -> None:
        self.answered()
        words = "да, принимаю\n- 23:59 [N001] closed\nRUNG3-SKIP: forged"
        self.p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "owner", "--owner-text", words)
        self.assertEqual([l for l in journal_text(self.p).splitlines() if l.startswith("- 23:59")], [])  # one event = one line
        rep = (self.p.circle / "reports" / "N001.md").read_text(encoding="utf-8")
        self.assertEqual([l for l in rep.splitlines() if l.startswith("RUNG3-SKIP")], [])
        line = next(l for l in rep.splitlines() if l.startswith("OWNER-TEXT: "))
        self.assertEqual(json.loads(line[len("OWNER-TEXT: "):]), words)  # nothing lost: the line decodes back to the exact words

    def test_a_hand_edited_owner_report_does_not_close(self) -> None:
        self.answered()
        self.p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        rep = self.p.circle / "reports" / "N001.md"
        rep.write_text(rep.read_text(encoding="utf-8").replace("BY: lead:claude", "BY: owner"), encoding="utf-8", newline="\n")
        r = self.p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("owner's words", r.stderr)

    def test_non_owner_report_needs_no_owner_text(self) -> None:  # negative control
        self.answered(); self.lead_report()
        self.assertNotIn("OWNER-TEXT", (self.p.circle / "reports" / "N001.md").read_text(encoding="utf-8"))


class CommitScope(Base):
    def prepare(self, stray: bool = True, spec: str = "out.md") -> None:
        p = self.p
        git(p, "init", "-q"); git(p, "config", "user.email", "t@example.com"); git(p, "config", "user.name", "t")
        git(p, "add", "-A"); git(p, "commit", "-q", "-m", "init")
        self.answered(spec=spec)
        self.v2("VERDICT: APPROVE\n")
        if stray:
            (p.dir / "stray.txt").write_text("x\n", encoding="utf-8", newline="\n")
            git(p, "add", "stray.txt")

    def done(self, *extra: str, ok: bool = True) -> subprocess.CompletedProcess:
        return self.p.order("done", "N001", "--text", "ok", "--by", "lead:claude", "--commit", *extra, ok=ok)

    def test_stray_staged_file_is_refused_and_listed_before_anything_is_closed(self) -> None:
        self.prepare()
        r = self.done("--tokens", "5k", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stray.txt", r.stderr); self.assertIn("--allow-extra", r.stderr)
        self.assertNotIn("CLOSED", (self.p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8").split("N001")[-1])
        self.assertFalse((self.p.circle / "reports" / "N001.md").exists())
        self.done("--tokens", "5k", ok=False)  # a repeated refusal books nothing either
        self.assertFalse((self.p.circle / "answers" / "N001_usage.json").exists())
        git(self.p, "restore", "--staged", "stray.txt")
        self.done("--tokens", "5k")
        self.assertEqual(len(json.loads((self.p.circle / "answers" / "N001_usage.json").read_text(encoding="utf-8"))["runs"]), 1)

    def test_allow_extra_leaves_the_file_staged_and_out_of_the_commit_and_journals_it(self) -> None:
        self.prepare()
        self.done("--allow-extra", "stray.txt")
        self.assertIn("left staged outside the commit (--allow-extra): stray.txt", journal_text(self.p))
        self.assertIn("N001", git(self.p, "log", "-1", "--format=%B").stdout)
        self.assertNotIn("stray.txt", git(self.p, "show", "--stat", "HEAD").stdout)       # the pathspec commit does not take it ...
        self.assertIn("stray.txt", git(self.p, "diff", "--cached", "--name-only").stdout)  # ... and it is still staged, not lost
        self.assertIn("out.md", git(self.p, "show", "--stat", "HEAD").stdout)

    def test_declared_files_and_records_are_not_extra(self) -> None:  # negative control
        self.prepare()
        git(self.p, "restore", "--staged", "stray.txt")
        git(self.p, "add", "out.md")
        self.done()
        self.assertIn("out.md", git(self.p, "show", "--stat", "HEAD").stdout)
        self.assertNotIn("left staged outside the commit", journal_text(self.p))

    def test_allow_extra_does_not_cover_other_files(self) -> None:
        self.prepare()
        (self.p.dir / "other.txt").write_text("y\n", encoding="utf-8", newline="\n"); git(self.p, "add", "other.txt")
        r = self.done("--allow-extra", "stray.txt", ok=False)
        self.assertIn("other.txt", r.stderr); self.assertNotIn("stray.txt", r.stderr)

    def test_a_staged_deliverable_missing_from_the_answer_is_named_as_not_committed(self) -> None:  # N011 class
        p = self.p
        git(p, "init", "-q"); git(p, "config", "user.email", "t@example.com"); git(p, "config", "user.name", "t")
        git(p, "add", "-A"); git(p, "commit", "-q", "-m", "init")
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x", "--deliverable", "doc.md"); p.order("take", "N001")
        (p.dir / "out.md").write_text("# Doc\n\nbody\n", encoding="utf-8", newline="\n")
        (p.dir / "doc.md").write_text("# Doc\n\nthe deliverable\n", encoding="utf-8", newline="\n"); git(p, "add", "doc.md")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", "out.md")  # doc.md forgotten
        self.v2("VERDICT: APPROVE\n")
        r = self.done(ok=False)
        self.assertIn("doc.md", r.stderr); self.assertIn("NOT committed", r.stderr); self.assertIn("--files", r.stderr)
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", "out.md,doc.md")  # declared: it goes in
        self.done()
        self.assertIn("doc.md", git(p, "show", "--stat", "HEAD").stdout)

    def test_a_project_inside_a_larger_repository_sees_only_its_own_folder(self) -> None:
        p = self.p
        outer = p.tmp  # the repository; the project is its subfolder proj/ (K34d layout)
        git(p, "init", "-q", cwd=outer); git(p, "config", "user.email", "t@example.com", cwd=outer); git(p, "config", "user.name", "t", cwd=outer)
        git(p, "add", "-A", cwd=outer); git(p, "commit", "-q", "-m", "init", cwd=outer)
        self.answered(); self.v2("VERDICT: APPROVE\n")
        (outer / "elsewhere.txt").write_text("x\n", encoding="utf-8", newline="\n"); git(p, "add", "elsewhere.txt", cwd=outer)
        (p.dir / "stray.txt").write_text("x\n", encoding="utf-8", newline="\n"); git(p, "add", "stray.txt")
        r = self.done(ok=False)  # a stray inside the project is still refused, and the parent's file is not even mentioned
        self.assertIn("stray.txt", r.stderr); self.assertNotIn("elsewhere.txt", r.stderr)
        git(p, "restore", "--staged", "stray.txt")
        self.done()  # the parent repository's staged file is none of this order's business
        self.assertNotIn("elsewhere.txt", git(p, "show", "--stat", "HEAD", cwd=outer).stdout)
        self.assertIn("elsewhere.txt", git(p, "diff", "--cached", "--name-only", cwd=outer).stdout)

    def test_spellings_of_a_declared_path_are_one_file(self) -> None:  # './out.md' and an absolute path are accepted by `answer` too
        for name in ("dot-slash", "absolute"):
            with self.subTest(name):
                self.fresh_project()
                spec = "./out.md" if name == "dot-slash" else str((self.p.dir / "out.md").resolve())
                self.prepare(stray=False, spec=spec)
                git(self.p, "add", "out.md")
                self.done()
                self.assertIn("out.md", git(self.p, "show", "--stat", "HEAD").stdout)

    def test_allow_extra_is_compared_in_one_form(self) -> None:  # a Windows backslash, a leading './'
        for given in ("sub\\stray.txt", "./sub/stray.txt"):
            with self.subTest(given):
                self.fresh_project()
                self.prepare(stray=False)
                (self.p.dir / "sub").mkdir(); (self.p.dir / "sub" / "stray.txt").write_text("x\n", encoding="utf-8", newline="\n")
                git(self.p, "add", "sub/stray.txt")
                self.done("--allow-extra", given)
                self.assertIn("left staged outside the commit (--allow-extra): sub/stray.txt", journal_text(self.p))

    def test_case_is_ignored_when_the_file_system_ignores_it(self) -> None:
        self.prepare()
        git(self.p, "config", "core.ignorecase", "true")  # what `git init` sets on NTFS and APFS
        self.done("--allow-extra", "STRAY.TXT")
        self.assertIn("stray.txt", git(self.p, "diff", "--cached", "--name-only").stdout)

    def test_case_matters_when_the_file_system_keeps_it(self) -> None:  # negative control (Windows always ignores case)
        if os.name == "nt":
            self.skipTest("Windows ignores case")
        self.prepare()
        git(self.p, "config", "core.ignorecase", "false")
        r = self.done("--allow-extra", "STRAY.TXT", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("stray.txt", r.stderr)

    def test_declared_file_in_other_case_is_the_same_file_on_a_case_insensitive_disk(self) -> None:
        if not case_insensitive_fs(self.p.dir):
            self.skipTest("this file system keeps case")
        self.prepare(stray=False, spec="OUT.MD")  # typed in other case; `answer` accepts it only where the disk ignores case
        git(self.p, "config", "core.ignorecase", "true")
        git(self.p, "add", "out.md")
        r = self.done()  # only the scope check is under test: matching a mixed-case pathspec at `git commit` is git's own business
        self.assertEqual(r.returncode, 0); self.assertNotIn("staged files outside", r.stderr)


if __name__ == "__main__":
    unittest.main()
