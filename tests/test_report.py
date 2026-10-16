"""The optional HTML report reads plainly: names instead of hashes, labelled context, named downloads."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from citations_calculations.audit import audit_file
from citations_calculations.demo import run


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        run(root)
        cls.html = (root / "report/review.html").read_text()
        cls.answer = (root / "report/answer.md").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_caveats_name_their_source_instead_of_its_hash(self):
        caveats = self.html.split("<h2>Caveats</h2>")[1].split("</section>")[0]
        self.assertIn("company-release.html: ", caveats)
        self.assertNotRegex(caveats, r"s_[0-9a-f]{64}")
        exported = self.answer.split("Caveats:")[1].split("\n- **")[0]
        self.assertIn("company-release.html: ", exported)
        self.assertNotRegex(exported, r"s_[0-9a-f]{64}")

    def test_source_context_and_input_checks_are_labelled_lists(self):
        self.assertNotIn("&quot;cited_unit&quot;", self.html)
        self.assertNotIn("&quot;value_check&quot;", self.html)
        self.assertIn("<dt>Metric</dt><dd>Revenue</dd>", self.html)
        self.assertIn("<dt>Value check</dt><dd>Exact value and sign found in the cited passage</dd>", self.html)
        self.assertIn("<dt>Labels checked</dt><dd>label, period, scale, unit</dd>", self.html)

    def test_bold_and_bullets_in_claims_render_as_formatting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run(root)
            plan = json.loads((root / "input.json").read_text())
            plan["claims"][0]["text"] = "- Revenue grew **{{calc:growth}}** to {{fact:revenue_current}}."
            (root / "input.json").write_text(json.dumps(plan))
            audit_file(root, root / "input.json", root / "report")
            html = (root / "report/review.html").read_text()
            answer = (root / "report/answer.md").read_text()
        self.assertIn('<p>Revenue grew <strong><a href="#calc-growth">25.0%</a></strong> to', html)
        self.assertIn("Revenue grew **25.0%** to", answer)

    def test_snapshot_downloads_keep_a_file_name_with_its_extension(self):
        names = re.findall(r'href="evidence/s_[0-9a-f]{64}/raw" download="([^"]+)"', self.html)
        self.assertEqual(
            sorted(names), ["Northstar CRM export synthetic.json", "company-release.html", "financials.csv"]
        )


if __name__ == "__main__":
    unittest.main()
