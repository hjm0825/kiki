"""以损坏包和迁移场景验证发布检查，不执行插件或修改用户工程。"""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_bundle", ROOT / "scripts/check_bundle.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hutools-bundle-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "original"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.plugin = self.root / "plugins/hutools"

    def errors(self):
        return CHECKER.validate(self.root)

    def test_release_bundle_is_valid(self):
        self.assertEqual([], self.errors())

    def test_move_with_original_absent_and_unrelated_cwd(self):
        moved = self.base / "另一个位置 with spaces"
        self.root.rename(moved)
        result = subprocess.run(
            [sys.executable, str(moved / "scripts/check_bundle.py")],
            cwd=self.base, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertFalse(self.root.exists())

    def test_missing_runtime_guard_fails(self):
        (self.plugin / "hooks/guard.py").unlink()
        self.assertTrue(any("guard.py" in e for e in self.errors()))

    def test_hook_cannot_call_external_script(self):
        path = self.plugin / "hooks/hooks.json"
        data = json.loads(path.read_text())
        data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "python3 /external.py"
        path.write_text(json.dumps(data))
        self.assertTrue(any("包内正式处理器" in e for e in self.errors()))

    def test_hook_matcher_and_async_regression_rejected(self):
        path = self.plugin / "hooks/hooks.json"
        original = json.loads(path.read_text())
        for matcher in ["__hutools_no_real_tool__", "["]:
            data = json.loads(json.dumps(original))
            data["hooks"]["PreToolUse"][0]["matcher"] = matcher
            path.write_text(json.dumps(data))
            self.assertTrue(any("matcher" in e for e in self.errors()))
        data = json.loads(json.dumps(original))
        data["hooks"]["PreToolUse"][0]["hooks"][0]["async"] = True
        path.write_text(json.dumps(data))
        self.assertTrue(any("同步" in e for e in self.errors()))

    def test_missing_skill_fails(self):
        (self.plugin / "skills/diagnosing-bugs/SKILL.md").unlink()
        self.assertTrue(any("diagnosing-bugs" in e for e in self.errors()))

    def test_marketplace_cannot_escape_repository(self):
        path = self.root / ".agents/plugins/marketplace.json"
        data = json.loads(path.read_text())
        data["plugins"][0]["source"]["path"] = "./../outside"
        path.write_text(json.dumps(data))
        self.assertTrue(any("越界" in e for e in self.errors()))

    def test_symlink_escape_fails_without_reading_target(self):
        secret = self.base / "outside.txt"
        secret.write_text("outside input must remain unchanged")
        (self.plugin / "linked.txt").symlink_to(secret)
        self.assertTrue(any("符号链接" in e for e in self.errors()))
        self.assertEqual("outside input must remain unchanged", secret.read_text())

    def test_broken_nested_reference_fails(self):
        path = self.plugin / "skills/hutools/references/analysis.md"
        path.write_text(path.read_text() + "\n[失效资料](missing.md)\n")
        self.assertTrue(any("missing.md" in e for e in self.errors()))

    def test_installed_skill_cannot_depend_on_repository_only_file(self):
        path = self.plugin / "skills/hutools/SKILL.md"
        path.write_text(path.read_text() + "\n[错误依赖](../../../../README.md)\n")
        self.assertTrue(any("包外引用" in e for e in self.errors()))

    def test_missing_upstream_license_fails(self):
        (self.plugin / "licenses/mattpocock-MIT.txt").unlink()
        self.assertTrue(any("mattpocock-MIT.txt" in e for e in self.errors()))

    def test_duplicate_marketplace_entry_fails(self):
        path = self.root / ".agents/plugins/marketplace.json"
        data = json.loads(path.read_text())
        data["plugins"].append(data["plugins"][0])
        path.write_text(json.dumps(data))
        self.assertTrue(any("重复" in e for e in self.errors()))

    def test_invalid_manifest_returns_actionable_cli_failure(self):
        (self.plugin / "plugin.json").write_text("{broken")
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts/check_bundle.py")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("plugin.json" in e for e in payload["errors"]))


if __name__ == "__main__":
    unittest.main()
