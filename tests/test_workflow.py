"""行为测试使用独立临时工程，原始状态/失败证据不可被后续成功覆盖。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'plugins/hutools/tools/workflow.py'
spec = importlib.util.spec_from_file_location('hutools_workflow', MODULE)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hutools-workflow-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        (self.root / 'source.txt').write_text('v1')
        (self.root / 'rules.txt').write_text('rules1')
        (self.root / 'check.py').write_text('print("verified")\n')
        self.path = self.root / 'records/task.json'
        self.definition = {
            'task_id': 'T1', 'rules_version': 'B02', 'source_version': 'working-tree',
            'author': 'engineer', 'requirements': ['R1'],
            'inputs': ['source.txt', 'rules.txt', 'check.py'], 'writable_paths': ['records'],
            'cases': [{'id': 'C1', 'requirement': 'R1', 'kind': 'command',
                       'argv': [sys.executable, '-B', 'check.py'], 'timeout_seconds': 3},
                      {'id': 'REV', 'requirement': 'R1', 'kind': 'review'}],
        }
        w.create(self.root, self.path, self.definition)

    def start(self):
        w.approve(self.path, 'user', 'B02 approved in user message')
        w.transition(self.path, 'running', 'approved task started')

    def review(self, outcome='pass'):
        (self.root / 'records/review.md').write_text('Reviewed behavior and evidence')
        w.record_evidence(self.path, 'REV', 'reviewer', 'records/review.md', outcome, 'Independent review completed')

    def test_unapproved_cannot_run_and_no_evidence_cannot_complete(self):
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'running', 'not approved')
        self.start()
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'complete', 'no evidence')

    def test_command_success_and_independent_review_complete(self):
        self.start()
        result = w.run_check(self.path, 'C1')
        self.assertEqual('pass', result['outcome'])
        self.assertEqual('running', w.load_state(self.path)['status'])
        self.review()
        w.transition(self.path, 'complete', 'all cases verified')
        self.assertEqual([], w.completion_errors(self.path))
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'complete', 'duplicate')

    def test_failure_is_retained_after_fix_and_retest(self):
        (self.root / 'check.py').write_text('raise SystemExit(4)\n')
        self.start()
        self.assertEqual('fail', w.run_check(self.path, 'C1')['outcome'])
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'complete', 'failure')
        (self.root / 'check.py').write_text('print("fixed")\n')
        self.assertEqual('pass', w.run_check(self.path, 'C1')['outcome'])
        self.review()
        w.transition(self.path, 'complete', 'fixed and reviewed')
        evidence = w.load_state(self.path)['evidence']
        self.assertEqual(['fail', 'pass', 'pass'], [e['outcome'] for e in evidence])

    def test_changed_input_invalidates_previous_evidence(self):
        self.start()
        w.run_check(self.path, 'C1')
        self.review()
        (self.root / 'source.txt').write_text('changed')
        self.assertTrue(w.completion_errors(self.path))
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'complete', 'stale evidence')

    def test_changed_spec_invalidates_approval(self):
        self.start()
        state = json.loads(self.path.read_text())
        state['spec']['cases'][0]['argv'] = [sys.executable, '-c', 'print("changed")']
        self.path.write_text(json.dumps(state))
        with self.assertRaises(w.WorkflowError):
            w.run_check(self.path, 'C1')

    def test_command_evidence_cannot_be_filled_by_manual_pass(self):
        self.start()
        (self.root / 'records/fake.md').write_text('pass')
        with self.assertRaises(w.WorkflowError):
            w.record_evidence(self.path, 'C1', 'engineer', 'records/fake.md', 'pass', 'self reported')

    def test_review_cannot_be_authors_own_attestation(self):
        self.start()
        (self.root / 'records/review.md').write_text('review')
        with self.assertRaises(w.WorkflowError):
            w.record_evidence(self.path, 'REV', 'engineer', 'records/review.md', 'pass', 'self review')

    def test_artifact_tampering_invalidates_evidence(self):
        self.start()
        result = w.run_check(self.path, 'C1')
        self.review()
        (self.root / result['artifact']).write_text('tampered')
        self.assertTrue(w.completion_errors(self.path))

    def test_migration_original_absent_and_unrelated_cwd(self):
        self.start()
        w.run_check(self.path, 'C1')
        self.review()
        moved = self.root.with_name('新目录 with spaces')
        self.root.rename(moved)
        newstate = moved / 'records/task.json'
        result = subprocess.run([sys.executable, '-B', str(MODULE), 'status', '--state', str(newstate)],
                                cwd=self.tmp.name, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], w.completion_errors(newstate))
        w.transition(newstate, 'complete', 'migrated')
        self.assertFalse(self.root.exists())

    def test_pause_blocks_checks_resume_and_failed_restart(self):
        self.start()
        w.transition(self.path, 'paused', 'observer requested stop')
        with self.assertRaises(w.WorkflowError):
            w.run_check(self.path, 'C1')
        w.transition(self.path, 'running', 'cause resolved')
        w.transition(self.path, 'failed', 'known failure')
        w.transition(self.path, 'running', 'fix approved within same scope')
        self.assertEqual('running', w.load_state(self.path)['status'])

    def test_timeout_and_output_limit_are_failures(self):
        state = json.loads(self.path.read_text())
        state['spec']['cases'][0]['argv'] = [sys.executable, '-c', 'import time;time.sleep(5)']
        state['spec']['cases'][0]['timeout_seconds'] = 0.1
        self.path.write_text(json.dumps(state))
        self.start()
        result = w.run_check(self.path, 'C1')
        self.assertEqual('fail', result['outcome'])
        self.assertEqual('timeout', result['reason'])

    def test_symlink_and_escape_rejected(self):
        self.start()
        (self.root / 'source.txt').unlink()
        (self.root / 'source.txt').symlink_to(Path(self.tmp.name) / 'outside.txt')
        with self.assertRaises(w.WorkflowError):
            w.run_check(self.path, 'C1')
        other = self.root / 'other/task.json'
        bad = dict(self.definition, inputs=['../outside.txt'])
        with self.assertRaises(w.WorkflowError):
            w.create(self.root, other, bad)

    def test_corrupt_json_and_existing_lock_fail_clearly(self):
        lock = self.path.with_name(self.path.name + '.lock')
        lock.write_text('another owner')
        with self.assertRaisesRegex(w.WorkflowError, '锁'):
            w.approve(self.path, 'user', 'approved')
        lock.unlink()
        self.path.write_text('{bad')
        with self.assertRaises(w.WorkflowError):
            w.load_state(self.path)

    def test_missing_requirement_case_rejected(self):
        bad = dict(self.definition, requirements=['R1', 'R2'])
        with self.assertRaises(w.WorkflowError):
            w.create(self.root, self.root / 'other/task.json', bad)


    def test_output_limit_rejects_noisy_command(self):
        state = json.loads(self.path.read_text())
        state['spec']['cases'][0]['argv'] = [sys.executable, '-c', 'print("x" * 2000000)']
        self.path.write_text(json.dumps(state))
        self.start()
        result = w.run_check(self.path, 'C1')
        self.assertEqual('fail', result['outcome'])
        self.assertEqual('output_limit', result['reason'])
        self.assertLessEqual((self.root / result['artifact']).stat().st_size, w.MAX_OUTPUT_BYTES)

    def test_check_modifying_inputs_cannot_pass(self):
        (self.root / 'check.py').write_text('from pathlib import Path;Path("source.txt").write_text("new")\n')
        self.start()
        result = w.run_check(self.path, 'C1')
        self.assertEqual('fail', result['outcome'])
        self.assertEqual('inputs_changed', result['reason'])

    def test_command_modifying_state_is_not_overwritten(self):
        (self.root / 'check.py').write_text('from pathlib import Path;Path("records/task.json").write_text("{bad")\n')
        self.start()
        with self.assertRaisesRegex(w.WorkflowError, '输出保留'):
            w.run_check(self.path, 'C1')
        self.assertEqual('{bad', self.path.read_text())
        self.assertEqual(1, len(list((self.root / 'records/task-evidence').glob('*.log'))))

    def test_live_process_lock_rejects_competing_writer(self):
        with w.locked(self.path):
            result = subprocess.run([sys.executable, '-B', str(MODULE), 'approve', '--state', str(self.path),
                                     '--actor', 'user', '--decision', 'approved'], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn('锁', result.stderr)
        self.assertEqual('planned', w.load_state(self.path)['status'])

    def test_independent_review_requirement_cannot_be_omitted(self):
        bad = dict(self.definition, requires_independent_review=True, cases=self.definition['cases'][:1])
        with self.assertRaises(w.WorkflowError):
            w.create(self.root, self.root / 'other/task.json', bad)



    def test_observation_fragments_share_state_and_preserve_parallel_times(self):
        w.observe(self.path, 'developer', 'worker-1', 'work', '2026-11-17T10:00:00+08:00',
                  '2026-11-17T10:03:00+08:00', 'implementation')
        w.observe(self.path, 'tester', 'worker-2', 'work', '2026-11-17T10:01:00+08:00',
                  '2026-11-17T10:02:00+08:00', 'parallel inspection')
        fragments = [e for e in w.load_state(self.path)['events'] if e['type'] == 'fragment']
        self.assertEqual(2, len(fragments))
        text = w.report(self.path)
        self.assertIn('worker-1', text)
        self.assertIn('不能相加', text)
        with self.assertRaises(w.WorkflowError):
            w.observe(self.path, 'tester', 'worker-2', 'retry', '2026-11-17T10:02:00+08:00',
                      '2026-11-17T10:01:00+08:00', 'reversed')
        with self.assertRaises(w.WorkflowError):
            w.observe(self.path, 'tester', 'worker-2', 'retry', '2026-11-17T10:02:00',
                      '2026-11-17T10:03:00', 'missing timezone')



    def test_cli_full_delivery_flow(self):
        state_path = self.root / 'records/cli.json'
        spec_path = self.root / 'spec.json'
        spec_path.write_text(json.dumps(self.definition))
        def cli(*args):
            result = subprocess.run([sys.executable, '-B', str(MODULE), *args],
                                    cwd=self.tmp.name, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            return result.stdout
        cli('create', '--root', str(self.root), '--state', str(state_path), '--spec', str(spec_path))
        cli('approve', '--state', str(state_path), '--actor', 'user', '--decision', 'approved scope')
        cli('transition', '--state', str(state_path), '--to', 'running', '--reason', 'start')
        cli('run-check', '--state', str(state_path), '--case', 'C1')
        (self.root / 'records/cli-review.md').write_text('Independent acceptance review')
        cli('evidence', '--state', str(state_path), '--case', 'REV', '--actor', 'reviewer',
            '--artifact', 'records/cli-review.md', '--outcome', 'pass', '--statement', 'review complete')
        cli('observe', '--state', str(state_path), '--role', 'tester', '--actor', 'worker', '--kind', 'work',
            '--started-at', '2026-11-17T10:00:00+08:00', '--ended-at', '2026-11-17T10:01:00+08:00', '--reason', 'actual inspection')
        cli('transition', '--state', str(state_path), '--to', 'complete', '--reason', 'done')
        status = json.loads(cli('status', '--state', str(state_path)))
        self.assertEqual('complete', status['effective_status'])
        self.assertIn('当前交付检查：通过', cli('report', '--state', str(state_path)))
        (self.root / 'source.txt').write_text('later change')
        status = json.loads(cli('status', '--state', str(state_path)))
        self.assertEqual('needs_revalidation', status['effective_status'])
        report = cli('report', '--state', str(state_path))
        self.assertIn('状态：' + status['effective_status'], report)
        self.assertIn('最近登记结果（历史）', report)
        self.assertEqual('complete', status['state']['status'])

    def test_relative_state_cli_records_exactly_one_execution(self):
        (self.root / 'check.py').write_text(
            "with open('count', 'a') as counter:\n    counter.write('1')\nprint('checked')\n")
        self.start()
        result = subprocess.run([sys.executable, '-B', str(MODULE), 'run-check',
                                 '--state', 'records/task.json', '--case', 'C1'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual('1', (self.root / 'count').read_text())
        self.assertEqual(0, result.returncode, result.stderr)
        evidence, = w.load_state(self.path)['evidence']
        self.assertEqual('pass', evidence['outcome'])
        log = Path(evidence['artifact'])
        self.assertFalse(log.is_absolute())
        self.assertEqual('checked\n', (self.root / log).read_text())

    def test_observe_cli_displays_result_and_evidence_alongside_legacy_fragment(self):
        start = '2026-11-17T10:00:00+08:00'
        end = '2026-11-17T10:01:00+08:00'
        old = w.observe(self.path, 'tester', 'legacy', 'work', start, end, 'original call')
        self.assertNotIn('result', old)
        artifact = 'records/sample|1.md'
        (self.root / artifact).write_text('observed')
        outcome = '复核通过 | 1\n个样例'
        result = subprocess.run([sys.executable, '-B', str(MODULE), 'observe',
                                 '--state', 'records/task.json', '--role', 'tester', '--actor', 'observer',
                                 '--kind', 'work', '--started-at', start, '--ended-at', end,
                                 '--reason', '复核', '--result', outcome, '--artifact', artifact],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        details = w.load_state(self.path)['events'][-1]
        self.assertEqual(outcome, details['result'])
        self.assertEqual(artifact, details['artifact'])
        lines = w.report(self.path).splitlines()
        self.assertIn('未登记', next(line for line in lines if 'legacy' in line))
        row = next(line for line in lines if 'observer' in line)
        self.assertIn(r'复核通过 \| 1', row)
        self.assertIn('个样例', row)
        self.assertIn(r'records/sample\|1.md', row)



    def _empty_directory_change_invalidates(self, change):
        (self.root / 'src/required').mkdir(parents=True)
        (self.root / 'check.py').write_text(
            'from pathlib import Path\n'
            'assert sorted(p.name for p in Path("src").iterdir()) == ["required"]\n'
            'assert Path("src/required").is_dir()\n')
        state = json.loads(self.path.read_text())
        state['spec']['inputs'] = ['src', 'check.py']
        self.path.write_text(json.dumps(state))
        self.start()
        self.assertEqual('pass', w.run_check(self.path, 'C1')['outcome'])
        self.review()
        if change == 'delete':
            (self.root / 'src/required').rmdir()
        elif change == 'add':
            (self.root / 'src/new-empty').mkdir()
        else:
            (self.root / 'src/required').rename(self.root / 'src/renamed')
        actual = subprocess.run([sys.executable, '-B', 'check.py'], cwd=self.root,
                                capture_output=True, text=True)
        self.assertNotEqual(0, actual.returncode)
        self.assertTrue(w.completion_errors(self.path), 'empty directory change must invalidate evidence')
        with self.assertRaises(w.WorkflowError):
            w.transition(self.path, 'complete', 'must reject stale directory evidence')

    def test_deleted_empty_child_directory_invalidates_evidence(self):
        self._empty_directory_change_invalidates('delete')

    def test_added_empty_child_directory_invalidates_evidence(self):
        self._empty_directory_change_invalidates('add')

    def test_renamed_empty_child_directory_invalidates_evidence(self):
        self._empty_directory_change_invalidates('rename')


if __name__ == '__main__':
    unittest.main()
