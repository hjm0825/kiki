import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "plugins/hutools/tools/project_rules.py"


class ProjectRulesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "项目"
        self.root.mkdir()
        self.input = Path(temp.name) / "decisions.json"
        self.rules = [{"rule": "旧接口必须兼容。", "source": "设计.md"}]
        (self.root / "设计.md").write_text("旧接口必须兼容。", encoding="utf-8")

    def generate(self, *args):
        self.input.write_text(json.dumps(self.rules), encoding="utf-8")
        return subprocess.run([sys.executable, str(TOOL), "--root", str(self.root),
                               "--decisions", str(self.input), *args],
                              capture_output=True, text=True)

    def test_preview_then_create_then_repeat(self):
        preview = self.generate()
        self.assertEqual(preview.returncode, 0, preview.stderr)
        path = self.root / "AGENTS.md"
        self.assertFalse(path.exists())
        self.assertIn("旧接口必须兼容。", preview.stdout)
        self.assertIn("设计.md", preview.stdout)
        self.assertEqual(self.generate("--init").returncode, 0)
        self.assertEqual(path.read_text(), preview.stdout)
        before = path.stat().st_mtime_ns
        self.assertEqual(self.generate("--init").returncode, 0)
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_no_history_requires_confirmation(self):
        self.rules = []
        self.assertNotEqual(self.generate("--init").returncode, 0)
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.rules = [{"rule": "提交前运行单测。", "confirmation": "用户在接入时确认该要求"}]
        self.assertEqual(self.generate("--init").returncode, 0)

    def test_missing_or_external_evidence_rejected(self):
        for source in ("不存在.md", "../decisions.json", str(self.input)):
            self.rules[0]["source"] = source
            self.assertNotEqual(self.generate("--init").returncode, 0)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_existing_manual_file_unchanged(self):
        target = self.root / "AGENTS.md"
        target.write_text("人工决定")
        self.assertNotEqual(self.generate("--init").returncode, 0)
        self.assertEqual(target.read_text(), "人工决定")

    def test_reject_symlink(self):
        target = self.root / "AGENTS.md"
        target.symlink_to(self.input)
        self.assertNotEqual(self.generate("--init").returncode, 0)
        self.assertTrue(target.is_symlink())

    def preview(self, rows, *extra):
        review = self.input.with_name("review.json")
        review.write_text(json.dumps(rows))
        return self.generate("--migration-preview", str(review), *extra)

    def row(self, path, role="rules", action="retire", **fields):
        return dict({"path": path, "role": role, "action": action, "scope": "root",
                     "reason": "已核对内容和引用", "rules": [1], "consumers": []}, **fields)

    def test_empty_project_first_creation(self):
        self.root = self.root.parent / "blank"
        self.root.mkdir()
        self.rules = [{"rule": "发布前运行单测。", "confirmation": "已确认的发布约定"}]
        preview = self.preview([])
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["root_action"], "create")
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.generate("--init").returncode, 0)
        self.assertEqual([p.name for p in self.root.iterdir()], ["AGENTS.md"])

    def test_review_names_are_project_specific(self):
        target = self.root / "AGENTS.md"
        target.write_text("已有人工条款\n")
        for name in ("约定.txt", "handbook"):
            with self.subTest(name=name):
                (self.root / name).write_text("旧接口必须兼容。")
                rows = [self.row(name, consumers=["AGENTS.md"]),
                        self.row("设计.md", "document", "keep")]
                result = self.preview(rows)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["root_action"], "replace")
                self.assertIn("-已有人工条款", report["diff"])
                self.assertEqual(report["files"][0]["path"], name)
                self.assertEqual(target.read_text(), "已有人工条款\n")
                self.assertTrue((self.root / name).exists())

    def test_reject_inconsistent_retirements(self):
        old = self.root / "obsolete"
        old.write_text("旧接口必须兼容。")
        for rows in ([self.row("obsolete", "mixed")], [self.row("obsolete", scope="nested")],
                     [self.row("obsolete", "document")], [self.row("obsolete", role="unknown")],
                     [self.row("obsolete", rules=[])], [self.row("obsolete", rules=[2])],
                     [self.row("设计.md")], [self.row("obsolete", consumers=["设计.md"])],
                     [self.row("obsolete"), self.row("./obsolete")],
                     [self.row("../decisions.json")], [self.row("AGENTS.md")]):
            with self.subTest(rows=rows):
                self.assertNotEqual(self.preview(rows).returncode, 0)
        self.assertTrue(old.is_file())

    def test_mixed_file_updates_references_and_conflict_stays_open(self):
        for name in ("旧模板", "使用指南", "待定"):
            (self.root / name).touch()
        rows = [self.row("旧模板", "template", rules=[], consumers=["使用指南"]),
                self.row("使用指南", "mixed", "update"),
                self.row("待定", "unknown", "pending", rules=[])]
        result = self.preview(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["pending"], ["待定"])
        self.assertNotEqual(self.preview(rows, "--init").returncode, 0)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_review_rejects_links(self):
        (self.root / "别名").symlink_to(self.root / "设计.md")
        self.assertNotEqual(self.preview([self.row("别名")]).returncode, 0)
        (self.root / "AGENTS.md").symlink_to(self.input)
        self.assertNotEqual(self.preview([]).returncode, 0)

    def prepare(self):
        self.batch = self.root.parent / "reviewed-batch"
        self.old = self.root / "团队约定.txt"
        self.old.write_text("旧接口必须兼容。")
        (self.root / "README.md").write_text("参阅团队约定.txt")
        (self.root / "AGENTS.md").write_text("人工规则")
        self.changes = {"README.md": "参阅 AGENTS.md", ".env.hutools-materials":
                        "MATERIALS_LIBRARY=" + str(self.root.parent) + "\n"}
        changes = self.root.parent / "changes.json"
        changes.write_text(json.dumps(self.changes))
        rows = [self.row(self.old.name, consumers=["README.md"]),
                self.row("README.md", "mixed", "update")]
        result = self.preview(rows, "--prepare", str(self.batch), "--changes", str(changes))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["sha256"]

    def apply_batch(self, key):
        return subprocess.run([sys.executable, str(TOOL), "--root", str(self.root),
                               "--apply", str(self.batch), "--confirm", key],
                              text=True, capture_output=True)

    def test_reviewed_batch_executes_twice(self):
        key = self.prepare()
        self.assertTrue(self.old.exists())
        self.assertNotEqual(self.apply_batch("0" * 64).returncode, 0)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "人工规则")
        result = self.apply_batch(key)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.old.exists())
        for name, wanted in self.changes.items():
            self.assertEqual((self.root / name).read_text(), wanted)
        self.assertIn("旧接口必须兼容", (self.root / "AGENTS.md").read_text())
        before = (self.root / "AGENTS.md").stat().st_mtime_ns
        self.assertEqual(self.apply_batch(key).returncode, 0)
        self.assertEqual((self.root / "AGENTS.md").stat().st_mtime_ns, before)
        plan = json.loads((self.batch / "plan.json").read_text())
        self.assertEqual(plan["files"][self.old.name]["before"], "旧接口必须兼容。")

    def test_drift_stops_before_any_write(self):
        key = self.prepare()
        self.old.write_text("用户后来增加的内容")
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        self.assertEqual((self.root / "README.md").read_text(), "参阅团队约定.txt")
        self.assertEqual((self.root / "AGENTS.md").read_text(), "人工规则")

    def test_changed_source_and_link_rejected(self):
        key = self.prepare()
        source = self.root / "设计.md"
        data = source.read_bytes()
        source.write_text("决定已改变")
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        source.write_bytes(data)
        external = self.root.parent / "other"
        external.write_bytes(self.old.read_bytes())
        self.old.unlink()
        self.old.symlink_to(external)
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        self.assertTrue(external.exists())

    def test_resume_after_process_exit_keeps_retirements_last(self):
        key = self.prepare()
        script = """
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
import rules_migration as m
original = m.install
def interrupted(root, name, entry):
    original(root, name, entry)
    if name == "README.md":
        os._exit(73)
m.install = interrupted
m.apply(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
"""
        result = subprocess.run([sys.executable, "-IB", "-c", script, str(TOOL),
                                 str(self.root), str(self.batch), key])
        self.assertEqual(result.returncode, 73)
        self.assertTrue(self.old.exists())
        self.assertEqual((self.root / "README.md").read_text(), self.changes["README.md"])
        result = self.apply_batch(key)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.old.exists())
        (self.root / "AGENTS.md").write_text("迁移完成后的用户编辑")
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "迁移完成后的用户编辑")

    def test_wrong_checkout_and_tampered_plan(self):
        key = self.prepare()
        real = self.root
        self.root = self.root.parent / "不同目录"
        self.root.mkdir()
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        self.root = real
        path = self.batch / "plan.json"
        plan = json.loads(path.read_text())
        plan["files"]["AGENTS.md"]["after"] = "另一份内容"
        path.write_text(json.dumps(plan))
        self.assertNotEqual(self.apply_batch(key).returncode, 0)
        self.assertTrue(self.old.exists())

    def test_pending_missing_payload_and_control_paths(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("现有配置")
        batch = self.root.parent / "unused"
        for rows in ([self.row("设计.md", "unknown", "pending")],
                     [self.row("设计.md", "document", "update")],
                     [self.row(".git/config", "template", "retire", rules=[])]):
            with self.subTest(rows=rows):
                self.assertNotEqual(self.preview(rows, "--prepare", str(batch)).returncode, 0)
                self.assertFalse(batch.exists())
        self.assertFalse((self.root / "AGENTS.md").exists())
