"""通过分发 CLI 验证问题单材料和报告的保存、接续与保护。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest


TOOL = Path(__file__).resolve().parents[1] / "plugins/hutools/tools/yunxiao_issue.py"


class YunxiaoIssueTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name)
        self.parent = self.base / "问题单 归档"
        self.parent.mkdir()
        self.issue = "TEST-17"
        self.case = self.parent / self.issue
        self.download = self.base / "download"

    def run_cli(self, action, *args, body=None, issue=None):
        return subprocess.run(
            [sys.executable, "-B", str(TOOL), action, "--parent", str(self.parent),
             "--issue", issue or self.issue, *args],
            cwd=self.base, text=True, input=body, capture_output=True, timeout=10,
        )

    def result(self, action, *args, body=None):
        process = self.run_cli(action, *args, body=body)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def identity(self, project="org/project1", attachment="id-3", revision="v1"):
        return ["--project", project, "--attachment", attachment, "--revision", revision]

    def store(self, content, **identity):
        self.download.write_bytes(content)
        saved = self.result("store", *self.identity(**identity), "--file",
                            str(self.download), "--name", "故障.log")
        path = Path(saved["path"])
        self.assertTrue(path.is_absolute())
        self.assertTrue(path.resolve().is_relative_to(self.case))
        self.assertEqual(path.read_bytes(), content)
        return path

    def test_inspect_creates_directory_and_reports_existing_materials(self):
        self.result("inspect")
        self.assertTrue(self.case.is_dir())
        logs = self.case / "原始日志"
        logs.mkdir()
        (logs / "event.log").write_bytes(b"event")
        (self.case / "地图.json").write_bytes(b"{}")
        inventory = self.result("inspect")["files"]
        sizes = {file["path"]: file["size"] for file in inventory}
        self.assertEqual(sizes["原始日志/event.log"], 5)
        self.assertEqual(sizes["地图.json"], 2)

    def test_lookup_reuses_same_source_and_detects_same_length_corruption(self):
        self.result("inspect")
        self.assertIsNone(self.result("lookup", *self.identity())["path"])
        saved = self.store(b"event")
        self.assertEqual(self.result("lookup", *self.identity())["path"], str(saved))
        self.assertEqual(self.store(b"event"), saved)
        saved.write_bytes(b"other")
        self.assertIsNone(self.result("lookup", *self.identity())["path"])
        restored = self.store(b"event")
        self.assertEqual(saved.read_bytes(), b"other")
        self.assertEqual(self.result("lookup", *self.identity())["path"], str(restored))

    def test_same_filename_is_scoped_to_project_attachment_and_revision(self):
        self.result("inspect")
        original = self.case / "故障.log"
        original.write_bytes(b"legacy log")
        first = self.store(b"first")
        for n, identity in enumerate([
            {"project": "org/project2"}, {"revision": "v2"}, {"attachment": "id-4"},
        ]):
            with self.subTest(identity=identity):
                self.assertIsNone(self.result("lookup", *self.identity(**identity))["path"])
                other = self.store(str(n).encode(), **identity)
                self.assertNotEqual(other, first)
                self.assertEqual(self.result("lookup", *self.identity(**identity))["path"], str(other))
        self.assertEqual(original.read_bytes(), b"legacy log")
        self.assertEqual(first.read_bytes(), b"first")

    def test_repair_checks_previous_fingerprint_for_the_same_revision(self):
        self.result("inspect")
        saved = self.store(b"official attachment")
        saved.write_bytes(b"damaged local copy")
        self.download.write_bytes(b"unrelated download")
        failed = self.run_cli("store", *self.identity(), "--file",
                              str(self.download), "--name", "故障.log")
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIsNone(self.result("lookup", *self.identity())["path"])
        self.assertEqual(saved.read_bytes(), b"damaged local copy")
        recovered = self.store(b"official attachment")
        self.assertEqual(self.result("lookup", *self.identity())["path"], str(recovered))

    def test_report_preserves_prior_text_and_uses_fixed_path(self):
        self.result("inspect")
        report = self.case / "详细分析报告.md"
        prior = "历史现场报告\n"
        report.write_text(prior, encoding="utf-8")
        new = "## 问题根因\n请提供故障时段。\n\n## 解决方案\n现场补充后复核。\n"
        self.assertEqual(self.result("report", body=new)["path"], str(report))
        self.assertEqual(report.read_text(encoding="utf-8"), new)
        self.assertTrue(any(
            path.is_file() and path.read_bytes() == prior.encode()
            for path in self.case.rglob("*")
        ))
        self.assertEqual(self.result("report", body=new)["path"], str(report))
        self.assertEqual(report.read_text(encoding="utf-8"), new)

    def test_concurrent_reports_preserve_every_version(self):
        self.result("inspect")
        original = "首次记录\n"
        report = self.case / "详细分析报告.md"
        report.write_text(original, encoding="utf-8")
        versions = ["现场补充时间\n", "对照源码后的结论\n"]
        start = Barrier(2)

        def submit(body):
            start.wait(timeout=10)
            return self.run_cli("report", body=body)

        with ThreadPoolExecutor(max_workers=2) as executor:
            for result in executor.map(submit, versions):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(report.read_text(encoding="utf-8"), versions)
        contents = {p.read_bytes() for p in self.case.rglob("*") if p.is_file()}
        for text in [original, *versions]:
            self.assertIn(text.encode(), contents)

    def test_rejects_report_link_and_issue_outside_parent(self):
        self.result("inspect")
        other = self.base / "outside"
        other.mkdir()
        elsewhere = other / "report"
        elsewhere.write_text("keep")
        (self.case / "详细分析报告.md").symlink_to(elsewhere)
        self.assertNotEqual(self.run_cli("report", body="new report").returncode, 0)
        self.assertEqual(elsewhere.read_text(), "keep")
        (self.parent / "LINK").symlink_to(other, target_is_directory=True)
        for issue in ("LINK", "../outside"):
            with self.subTest(issue=issue):
                self.assertNotEqual(self.run_cli("inspect", issue=issue).returncode, 0)
        self.assertEqual(list(other.iterdir()), [elsewhere])


if __name__ == "__main__":
    unittest.main()
