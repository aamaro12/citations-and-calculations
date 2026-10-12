"""The optional Markdown export escapes untrusted text, as the chat answer does."""

from pathlib import Path
import tempfile
import unittest

from citations_calculations.report import render


class ExportTests(unittest.TestCase):
    def test_claim_text_and_caveats_cannot_inject_markdown_links(self):
        claim = "See [the filing](javascript:alert) for details"
        result = {
            "status": "checks_passed_with_caveats",
            "boundary": "Checks establish snapshot integrity.",
            "warnings": [{"at": "s_x", "message": "Source says ![tracker](https://example.com/pixel)"}],
            "issues": [],
            "claims": [
                {
                    "id": "c1",
                    "kind": "inference",
                    "status": "checks_passed",
                    "text": claim,
                    "rendered_text": claim,
                    "evidence": [],
                    "fact_dependencies": [],
                    "support_review": None,
                }
            ],
            "facts": {},
            "calculations": {},
            "sources": {},
            "tool_version": "test",
            "audited_at": "2026-10-12T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as td:
            render(Path(td), result)
            answer = (Path(td) / "answer.md").read_text()
        self.assertNotIn("[the filing](javascript", answer)
        self.assertNotIn("![tracker](", answer)
        self.assertIn("the filing", answer)
        self.assertNotIn("—", answer)


if __name__ == "__main__":
    unittest.main()
