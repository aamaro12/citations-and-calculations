"""A delivered chat message must keep the checked figures, their links and the source links."""

import contextlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from citations_calculations.cli import main
from citations_calculations.delivery import check_answer
from citations_calculations.demo import run
from citations_calculations.store import save_json

LINK = re.compile(r"(?<!\\)\[([^\]]*)\]\(<([^>]+)>\)")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="citecalc delivery ")
        self.root = Path(self.tmp.name).resolve()
        self.chat = run(self.root)["chat"]
        self.text = self.chat["answer_markdown"]
        self.bundle = self.root / "input.json"

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, text, base=None):
        return check_answer(self.root, self.bundle, text, base or self.root)

    def test_the_checked_answer_passes_unchanged(self):
        self.assertEqual(self.check(self.text), {"status": "passed", "problems": []})

    def test_relative_links_without_line_suffix_resolve_to_the_same_pages(self):
        relative = LINK.sub(
            lambda m: "[" + m[1] + "](" + str(Path(m[2][:-2]).relative_to(self.root)) + ")",
            self.text,
        )
        self.assertNotIn("](<", relative)
        self.assertEqual(self.check(relative)["problems"], [])

    def test_source_links_may_be_relabelled_but_not_dropped(self):
        relabelled = re.sub(r"\[S2 · [^\]]+\]", "[Revenue row]", self.text)
        self.assertEqual(self.check(relabelled)["problems"], [])
        dropped = re.sub(r" \[S2 · [^\]]+\]\(<[^>]+>\)", "", self.text)
        problems = self.check(dropped)["problems"]
        self.assertTrue(any("source link" in p and "S2" in p for p in problems), problems)

    def test_a_relabelled_or_missing_figure_is_reported(self):
        problems = self.check(self.text.replace("[25.0%]", "[25%]"))["problems"]
        self.assertTrue(any("25.0%" in p for p in problems), problems)
        unlinked = re.sub(r"\[25\.0%\]\(<[^>]+>\)", "25.0%", self.text)
        self.assertTrue(any("25.0%" in p for p in self.check(unlinked)["problems"]))

    def test_a_number_the_audit_did_not_check_is_reported(self):
        problems = self.check(self.text + "\nRevenue could reach 2,000 million USD next year.\n")["problems"]
        self.assertTrue(any("2,000" in p for p in problems), problems)

    def test_a_blocked_answer_must_still_say_checks_failed(self):
        plan = json.loads(self.bundle.read_text())
        plan["claims"][0]["text"] = "Growth was 900%."
        save_json(self.bundle, plan)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(
                [
                    "answer",
                    "--workspace",
                    str(self.root),
                    "--bundle",
                    str(self.bundle),
                    "--output",
                    str(self.root / "report"),
                ]
            )
        blocked = output.getvalue()
        self.assertEqual(self.check(blocked)["problems"], [])
        problems = self.check(blocked.replace("**Draft: checks failed. Blocked claims below are not verified.**", ""))
        self.assertTrue(any("checks failed" in p for p in problems["problems"]), problems)

    def test_links_to_an_audit_that_no_longer_matches_the_bundle_are_reported(self):
        plan = json.loads(self.bundle.read_text())
        plan["facts"][1]["review"]["rationale"] = "Rechecked against the row labels."
        save_json(self.bundle, plan)
        problems = self.check(self.text)["problems"]
        self.assertTrue(any("fresh audit" in p for p in problems), problems)

    def test_the_command_exits_2_when_the_message_changed(self):
        message = self.root / "final.md"
        message.write_text(self.text.replace("[25.0%]", "[25%]"))
        args = ["check-answer", "--workspace", str(self.root), "--bundle", str(self.bundle), "--file", str(message)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args + ["--base", str(self.root)]), 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "changed")
        message.write_text(self.text)
        cwd = os.getcwd()
        try:
            os.chdir(self.root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
