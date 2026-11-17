"""资料生命周期行为测试；只处理每例自己的临时资产。"""
import importlib.util
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/hutools/tools/lifecycle.py'


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hutools-lifecycle-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        (self.root / 'sample.txt').write_text('original evidence\n')
        spec = importlib.util.spec_from_file_location('lifecycle', SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)

    def metadata(self, **changes):
        result = dict(path='sample.txt', category='rebuildable', unique=False,
                      unresolved_failure=False, active=False, recovery_required=False,
                      retention_satisfied=True, reproducible=True,
                      rationale='temporary output; input and rebuild command verified',
                      rebuild_method='test fixture reconstruction',
                      retention_condition='fixture review finished',
                      authorization_ref='test-user-confirmation',
                      allowed_actions=['quarantine'])
        result.update(changes)
        return result

    def plan(self, **changes):
        return self.api.make_plan(self.root, 'fixture-project', 'fixture-task', [self.metadata(**changes)])

    def test_exact_file_plan_and_content_verification(self):
        plan = self.plan()
        self.assertEqual(['sample.txt'], [r['path'] for r in plan['records']])
        self.assertTrue(self.api.verify(self.root, plan)['ok'])
        (self.root / 'sample.txt').write_text('changed')
        self.assertFalse(self.api.verify(self.root, plan)['ok'])

    def test_unknown_metadata_keeps_original(self):
        plan = self.api.make_plan(self.root, 'p', 't', [{'path': 'sample.txt'}])
        review = self.api.cleanup_plan(self.root, plan)
        self.assertEqual([], review['candidates'])
        self.assertTrue(review['retained'][0]['reasons'])
        self.assertTrue((self.root / 'sample.txt').exists())

    def test_failure_active_unique_recovery_and_retention_are_preserved(self):
        for flags in [dict(unresolved_failure=True), dict(active=True), dict(unique=True),
                      dict(recovery_required=True), dict(retention_satisfied=False),
                      dict(reproducible=False), dict(category='core')]:
            with self.subTest(flags=flags):
                self.assertEqual([], self.api.cleanup_plan(self.root, self.plan(**flags))['candidates'])

    def test_escape_absolute_and_duplicate_paths_are_rejected(self):
        for path in ['../outside', str(self.root / 'sample.txt'), './sample.txt', 'x/../sample.txt']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.plan(path=path)
        with self.assertRaises(ValueError):
            self.api.make_plan(self.root, 'p', 't', [self.metadata(), self.metadata()])

    def test_symlink_input_is_rejected(self):
        (self.root / 'link').symlink_to(self.root / 'sample.txt')
        with self.assertRaises(ValueError):
            self.plan(path='link')
        (self.root / 'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.plan(path='alias/sample.txt')

    def test_archive_preserves_source_and_checks_copy(self):
        plan = self.plan()
        dest = self.base / 'archive'
        result = self.api.archive(self.root, plan, dest)
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'sample.txt').read_bytes(), (dest / 'files/sample.txt').read_bytes())
        self.assertTrue(self.api.verify(dest / 'files', plan)['ok'])
        self.assertTrue((dest / 'receipt.json').is_file())

    def test_archive_conflict_and_changed_input_leave_source_intact(self):
        plan = self.plan()
        dest = self.base / 'archive'
        dest.mkdir()
        (dest / 'user.txt').write_text('user data')
        with self.assertRaises(ValueError):
            self.api.archive(self.root, plan, dest)
        self.assertEqual('user data', (dest / 'user.txt').read_text())
        (self.root / 'sample.txt').write_text('new content')
        with self.assertRaises(ValueError):
            self.api.archive(self.root, plan, self.base / 'fresh')
        self.assertEqual('new content', (self.root / 'sample.txt').read_text())
        self.assertFalse((self.base / 'fresh').exists())

    def test_quarantine_requires_exact_confirmation_and_all_eligible(self):
        plan = self.plan()
        dest = self.base / 'quarantine'
        with self.assertRaises(ValueError):
            self.api.quarantine(self.root, plan, dest, 'wrong')
        unknown = self.plan(unique=True)
        with self.assertRaises(ValueError):
            self.api.quarantine(self.root, unknown, dest, self.api.plan_digest(unknown))
        self.assertTrue((self.root / 'sample.txt').exists())
        self.assertFalse(dest.exists())

    def test_quarantine_and_restore_without_overwriting(self):
        plan = self.plan()
        dest = self.base / 'quarantine'
        self.api.quarantine(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertFalse((self.root / 'sample.txt').exists())
        self.assertEqual('original evidence\n', (dest / 'files/sample.txt').read_text())
        (self.root / 'sample.txt').write_text('new user data')
        with self.assertRaises(ValueError):
            self.api.restore(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertEqual('new user data', (self.root / 'sample.txt').read_text())
        (self.root / 'sample.txt').unlink()
        self.api.restore(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertEqual('original evidence\n', (self.root / 'sample.txt').read_text())

    def test_partial_quarantine_has_recoverable_exact_locations(self):
        (self.root / 'second.txt').write_text('second evidence')
        plan = self.api.make_plan(self.root, 'p', 't', [self.metadata(), self.metadata(path='second.txt')])
        dest = self.base / 'quarantine'
        rename = self.api.os.rename
        count = 0
        def interrupted(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('simulated interrupted move')
            return rename(source, target)
        with mock.patch.object(self.api.os, 'rename', side_effect=interrupted), self.assertRaises(ValueError):
            self.api.quarantine(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertEqual('partial', json.loads((dest / 'receipt.json').read_text())['status'])
        self.assertTrue((dest / 'files/sample.txt').exists())
        self.assertTrue((self.root / 'second.txt').exists())
        self.api.restore(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertTrue(self.api.verify(self.root, plan)['ok'])

    def test_archive_io_failure_keeps_original_and_partial_receipt(self):
        plan = self.plan()
        dest = self.base / 'archive'
        with mock.patch.object(self.api.shutil, 'copyfileobj', side_effect=OSError('simulated I/O error')):
            with self.assertRaises(ValueError):
                self.api.archive(self.root, plan, dest)
        self.assertTrue(self.api.verify(self.root, plan)['ok'])
        self.assertEqual('partial', json.loads((dest / 'receipt.json').read_text())['status'])

    def test_restored_directory_does_not_reauthorize_fresh_files(self):
        plan = self.plan()
        dest = self.base / 'quarantine'
        self.api.quarantine(self.root, plan, dest, self.api.plan_digest(plan))
        (dest / 'files/sample.txt').write_text('changed quarantined file')
        with self.assertRaises(ValueError):
            self.api.restore(self.root, plan, dest, self.api.plan_digest(plan))
        self.assertFalse((self.root / 'sample.txt').exists())
        self.assertEqual('changed quarantined file', (dest / 'files/sample.txt').read_text())

    def test_moved_project_can_rebind_with_original_absent(self):
        plan = self.plan()
        moved = self.base / 'moved project'
        self.root.rename(moved)
        self.assertTrue(self.api.verify(moved, plan)['ok'])
        self.assertFalse(self.root.exists())

    def test_destination_inside_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.archive(self.root, self.plan(), self.root / 'archive')

    def test_bad_schema_and_string_flags_cannot_be_cleanup_candidates(self):
        plan = self.plan(unique='false')
        self.assertEqual([], self.api.cleanup_plan(self.root, plan)['candidates'])
        plan['schema_version'] = 999
        with self.assertRaises(ValueError):
            self.api.verify(self.root, plan)

    def test_root_and_json_parent_symlinks_are_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api.make_plan(alias / 'project', 'p', 't', [self.metadata()])
        spec = self.base / 'spec.json'
        spec.write_text('{}')
        with self.assertRaises(ValueError):
            self.api.load_json(alias / 'spec.json')

    def test_installed_script_and_guide_are_self_contained(self):
        installed = self.base / 'installed' / 'tools'
        installed.mkdir(parents=True)
        shutil.copyfile(SCRIPT, installed / 'lifecycle.py')
        shutil.copyfile(SCRIPT.with_suffix('.md'), installed / 'lifecycle.md')
        spec = self.base / 'spec.json'
        spec.write_text(json.dumps({'project_id': 'p', 'task_id': 't', 'files': [self.metadata()]}))
        result = subprocess.run([sys.executable, str(installed / 'lifecycle.py'), 'plan',
                                 '--root', str(self.root), '--spec', str(spec),
                                 '--out', str(self.base / 'standalone-plan.json')],
                                cwd=installed, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_cli_plan_verify_and_invalid_json(self):
        spec = self.base / 'spec.json'
        spec.write_text(json.dumps({'project_id': 'p', 'task_id': 't', 'files': [self.metadata()]}))
        out = self.base / 'plan.json'
        result = subprocess.run([sys.executable, str(SCRIPT), 'plan', '--root', str(self.root),
                                 '--spec', str(spec), '--out', str(out)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPT), 'verify', '--root', str(self.root),
                                 '--plan', str(out)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        out.write_text('{broken')
        result = subprocess.run([sys.executable, str(SCRIPT), 'verify', '--root', str(self.root),
                                 '--plan', str(out)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('error', json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
