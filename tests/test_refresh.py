"""Restyling a note must not change or mislabel its audited evidence."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from citations_calculations.demo import run
from citations_calculations.store import EvidenceError, save_json

spec = importlib.util.spec_from_file_location(
    "refresh_notes", Path(__file__).resolve().parents[1] / "scripts/refresh_notes.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.chat = run(self.root)["chat"]
        self.snapshot = Path(self.chat["audit_target"][:-2]).parent

    def tearDown(self):
        self.tmp.cleanup()

    def test_refresh_backs_up_notes_without_changing_audit_or_source_bytes(self):
        note = Path(self.chat["citations"][0]["target"][:-2])
        original = note.read_bytes()
        note.write_text("Previous presentation")
        records = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p.suffix != ".md"}
        result = module.refresh(self.root)
        self.assertEqual(result["notes_refreshed"], 1)
        self.assertEqual(note.read_bytes(), original)
        for p, before in records.items():
            self.assertEqual(p.read_bytes(), before)
        backup = Path(result["backup"]) / note.relative_to(self.root)
        self.assertEqual(backup.read_text(), "Previous presentation")

    def test_refresh_uses_audited_title_instead_of_unchecked_chat_metadata(self):
        file = self.snapshot / "answer.json"
        data = json.loads(file.read_text())
        data["citations"][0]["title"] = "Invented source"
        save_json(file, data)
        module.refresh(self.root)
        for note in self.snapshot.glob("*.md"):
            self.assertNotIn("Invented source", note.read_text())

    def test_corrupt_saved_source_stops_refresh_before_any_page_changes(self):
        note = Path(self.chat["citations"][0]["target"][:-2])
        note.write_text("Previous presentation")
        raw = next((self.snapshot / "evidence").glob("*/raw"))
        raw.write_bytes(b"Altered source")
        with self.assertRaisesRegex(EvidenceError, "source content changed"):
            module.refresh(self.root)
        self.assertEqual(note.read_text(), "Previous presentation")

    def test_duplicate_citation_ids_cannot_overwrite_a_different_source_note(self):
        file = self.snapshot / "answer.json"
        data = json.loads(file.read_text())
        data["citations"][1]["id"] = data["citations"][0]["id"]
        save_json(file, data)
        with self.assertRaisesRegex(EvidenceError, "duplicate"):
            module.refresh(self.root)
