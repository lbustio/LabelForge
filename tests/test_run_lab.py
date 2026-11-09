"""Tests de las partes puras de run_lab.py (sin red ni subprocesos)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_lab


class TestRunLab(unittest.TestCase):
    def test_resume_only_for_same_unfinished_dataset(self):
        data = Path("/x/a.csv")
        self.assertTrue(run_lab.should_resume({"status": "paused", "input_file": "/x/a.csv"}, data))
        self.assertFalse(run_lab.should_resume({"status": "completed", "input_file": "/x/a.csv"}, data))
        self.assertFalse(run_lab.should_resume({"status": "paused", "input_file": "/x/b.csv"}, data))
        self.assertFalse(run_lab.should_resume({}, data))

    def test_new_dataset_starts_fresh_and_resume_does_not(self):
        fresh = run_lab.build_cli_command("py", Path("a.csv"), Path("c.json"), "txt", None, resume=False)
        resumed = run_lab.build_cli_command("py", Path("a.csv"), Path("c.json"), "txt", 5, resume=True)
        self.assertIn("--batch", fresh)
        self.assertIn("--fresh", fresh)
        self.assertNotIn("--fresh", resumed)
        self.assertEqual(resumed[resumed.index("--limit") + 1], "5")

    def test_run_key_changes_with_content_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, cfg = Path(tmp) / "d.csv", Path(tmp) / "c.json"
            data.write_text("txt\na\n")
            cfg.write_text("{}")
            first = run_lab.run_key(data, cfg, "txt", None)
            self.assertNotEqual(first, run_lab.run_key(data, cfg, "txt", 10))
            data.write_text("txt\nb\n")
            self.assertNotEqual(first, run_lab.run_key(data, cfg, "txt", None))

    def test_parser_accepts_several_datasets(self):
        args = run_lab.build_parser().parse_args(["run", "a.csv", "b.csv", "--bg"])
        self.assertEqual((args.datasets, args.bg, args.column), (["a.csv", "b.csv"], True, "txt"))


if __name__ == "__main__":
    unittest.main()
