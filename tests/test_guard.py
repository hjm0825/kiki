"""受控调用保护行为；并不替代宿主加载与 OS 隔离验证。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'plugins/hutools/hooks/guard.py'

class GuardTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('guard_under_test', GUARD)
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)
        self.tmp = tempfile.TemporaryDirectory(prefix='hutools-guard-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        (self.root / 'src').mkdir()
        self.policy = {'schema_version': 1, 'project_root': str(self.root),
            'batch_id': 'B02-test', 'decision_ref': 'isolated-test-confirmation',
            'mode': 'approved', 'write_paths': ['src/a.py'], 'delete_paths': [],
            'approved_calls': [], 'rules_sha256': self.g.rules_digest(),
            'expires_at': '2099-01-01T00:00:00+00:00'}
    def call(self, name='apply_patch', inp=None, cwd=None):
        return {'hook_event_name':'PreToolUse', 'tool_name': name,
            'tool_input': inp or {'command':'*** Begin Patch\n*** Add File: src/a.py\n+x = 1\n*** End Patch'},
            'cwd': str(cwd or self.root), 'session_id':'test'}
    def denied(self, event, policy=None):
        result = self.g.evaluate(event, self.policy if policy is None else policy)
        return result.get('hookSpecificOutput',{}).get('permissionDecision') == 'deny'
    def test_approved_patch_passes_without_granting_host_permission(self):
        self.assertFalse(self.denied(self.call()))
        self.assertNotIn('permissionDecision', self.g.evaluate(self.call(), self.policy).get('hookSpecificOutput',{}))
    def test_unapproved_path_and_delete_are_denied(self):
        for command in ['*** Begin Patch\n*** Add File: ../outside\n+x\n*** End Patch',
                        '*** Begin Patch\n*** Delete File: src/a.py\n*** End Patch']:
            self.assertTrue(self.denied(self.call(inp={'command':command})))
    def test_move_destination_is_checked(self):
        self.assertTrue(self.denied(self.call(inp={'command':'*** Begin Patch\n*** Update File: src/a.py\n*** Move to: ../escaped\n@@\n-x\n+y\n*** End Patch'})))
    def test_move_requires_source_disposal_approval(self):
        self.policy['write_paths'].append('src/b.py')
        self.policy['delete_paths'] = ['src/b.py']
        e = self.call(inp={'command':'*** Begin Patch\n*** Update File: src/a.py\n*** Move to: src/b.py\n@@\n-x\n+y\n*** End Patch'})
        self.assertTrue(self.denied(e))
        self.policy['delete_paths'] = ['src/a.py']
        self.assertFalse(self.denied(e))

    def test_symlink_ancestor_denied(self):
        (self.root / 'src/a.py').symlink_to(Path(self.tmp.name)/'outside')
        self.assertTrue(self.denied(self.call()))
    def test_shell_exact_input_is_required(self):
        e = self.call('Bash', {'command':'python3 -m unittest','timeout_ms':1000})
        self.assertTrue(self.denied(e))
        self.policy['approved_calls']=[{'tool':'Bash','sha256':self.g.call_digest(e)}]
        self.assertFalse(self.denied(e))
        e['tool_input']['command'] += '; touch outside'
        self.assertTrue(self.denied(e))
    def test_unknown_tool_stdin_and_escalation_denied(self):
        for name, inp in [('write_stdin',{'session_id':1,'chars':'rm file\n'}),
            ('mcp__unknown__write',{'path':'src/a.py'}),
            ('Bash',{'command':'true','sandbox_permissions':'require_escalated'})]:
            self.assertTrue(self.denied(self.call(name,inp)))
    def test_stale_rules_expiry_and_paused_deny(self):
        for key,val in [('rules_sha256','0'*64),('expires_at','2000-01-01T00:00:00+00:00'),('mode','paused')]:
            p=dict(self.policy);p[key]=val
            self.assertTrue(self.denied(self.call(),p))
    def test_malformed_patch_and_policy_denied(self):
        self.assertTrue(self.denied(self.call(inp={'command':'not a patch'})))
        self.assertTrue(self.denied(self.call(),{'schema_version':99}))
    def test_invalid_policy_field_types_deny(self):
        for key,value in [('expires_at',None),('approved_calls',['bad']),('write_paths','src/a.py'),('batch_id',42)]:
            p=dict(self.policy);p[key]=value
            self.assertTrue(self.denied(self.call(),p))

    def test_no_binding_reports_inactive_not_enforced(self):
        result=self.g.evaluate(self.call(),None)
        self.assertIn('未绑定',json.dumps(result,ensure_ascii=False))
    def test_unrelated_project_is_not_affected(self):
        other=Path(self.tmp.name)/'other';other.mkdir()
        self.assertEqual({},self.g.evaluate(self.call(cwd=other),self.policy))
    def test_read_only_mode_does_not_permit_mutation(self):
        self.policy['mode']='read_only'
        self.assertTrue(self.denied(self.call()))
    def test_cli_bad_policy_types_emit_formal_denial(self):
        path=Path(self.tmp.name)/'policy.json'
        for key,value in [('expires_at',None),('approved_calls',['bad'])]:
            policy=dict(self.policy);policy[key]=value
            path.write_text(json.dumps(policy))
            r=subprocess.run([sys.executable,str(GUARD),'--policy',str(path)],input=json.dumps(self.call()),text=True,capture_output=True,timeout=10)
            self.assertEqual(0,r.returncode,r.stderr)
            self.assertEqual('deny',json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'])

    def test_stop_checks_real_workflow_evidence_and_source_version(self):
        spec=importlib.util.spec_from_file_location('workflow_for_guard',ROOT/'plugins/hutools/tools/workflow.py')
        w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
        (self.root/'src/a.py').write_text('x=1')
        state=self.root/'records/task.json'
        w.create(self.root,state,{'task_id':'fixture','rules_version':'v1','source_version':'fixture','author':'developer',
            'requirements':['R1'],'inputs':['src'],'writable_paths':['records'],
            'cases':[{'id':'C1','requirement':'R1','kind':'command','argv':[sys.executable,'-c','assert 2+2==4']} ]})
        w.approve(state,'user','B02 isolated test');w.transition(state,'running','test')
        self.policy['task_state']='records/task.json'
        event={'hook_event_name':'Stop','cwd':str(self.root)}
        self.assertEqual('block',self.g.evaluate(event,self.policy)['decision'])
        w.run_check(state,'C1');w.transition(state,'complete','evidence present')
        self.assertEqual({},self.g.evaluate(event,self.policy))
        (self.root/'src/a.py').write_text('x=2')
        self.assertEqual('block',self.g.evaluate(event,self.policy)['decision'])
        event['stop_hook_active']=True
        self.assertIs(False,self.g.evaluate(event,self.policy)['continue'])

    def test_policy_in_workspace_is_not_trusted(self):
        path=self.root/'policy.json';path.write_text(json.dumps(self.policy))
        with self.assertRaises(ValueError):self.g.load_policy(path)

if __name__=='__main__':unittest.main()
