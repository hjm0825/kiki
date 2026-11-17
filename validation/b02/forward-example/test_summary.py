import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from summary import summarize


class SummaryTests(unittest.TestCase):
    def test_case_blank_and_present_levels(self):
        self.assertEqual(summarize(["info: 启动\n", "ERROR: 停止\n", "\n", "Info: 恢复\n", " \t\n"]), {"INFO": 2, "ERROR": 1})

    def test_three_levels_and_message_colons(self):
        self.assertEqual(summarize([" WARN : a:b", "warn:", "error: x", "INFO: y"]), {"WARN": 2, "ERROR": 1, "INFO": 1})

    def test_empty(self):
        self.assertEqual(summarize([]), {})
        self.assertEqual(summarize([" \t\n"]), {})

    def test_invalid_lines(self):
        for line in ("DEBUG: x", "INFO missing colon", ": empty", "info"):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, "line 2"):
                    summarize(["INFO: valid", line])


class CliTests(unittest.TestCase):
    script = Path(__file__).resolve().with_name("cli.py")

    def run_input(self, content):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "日志.log").write_text(content, encoding="utf-8")
            return subprocess.run([sys.executable, str(self.script), "日志.log"], cwd=directory, text=True, encoding="utf-8", capture_output=True, timeout=5, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_real_cli_utf8_relative_cwd_stable_json(self):
        result = self.run_input("warn: 警告\nINFO: 启动\nerror: 失败\ninfo: 恢复\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, '{"ERROR": 1, "INFO": 2, "WARN": 1}\n')
        self.assertEqual(json.loads(result.stdout), {"ERROR": 1, "INFO": 2, "WARN": 1})

    def test_real_cli_rejects_without_success_json(self):
        for content in ("INFO: ok\nDEBUG: bad", "INFO: ok\nERROR missing colon"):
            with self.subTest(content=content):
                result = self.run_input(content)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("line 2", result.stderr)

    def test_real_cli_blank_only(self):
        result = self.run_input(" \n\t\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")


if __name__ == "__main__":
    unittest.main()
