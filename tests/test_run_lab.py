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


class TestApiIpOverride(unittest.TestCase):
    def test_only_the_api_host_is_redirected(self):
        import os
        import socket
        from unittest import mock
        from labelforge_core import saptiva

        with mock.patch.dict(os.environ, {saptiva.API_IP_ENV: "172.16.127.20"}), \
                mock.patch.object(socket, "getaddrinfo", socket.getaddrinfo), \
                mock.patch.object(saptiva, "_real_getaddrinfo", return_value=["ok"]) as real, \
                mock.patch.dict(saptiva._ip_overrides, clear=True):
            saptiva.apply_ip_override("https://api.lab.test/infernode/v1")
            socket.getaddrinfo("api.lab.test", 443)
            socket.getaddrinfo("github.com", 443)
        self.assertEqual([c.args[0] for c in real.call_args_list], ["172.16.127.20", "github.com"])

    def test_invalid_ip_is_rejected(self):
        import os
        from unittest import mock
        from labelforge_core import saptiva

        with mock.patch.dict(os.environ, {saptiva.API_IP_ENV: "no-es-ip"}):
            with self.assertRaises(ValueError):
                saptiva.apply_ip_override("https://api.lab.test/v1")


if __name__ == "__main__":
    unittest.main()
