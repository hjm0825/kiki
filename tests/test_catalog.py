"""以实际 CLI 进程验证双环整理的持久化、缺口决定和完成边界。"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/hutools/tools/catalog.py'


class CatalogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='hutools-catalog-test-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.library = self.base / 'library'
        self.library.mkdir()
        self.state = self.root / 'catalog.json'
        self.scopes = ('manual=project:manual', 'archive=library:batch')
        (self.root / 'manual').mkdir()
        (self.library / 'batch').mkdir()
        (self.library / 'batch/archive.txt').write_text('archive batch 01')
        for name in ('manual', 'archive', 'extra'):
            (self.root / f'{name}-outer.md').write_text(f'[{name}]({name}-inner.md)')
            (self.root / f'{name}-inner.md').write_text(f'[盘点]({name}-inventory.md)')
            (self.root / f'{name}-inventory.md').write_text(f'{name} 资料盘点')

    def cli(self, *args, library=None, success=True):
        # 从异目录以新进程启动，检查路径基准与跨会话状态。
        result = subprocess.run(
            [sys.executable, '-B', str(SCRIPT), '--state', str(self.state),
             '--root', str(self.root), '--library-root', str(library or self.library),
             *args], cwd=self.base, capture_output=True, text=True, timeout=10)
        if not success:
            self.assertNotEqual(0, result.returncode, result.stdout)
            return
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def begin(self, scopes=None, rebind=False, **kwargs):
        args = ['begin', '--project', 'routing-sample', '--rules', 'v1']
        if rebind:
            args.append('--rebind-library')
        for scope in self.scopes if scopes is None else scopes:
            args.extend(('--scope', scope))
        return self.cli(*args, **kwargs)

    def review(self, scope, **kwargs):
        return self.cli('review', '--scope', scope,
                        '--outer', f'{scope}-outer.md',
                        '--inner', f'{scope}-inner.md',
                        '--inventory', f'{scope}-inventory.md', **kwargs)

    def add_gap(self, gap, finding):
        return self.cli('gap', '--scope', 'archive', '--id', gap,
                        '--item', f'batch/{gap}.bag', '--finding', finding,
                        '--impact', '无法复核原始路线')

    def close(self, gap, *args, **kwargs):
        return self.cli('close', '--scope', 'archive', '--id', gap, *args, **kwargs)

    def test_physical_layout_migration_review_and_repeat(self):
        self.begin()
        self.cli('begin', '--project', 'routing-sample', '--rules', 'v1',
                 '--scope', self.scopes[0], '--scope', self.scopes[1], '--layout-scope', 'manual')
        old = self.root / 'manual' / 'design.md'
        old.write_text('# 原始需求')
        home = self.root / 'manual' / 'README.md'
        home.write_text('[设计](design.md) · [阶段](外环/阶段/A/结果.md)')
        outer = 'manual/外环/阶段/A/结果.md'
        inner = 'manual/内环/阶段/A/设计.md'
        plan = self.base / 'plan.json'
        doc = dict(directory='manual', keep=['manual/README.md'],
                   moves={'manual/design.md': inner},
                   pages={outer: '[依据](../../../内环/阶段/A/设计.md)'},
                   pairs=[[outer, inner]])
        plan.write_text(json.dumps(doc))
        args = ['arrange', '--scope', 'manual', '--plan', str(plan)]
        self.review('manual', success=False)
        self.assertIn('manual/design.md', self.cli('inspect', '--scope', 'manual')['files'])
        self.assertFalse(self.cli(*args)['changed'])
        self.assertTrue(old.exists())
        self.cli(*args, '--apply', success=False)
        recovery = self.base / 'backup.zip'
        apply = args + ['--apply', '--recovery', str(recovery)]
        before_apply = self.state.read_bytes()
        self.assertTrue(self.cli(*apply)['changed'])
        self.state.write_bytes(before_apply)
        self.assertTrue(self.cli(*apply)['changed'])
        self.assertEqual((self.root / inner).read_text(), '# 原始需求')
        self.assertEqual(home.read_text(), '[设计](内环/阶段/A/设计.md) · [阶段](外环/阶段/A/结果.md)')
        self.assertFalse(old.exists())
        self.assertTrue(recovery.is_file())
        self.assertFalse(self.cli(*apply)['changed'])
        self.assertTrue(self.review('manual')['scopes']['manual']['complete'])
        extra = dict(doc, moves={}, pages={}, edits={'manual/README.md': [['[设计]', '[技术设计]']]})
        extra_plan = self.base / 'extra.json'
        extra_plan.write_text(json.dumps(extra))
        next_batch = self.cli('arrange', '--scope', 'manual', '--plan', str(extra_plan),
                              '--apply', '--recovery', str(self.base / 'extra.zip'))
        self.assertIn(inner, next_batch['targets'])
        self.assertFalse(self.cli(*apply)['changed'])
        old.write_text('新的待归位资料')
        self.assertFalse(self.cli('status')['scopes']['manual']['complete'])
        self.cli(*apply, success=False)
        self.assertEqual(old.read_text(), '新的待归位资料')

    def test_restart_resumes_and_task_updates_leave_other_reviews_intact(self):
        blank = self.cli('status')
        self.assertFalse(blank['complete'])
        self.assertEqual('full', blank['mode'])
        self.begin()
        self.review('manual')
        resume = self.cli('status')
        self.assertEqual('resume', resume['mode'])
        self.assertFalse(resume['complete'])
        self.assertTrue(resume['scopes']['manual']['reviewed'])
        self.assertFalse(resume['scopes']['archive']['reviewed'])
        self.review('archive')
        after = self.begin()
        self.assertTrue(after['complete'])
        self.assertEqual('incremental', after['mode'])
        before_archive = after['scopes']['archive']
        working = self.cli('touch', '--scope', 'manual', '--reason', 'R1 阶段修改')
        self.assertFalse(working['complete'])
        self.assertFalse(working['scopes']['manual']['reviewed'])
        self.assertEqual(before_archive, working['scopes']['archive'])
        self.review('manual')
        self.assertEqual(before_archive, self.cli('status')['scopes']['archive'])

    def test_user_closure_and_verified_repair_are_independent_and_persistent(self):
        self.begin()
        self.add_gap('encrypted', '原件加密，内容未核验')
        self.add_gap('missing', '原始路线文件缺失')
        self.review('manual')
        self.review('archive')
        for _ in range(2):
            pending = self.begin()
            self.assertFalse(pending['complete'])
            self.assertCountEqual(['encrypted', 'missing'],
                                  pending['scopes']['archive']['open_gaps'])
        self.close('encrypted', '--accepted', '缺少明确答复依据', success=False)
        self.close('encrypted', '--accepted', '我同意仅此加密录包结束修复',
                   '--reference', '试验用户答复记录 A')
        one = self.cli('status')
        self.assertFalse(one['complete'])
        archive = one['scopes']['archive']
        self.assertEqual(['missing'], archive['open_gaps'])
        encrypted = json.dumps(archive['gaps']['encrypted'], ensure_ascii=False)
        for text in ('原件加密，内容未核验', '无法复核原始路线', '仅此加密录包结束修复',
                     '试验用户答复记录 A'):
            self.assertIn(text, encrypted)
        self.close('missing', '--fixed', 'missing-source.txt', success=False)
        self.assertEqual(['missing'], self.cli('status')['scopes']['archive']['open_gaps'])
        (self.root / 'verification.md').write_text('恢复的资料已与原批次身份核验')
        self.close('missing', '--fixed', 'verification.md')
        done = self.begin()
        self.assertTrue(done['complete'])
        self.assertEqual([], done['scopes']['archive']['open_gaps'])
        fixed = json.dumps(done['scopes']['archive']['gaps']['missing'], ensure_ascii=False)
        self.assertIn('verification.md', fixed)
        self.assertIn('原始路线文件缺失', fixed)
        self.assertEqual(archive['gaps']['encrypted'], done['scopes']['archive']['gaps']['encrypted'])
        duplicate = self.add_gap('encrypted', '原件加密，内容未核验')
        self.assertTrue(duplicate['complete'])
        self.assertEqual(archive['gaps']['encrypted'], duplicate['scopes']['archive']['gaps']['encrypted'])

    def test_scope_expansion_and_library_rebinding_only_reset_affected_reviews(self):
        self.begin()
        self.review('manual')
        self.review('archive')
        (self.root / 'extra').mkdir()
        scopes = self.scopes + ('extra=project:extra',)
        larger = self.begin(scopes)
        self.assertFalse(larger['complete'])
        self.assertFalse(larger['scopes']['extra']['reviewed'])
        self.assertTrue(larger['scopes']['manual']['reviewed'])
        self.assertTrue(larger['scopes']['archive']['reviewed'])
        self.review('extra')
        second = self.base / 'library-2'
        (second / 'batch').mkdir(parents=True)
        rebound = self.begin(scopes, library=second)
        self.assertFalse(rebound['complete'])
        self.assertTrue(rebound['scopes']['manual']['reviewed'])
        self.assertTrue(rebound['scopes']['extra']['reviewed'])
        self.assertFalse(rebound['scopes']['archive']['reviewed'])
        self.assertTrue(self.review('archive', library=second)['complete'])
        self.assertNotIn(str(second), self.state.read_text())

    def test_confirmed_same_path_volume_change_retains_old_decision_only_as_history(self):
        self.begin()
        self.review('manual')
        self.review('archive')
        self.add_gap('encrypted', '旧卷录包加密')
        self.close('encrypted', '--accepted', '旧卷这一份不再修',
                   '--reference', '旧卷审核决定 A')
        manual = self.cli('status')['scopes']['manual']
        self.library.rename(self.base / 'previous-volume')
        (self.library / 'batch').mkdir(parents=True)
        (self.library / 'batch/archive.txt').write_text('new volume data')
        changed = self.begin(rebind=True)
        self.assertFalse(changed['complete'])
        self.assertEqual(manual, changed['scopes']['manual'])
        self.assertFalse(changed['scopes']['archive']['reviewed'])
        self.assertNotIn('encrypted', changed['scopes']['archive']['gaps'])
        state = json.loads(self.state.read_text())
        history = json.dumps(state['scopes']['archive']['history'], ensure_ascii=False)
        for retained in ('旧卷录包加密', '旧卷这一份不再修', '旧卷审核决定 A'):
            self.assertIn(retained, history)
        self.add_gap('encrypted', '新卷同名录包仍待用户决定')
        self.review('archive')
        pending = self.begin()
        self.assertFalse(pending['complete'])
        self.assertEqual(['encrypted'], pending['scopes']['archive']['open_gaps'])

    def test_rebound_offline_libraries_keep_distinct_unresolved_gaps(self):
        (self.library / 'batch').rename(self.library / 'batch-offline')
        before = self.begin()
        old_scope = before['scopes']['archive']
        self.assertCountEqual(['scope-unavailable'], old_scope['open_gaps'])
        self.review('manual')
        new_library = self.base / 'another-library'
        new_library.mkdir()
        rebound = self.begin(library=new_library)
        gaps = rebound['scopes']['archive']
        self.assertCountEqual(['scope-unavailable', 'scope-unavailable@2'], gaps['open_gaps'])
        self.assertNotEqual(gaps['gaps']['scope-unavailable']['source_generation'],
                            gaps['gaps']['scope-unavailable@2']['source_generation'])
        self.review('archive', library=new_library)
        self.close('scope-unavailable', '--accepted', '仅关闭原资料库缺失项',
                   '--reference', '旧库确认 1', library=new_library)
        remaining = self.begin(library=new_library)
        self.assertFalse(remaining['complete'])
        self.assertEqual(['scope-unavailable@2'], remaining['scopes']['archive']['open_gaps'])
        (new_library / 'batch').mkdir()
        (self.root / 'new-volume-check.md').write_text('已独立核验新库批次')
        fixed = self.close('scope-unavailable@2', '--fixed', 'new-volume-check.md',
                           library=new_library)
        self.assertTrue(fixed['complete'])

    def test_reported_recurrence_reopens_fixed_gap_and_retains_old_repair(self):
        self.begin()
        self.review('manual')
        self.review('archive')
        self.add_gap('sample', '路线样本无法读取')
        (self.root / 'first-repair.md').write_text('第一次恢复曾通过核验')
        self.close('sample', '--fixed', 'first-repair.md')
        self.assertTrue(self.begin()['complete'])
        again = self.add_gap('sample', '路线样本无法读取')
        self.assertFalse(again['complete'])
        self.assertEqual(['sample'], again['scopes']['archive']['open_gaps'])
        stored = json.loads(self.state.read_text())
        history = json.dumps(stored['scopes']['archive']['history'], ensure_ascii=False)
        self.assertIn('first-repair.md', history)
        self.assertFalse(self.begin()['complete'])
        (self.root / 'second-repair.md').write_text('第二次恢复，重新比对批次')
        repaired = self.close('sample', '--fixed', 'second-repair.md')
        self.assertTrue(repaired['complete'])

    def test_absent_library_allows_unrelated_work_and_restoration(self):
        (self.library / 'batch').rename(self.library / 'batch-offline')
        self.begin()
        self.review('manual')
        status = self.cli('status')
        self.assertFalse(status['complete'])
        self.assertTrue(status['scopes']['manual']['reviewed'])
        self.assertIn('scope-unavailable', status['scopes']['archive']['open_gaps'])
        (self.library / 'batch-offline').rename(self.library / 'batch')
        previously_unavailable = self.begin()
        self.assertIn('scope-unavailable', previously_unavailable['scopes']['archive']['open_gaps'])
        (self.root / 'library-check.md').write_text('已重新读取范围内资料并复核批次身份')
        self.close('scope-unavailable', '--fixed', 'library-check.md')
        self.assertTrue(self.review('archive')['complete'])

    def test_invalid_evidence_or_scope_removal_cannot_overwrite_progress(self):
        self.begin()
        self.review('manual')
        before = self.state.read_bytes()
        self.cli('review', '--scope', 'archive', '--outer', 'absent.md',
                 '--inner', 'archive-inner.md', '--inventory', 'archive-inventory.md',
                 success=False)
        self.assertEqual(before, self.state.read_bytes())
        self.begin((self.scopes[0],), success=False)
        self.assertEqual(before, self.state.read_bytes())
        self.assertTrue(self.review('archive')['complete'])
        (self.root / 'archive-inner.md').unlink()
        stale = self.cli('status')
        self.assertFalse(stale['complete'])
        self.assertTrue(stale['scopes']['manual']['reviewed'])

    def test_parallel_independent_gaps_are_not_lost(self):
        self.begin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            writes = [pool.submit(self.add_gap, gap, '待复核') for gap in ('one', 'two')]
            for write in writes:
                write.result()
        self.assertCountEqual(['one', 'two'],
                              self.cli('status')['scopes']['archive']['open_gaps'])


class CatalogScaffoldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hutools-rings-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.docs = self.root / 'docs'

    def scaffold(self, success=True):
        result = subprocess.run(
            [sys.executable, '-B', str(SCRIPT.with_name('catalog_scaffold.py')),
             '--root', str(self.root), '--directory', 'docs'],
            cwd=self.base, capture_output=True, text=True, timeout=10)
        # 失败不能仅由工具尚未安装造成。
        self.assertTrue(SCRIPT.with_name('catalog_scaffold.py').is_file(),
                        '缺少双环目录初始化工具')
        if success:
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        else:
            self.assertNotEqual(0, result.returncode, result.stdout)

    def test_new_project_gets_real_ring_directories_without_fake_work(self):
        self.scaffold()
        for ring, sections in [('外环', ('阶段', '模块')),
                               ('内环', ('阶段', '模块', '数据'))]:
            for section in sections:
                path = self.docs / ring / section
                self.assertTrue(path.is_dir(), path)
                self.assertFalse(any(item.is_dir() for item in path.iterdir()), path)
        navigation = self.docs / 'README.md'
        self.assertTrue(navigation.is_file())
        self.assertIn('外环', navigation.read_text())
        self.assertIn('内环', navigation.read_text())
        self.assertFalse(list(self.root.rglob('catalog-state.json')))
        own_navigation = navigation.read_bytes() + b'\nUser notes\n'
        navigation.write_bytes(own_navigation)
        existing = self.docs / '外环/模块/existing.md'
        existing.write_text('已有用户资料')
        self.scaffold()
        self.assertEqual(own_navigation, navigation.read_bytes())
        self.assertEqual('已有用户资料', existing.read_text())

    def test_conflicting_regular_file_is_preserved(self):
        self.docs.mkdir()
        conflict = self.docs / '内环'
        conflict.write_text('此前已存在的普通文件')
        self.scaffold(success=False)
        self.assertEqual('此前已存在的普通文件', conflict.read_text())

    def test_does_not_follow_symlinks_when_creating_ring_structure(self):
        self.docs.mkdir()
        external = self.base / 'separate-docs'
        external.mkdir()
        self.docs.joinpath('外环').symlink_to(external, target_is_directory=True)
        self.scaffold(success=False)
        self.assertTrue((self.docs / '外环').is_symlink())
        self.assertEqual([], list(external.iterdir()))


if __name__ == '__main__':
    unittest.main()
