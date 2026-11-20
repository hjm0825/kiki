#!/usr/bin/env python3
"""HuTools 调用检查。无自动信任、无权限提升、无文件写入；不是 OS 沙箱。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

PLUGIN = Path(__file__).resolve().parents[1]
MAX_INPUT = 1024 * 1024
INPUT_CARDS = {'request_user_input', 'request_user_input_async',
               'functions.request_user_input', 'functions.request_user_input_async'}


def rules_digest():
    return hashlib.sha256((PLUGIN / 'skills/hutools/references/rules.md').read_bytes()).hexdigest()


def call_digest(event):
    """精确批准绑定工具、完整参数和真实 cwd；不解析 shell 猜测副作用。"""
    value = {'tool': event['tool_name'], 'input': event['tool_input'],
             'cwd': str(Path(event['cwd']).resolve())}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':')).encode()).hexdigest()


def no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('路径含符号链接')


def beneath(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_policy(path):
    """绑定必须由用户维护在项目外；位置检查不证明 OS 已禁止 agent 写入。"""
    path = Path(path).absolute()
    no_links(path)
    if path.stat().st_size > MAX_INPUT:
        raise ValueError('绑定文件过大')
    policy = json.loads(path.read_text(encoding='utf-8'))
    root = Path(policy['project_root'])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('project_root 必须是存在的绝对目录')
    no_links(root)
    if beneath(path.resolve(), root.resolve()):
        raise ValueError('授权绑定不能放在待执行项目内')
    return policy


def deny(reason, event='PreToolUse'):
    if event == 'Stop':
        return {'decision': 'block', 'reason': reason}
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny', 'permissionDecisionReason': reason}}


def context(message, event='PreToolUse'):
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': message}}


def relative_file(raw, cwd, root):
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise ValueError('无效文件路径')
    p = Path(raw)
    if '..' in p.parts:
        raise ValueError('路径不能含 ..')
    p = p if p.is_absolute() else cwd / p
    no_links(p)
    resolved = p.resolve()
    if not beneath(resolved, root) or resolved == root:
        raise ValueError('目标不在批准工程内')
    rel = resolved.relative_to(root)
    if any(x in {'.git', '.codex', '.agents'} for x in rel.parts):
        raise ValueError('不能通过任务补丁修改控制配置')
    if p.exists() and (not p.is_file() or p.stat().st_nlink > 1):
        raise ValueError('目标不是独立普通文件')
    return rel.as_posix()


def patch_targets(command):
    if not isinstance(command, str):
        raise ValueError('补丁 command 必须是文本')
    lines = command.splitlines()
    if not lines or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise ValueError('只接受完整 apply_patch 格式')
    targets = []
    last = None
    for line in lines[1:-1]:
        matched = False
        for action in ('Add File', 'Update File', 'Delete File', 'Move to'):
            prefix = '*** ' + action + ': '
            if line.startswith(prefix):
                if action == 'Move to' and last != 'Update File':
                    raise ValueError('Move to 必须跟随 Update File')
                targets.append((action, line[len(prefix):]))
                last = action
                matched = True
                break
        if line.startswith('*** ') and not matched and line != '*** End of File':
            raise ValueError('未知补丁控制行')
    if not targets:
        raise ValueError('补丁没有目标文件')
    return targets


def evaluate(event, policy):
    name = event.get('hook_event_name', 'PreToolUse')
    if name not in {'PreToolUse', 'SessionStart', 'Stop'}:
        return {}
    if policy is None:
        inactive = 'HuTools 未绑定执行策略，保护未启用。高风险执行须完成宿主保护核验。'
        if name == 'SessionStart':
            return context(inactive + '用户选择 HuTools 并输入“运行”时，按入口技能调用宿主提问卡。', name)
        if name == 'PreToolUse' and event.get('tool_name') not in INPUT_CARDS:
            return context(inactive, name)
        return {}
    try:
        if not isinstance(policy, dict) or policy.get('schema_version') != 1:
            raise ValueError('不支持的策略版本')
        root = Path(policy['project_root'])
        cwd = Path(event['cwd'])
        if not root.is_absolute() or not cwd.is_absolute():
            raise ValueError('策略根和调用 cwd 必须是绝对路径')
        no_links(root); no_links(cwd)
        root = root.resolve(); cwd = cwd.resolve()
        if not beneath(cwd, root):
            return {}
        if not root.is_dir():
            raise ValueError('工程根不存在')
        if policy.get('rules_sha256') != rules_digest():
            raise ValueError('生效规则已变化，策略需重新审阅')
        if not isinstance(policy.get('expires_at'), str):
            raise ValueError('expires_at 必须是带时区的文本')
        expiry = datetime.fromisoformat(policy['expires_at'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError('策略已到期或缺少时区')
        if any(not isinstance(policy.get(k), str) or not policy[k].strip() for k in ('batch_id', 'decision_ref')):
            raise ValueError('缺少批准批次或外部确认定位')
        for key in ('write_paths', 'delete_paths'):
            values = policy.get(key, [])
            if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
                raise ValueError(key + ' 必须是路径列表')
        calls = policy.get('approved_calls', [])
        if not isinstance(calls, list) or any(not isinstance(x, dict) or not isinstance(x.get('tool'), str) or not isinstance(x.get('sha256'), str) for x in calls):
            raise ValueError('approved_calls 必须是工具及摘要对象列表')
        mode = policy.get('mode')
        if mode not in {'read_only', 'approved', 'paused'}:
            raise ValueError('无效执行模式')
        if name == 'SessionStart':
            return context('HuTools 调用检查已加载，批次 ' + policy['batch_id'] +
                '。这不证明宿主沙箱或授权文件已受保护；必须核验相应边界。', name)
        if name == 'Stop':
            if event.get('stop_hook_active'):
                return {'continue': False, 'stopReason': '交付检查仍未通过；停止重复续跑，报告未完成与阻塞。'}
            state_rel = policy.get('task_state')
            if not state_rel:
                return {}
            state = root / relative_file(state_rel, root, root)
            sys.path.insert(0, str(PLUGIN / 'tools'))
            from workflow import load_state, completion_errors
            value = load_state(state)
            if value['status'] == 'complete':
                errors = completion_errors(state)
                return deny('交付检查未通过：' + '; '.join(errors), 'Stop') if errors else {}
            if value['status'] == 'running':
                return deny('任务仍在执行；需如实记为完成、失败或暂停后交接。', 'Stop')
            return {}
        if mode == 'paused':
            raise ValueError('批次已暂停')
        tool = event['tool_name']
        args = event['tool_input']
        if not isinstance(args, dict):
            raise ValueError('工具参数必须是对象')
        if args.get('sandbox_permissions') == 'require_escalated':
            raise ValueError('策略不授予沙箱升级权限；交宿主独立审批')
        # 原生提问只收集本轮答复，工具可用性及答复后的操作仍由宿主核定。
        if tool in INPUT_CARDS:
            return {}
        if tool in {'write_stdin', 'exec', 'js', 'functions.exec'}:
            raise ValueError('持续会话/嵌套执行入口不能由一次批准覆盖')
        if tool in {'apply_patch', 'Edit', 'Write'}:
            if mode != 'approved':
                raise ValueError('本批未批准写入')
            writes = {relative_file(x, root, root) for x in policy.get('write_paths', [])}
            deletes = {relative_file(x, root, root) for x in policy.get('delete_paths', [])}
            source = None
            for action, raw in patch_targets(args.get('command')):
                path = relative_file(raw, cwd, root)
                if path not in writes:
                    raise ValueError('补丁目标不在精确批准清单：' + path)
                if action == 'Delete File' and path not in deletes:
                    raise ValueError('删除需要精确处置批准：' + path)
                if action == 'Move to' and source not in deletes:
                    raise ValueError('移动源文件需要精确处置批准：' + str(source))
                if action == 'Update File':
                    source = path
            return {}
        if mode != 'approved':
            raise ValueError('只读模式不通过未知副作用工具；按宿主只读入口调查')
        digest = call_digest(event)
        for record in policy.get('approved_calls', []):
            if record.get('tool') == tool and record.get('sha256') == digest:
                # 仅承认此调用已审阅；shell/子进程副作用仍由宿主限制。
                return {}
        raise ValueError('工具调用未获精确批准：' + str(tool))
    except (KeyError, TypeError, ValueError, OSError, ImportError) as exc:
        reason = 'HuTools 拒绝：' + str(exc)
        if name == 'SessionStart':
            return context(reason + '。执行保护不可用，停止高风险写入。', name)
        return deny(reason, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, help='外部本机绑定；缺省读取 PLUGIN_DATA/hutools-policy.json')
    parser.add_argument('--fingerprint', action='store_true', help='输出输入调用指纹，不批准调用')
    parser.add_argument('--rules-digest', action='store_true')
    args = parser.parse_args()
    if args.rules_digest:
        print(rules_digest()); return 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('hook 输入超过 1 MiB')
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError('hook 输入必须是对象')
        if args.fingerprint:
            print(call_digest(event)); return 0
        path = args.policy
        if path is None and os.environ.get('PLUGIN_DATA'):
            path = Path(os.environ['PLUGIN_DATA']) / 'hutools-policy.json'
        policy = load_policy(path) if path and path.exists() else None
        if args.policy and not args.policy.exists():
            raise ValueError('显式策略路径不存在')
        result = evaluate(event, policy)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Exit 2 是宿主文档中阻断 PreToolUse 的正式方式；不吞解析错误。
        print('HuTools hook 输入/绑定错误：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
