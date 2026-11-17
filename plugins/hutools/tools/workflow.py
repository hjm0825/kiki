#!/usr/bin/env python3
"""HuTools 版本化任务及证据检查。用于可追溯交付，不是沙箱或可信授权服务。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid

SCHEMA_VERSION = 1
STATES = {'planned', 'approved', 'running', 'paused', 'failed', 'complete'}
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


class WorkflowError(ValueError):
    """可定位的输入、状态或验证错误。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def no_links(path):
    path = Path(os.path.abspath(path))
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise WorkflowError(f'不接受符号链接：{part}')
    return path


def relative_path(root, value, must_exist=False):
    """所有持久业务引用相对工程根；拒绝绝对路径、上跳和符号链接。"""
    if not isinstance(value, str) or not value or '\\' in value:
        raise WorkflowError('引用必须是非空 POSIX 相对路径')
    rel = Path(value)
    if rel.is_absolute() or '..' in rel.parts:
        raise WorkflowError(f'引用越界：{value}')
    result = no_links(Path(root) / rel)
    if not result.is_relative_to(Path(root)):
        raise WorkflowError(f'引用越界：{value}')
    if must_exist and not result.exists():
        raise WorkflowError(f'文件或目录不存在：{value}')
    return result


def read_json(path):
    path = no_links(path)
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise WorkflowError('JSON 超出 8 MiB 上限，请拆分任务；不自动裁剪历史')
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise WorkflowError(f'无法读取 JSON {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise WorkflowError('JSON 顶层必须是对象')
    return value


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise WorkflowError('spec 必须是对象')
    for key in ('task_id', 'rules_version', 'source_version', 'author'):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise WorkflowError(f'spec 缺少 {key}')
    for key in ('requirements', 'inputs', 'writable_paths'):
        values = spec.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise WorkflowError(f'{key} 必须是非空字符串列表')
        if len(values) != len(set(values)):
            raise WorkflowError(f'{key} 不允许重复')
    cases = spec.get('cases')
    if not isinstance(cases, list) or not cases:
        raise WorkflowError('至少提供一个验收 case')
    ids = set()
    covered = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'].strip() or case['id'] in ids:
            raise WorkflowError('case.id 必须是唯一非空字符串')
        ids.add(case['id'])
        if case.get('requirement') not in spec['requirements']:
            raise WorkflowError(f"未知需求：{case.get('requirement')}")
        if not isinstance(case.get('required', True), bool):
            raise WorkflowError('case.required 必须是布尔值')
        if case.get('required', True):
            covered.add(case['requirement'])
        if case.get('kind') not in ('command', 'manual', 'review'):
            raise WorkflowError('case.kind 只支持 command/manual/review')
        if case['kind'] == 'command':
            argv = case.get('argv')
            if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or '\x00' in a for a in argv) or not argv[0]:
                raise WorkflowError('command case 必须提供 argv 字符串列表')
            timeout = case.get('timeout_seconds', 60)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
                raise WorkflowError('timeout_seconds 必须在 0 到 3600 秒之间')
    independent = spec.get('requires_independent_review', False)
    if not isinstance(independent, bool):
        raise WorkflowError('requires_independent_review 必须是布尔值')
    if independent and not any(c['kind'] == 'review' and c.get('required', True) for c in cases):
        raise WorkflowError('此任务要求独立审查，必须声明 required review case')
    if covered != set(spec['requirements']):
        raise WorkflowError('每个需求必须至少对应一个 required case')


def project_root(state_path, state):
    value = state.get('project_root')
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise WorkflowError('project_root 必须相对任务文件目录')
    path = no_links(Path(state_path).parent / value).resolve()
    if not path.is_dir() or not Path(state_path).resolve().is_relative_to(path):
        raise WorkflowError('工程根无效或任务文件不在工程内')
    return path


def load_state(state_path):
    state = read_json(state_path)
    if state.get('schema_version') != SCHEMA_VERSION:
        raise WorkflowError('不支持的 schema_version；保留原件后显式迁移')
    if state.get('status') not in STATES:
        raise WorkflowError('未知任务状态')
    validate_spec(state.get('spec'))
    project_root(state_path, state)
    for key in ('events', 'evidence'):
        if not isinstance(state.get(key), list) or any(not isinstance(item, dict) for item in state[key]):
            raise WorkflowError(f'{key} 必须是对象列表')
    for evidence in state['evidence']:
        if not isinstance(evidence.get('case_id'), str) or evidence.get('outcome') not in ('pass', 'fail') or not isinstance(evidence.get('artifact'), str):
            raise WorkflowError('证据条目缺少 case_id/outcome/artifact')
    return state


def spec_digest(state):
    return digest({'project_root': state['project_root'], 'spec': state['spec']})


def require_approval(state):
    approval = state.get('approval')
    if not isinstance(approval, dict) or approval.get('spec_sha256') != spec_digest(state):
        raise WorkflowError('缺少适用批准，或需求/规则/检查定义已变化，需重新确认')
    if not approval.get('actor') or not approval.get('decision'):
        raise WorkflowError('批准缺少用户决定出处')


def snapshot(root, spec, state_path):
    """绑定明确输入集合；禁止把自己的状态/证据目录纳入输入造成自引用。"""
    files = {}
    evidence_dir = Path(state_path).parent
    for item in spec['inputs']:
        path = relative_path(root, item, must_exist=True)
        if path == evidence_dir or evidence_dir.is_relative_to(path) or path == Path(state_path):
            raise WorkflowError('inputs 不能覆盖任务/证据目录，请列具体源码及规则输入')
        if not path.is_file() and not path.is_dir():
            raise WorkflowError(f'不接受特殊输入文件：{path}')
        entries = [path] if path.is_file() else [path, *sorted(path.rglob('*'))]
        for entry in entries:
            no_links(entry)
            relative = entry.relative_to(root).as_posix()
            if entry.is_file():
                files[relative] = {'kind': 'file', 'sha256': hashlib.sha256(entry.read_bytes()).hexdigest()}
            elif entry.is_dir():
                # 目录结构也是输入；空子目录删、增、改名均须使旧证据失效。
                files[relative] = {'kind': 'directory'}
            else:
                raise WorkflowError(f'不接受特殊输入文件：{entry}')
    return digest(files)


def writable(root, spec, path):
    target = no_links(path)
    allowed = [relative_path(root, item) for item in spec['writable_paths']]
    if not any(target == p or target.is_relative_to(p) for p in allowed):
        raise WorkflowError(f'产物路径不在批准的 writable_paths：{target}')


@contextmanager
def locked(path):
    path = no_links(path)
    lock = path.with_name(path.name + '.lock')
    fd = None
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f'{os.getpid()} {now()}\n'.encode())
    except FileExistsError as exc:
        raise WorkflowError(f'任务锁已存在：{lock}；先核实进程，禁止自动抢锁') from exc
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            lock.unlink()


def save_state(path, state):
    path = no_links(path)
    state['updated_at'] = now()
    data = json.dumps(state, ensure_ascii=False, indent=2) + '\n'
    if len(data.encode()) > MAX_STATE_BYTES:
        raise WorkflowError('任务状态超过容量边界；不自动丢弃历史')
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def event(state, event_type, **details):
    state['events'].append({'at': now(), 'type': event_type, **details})


def create(root, state_path, spec):
    root = no_links(root).resolve()
    path = no_links(state_path)
    if not root.is_dir() or not path.is_relative_to(root):
        raise WorkflowError('任务必须位于存在的工程根内')
    validate_spec(spec)
    for item in spec['writable_paths']:
        relative_path(root, item)
    writable(root, spec, path)
    snapshot(root, spec, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        if path.exists():
            raise WorkflowError('任务已存在，不覆盖')
        state = {'schema_version': SCHEMA_VERSION, 'project_root': os.path.relpath(root, path.parent),
                 'spec': spec, 'status': 'planned', 'approval': None,
                 'events': [], 'evidence': [], 'created_at': now()}
        event(state, 'created')
        save_state(path, state)
    return state


def approve(state_path, actor, decision):
    if not actor.strip() or not decision.strip():
        raise WorkflowError('必须提供确认者和实际用户决定出处')
    with locked(state_path):
        state = load_state(state_path)
        if state['status'] not in ('planned', 'paused', 'failed'):
            raise WorkflowError('只能批准 planned/paused/failed 任务，不能重写执行中批准')
        state['approval'] = {'actor': actor, 'decision': decision, 'at': now(),
                             'spec_sha256': spec_digest(state), 'trust': 'recorded_external_decision'}
        state['status'] = 'approved'
        event(state, 'approved', actor=actor, decision=decision)
        save_state(state_path, state)
    return state


def case_for(state, case_id):
    for case in state['spec']['cases']:
        if case['id'] == case_id:
            return case
    raise WorkflowError(f'未知 case：{case_id}')


def artifact_hash(root, artifact):
    path = relative_path(root, artifact, True)
    if not path.is_file():
        raise WorkflowError('证据必须是普通文件')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _completion_errors(state_path, state):
    errors = []
    try:
        require_approval(state)
        root = project_root(state_path, state)
        current = snapshot(root, state['spec'], Path(state_path))
    except (WorkflowError, OSError) as exc:
        return [str(exc)]
    for case in state['spec']['cases']:
        if not case.get('required', True):
            continue
        matches = [e for e in state['evidence'] if e.get('case_id') == case['id']]
        if not matches:
            errors.append(f"{case['id']} 缺少证据")
            continue
        evidence = matches[-1]
        if evidence.get('outcome') != 'pass':
            errors.append(f"{case['id']} 最新结果未通过")
        if evidence.get('input_sha256') != current or evidence.get('spec_sha256') != spec_digest(state):
            errors.append(f"{case['id']} 证据版本失效")
        if evidence.get('kind') != case['kind']:
            errors.append(f"{case['id']} 证据类型不符")
        if case['kind'] == 'command' and (evidence.get('collector') != 'run-check'
                                         or evidence.get('returncode') != 0
                                         or evidence.get('reason') != 'exit'
                                         or evidence.get('after_input_sha256') != current
                                         or evidence.get('argv') != case['argv']):
            errors.append(f"{case['id']} 缺少真实成功命令结果")
        if case['kind'] == 'review' and evidence.get('actor') == state['spec']['author']:
            errors.append(f"{case['id']} 缺少独立审查者")
        try:
            if artifact_hash(root, evidence.get('artifact')) != evidence.get('artifact_sha256'):
                errors.append(f"{case['id']} 证据文件已变化")
        except (WorkflowError, OSError) as exc:
            errors.append(f"{case['id']} 证据不可用：{exc}")
    return errors


def completion_errors(state_path):
    try:
        return _completion_errors(state_path, load_state(state_path))
    except (WorkflowError, OSError) as exc:
        return [str(exc)]


def transition(state_path, target, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError('状态变更必须说明理由')
    edges = {'planned': {'paused'}, 'approved': {'running', 'paused'},
             'running': {'paused', 'failed', 'complete'},
             'paused': {'running', 'failed'}, 'failed': {'running', 'paused'},
             'complete': {'paused'}}
    with locked(state_path):
        state = load_state(state_path)
        if target not in edges[state['status']]:
            raise WorkflowError(f"禁止状态转换 {state['status']} → {target}")
        if target in ('running', 'complete'):
            require_approval(state)
        if target == 'complete':
            errors = _completion_errors(state_path, state)
            if errors:
                raise WorkflowError('不能完成：' + '；'.join(errors))
        previous = state['status']
        state['status'] = target
        event(state, 'transition', previous=previous, target=target, reason=reason)
        save_state(state_path, state)
    return state


def _execute(argv, cwd, logfile, timeout):
    """执行调用方已批准的 argv。POSIX 上限制输出与时间，并清理同组子进程。"""
    if os.name != 'posix':
        raise WorkflowError('run-check 当前仅验收 POSIX；其他平台保留计划/人工证据流程')
    reason = 'exit'
    output_bytes = 0
    started = time.monotonic()
    with open(logfile, 'xb') as output:
        try:
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as exc:
            output.write(str(exc).encode())
            return 127, 'spawn_error', time.monotonic() - started
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        killed_at = None
        try:
            while selector.get_map():
                if killed_at is None and time.monotonic() - started >= timeout:
                    reason = 'timeout'
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    killed_at = time.monotonic()
                if killed_at is not None and time.monotonic() - killed_at > 1:
                    break
                for key, _ in selector.select(0.05):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = MAX_OUTPUT_BYTES - output_bytes
                    output.write(block[:remaining])
                    output_bytes += min(remaining, len(block))
                    if len(block) > remaining and killed_at is None:
                        reason = 'output_limit'
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        killed_at = time.monotonic()
            # 父进程关闭 stdout 后仍可能存活；剩余时间继续限时等待。
            remaining_time = max(0.01, timeout - (time.monotonic() - started))
            try:
                process.wait(timeout=remaining_time if killed_at is None else 1)
            except subprocess.TimeoutExpired:
                reason = 'timeout'
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
        finally:
            selector.close()
            process.stdout.close()
            # 即使父进程成功退出，也不遗留仍在同一进程组的后台工作。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return process.returncode, reason, round(time.monotonic() - started, 6)


def run_check(state_path, case_id):
    path = Path(state_path)
    with locked(path):
        state = load_state(path)
        require_approval(state)
        if state['status'] != 'running':
            raise WorkflowError('仅 running 任务可以执行检查')
        case = case_for(state, case_id)
        if case['kind'] != 'command':
            raise WorkflowError('run-check 只接受 command case')
        state_before = digest(state)
        root = project_root(path, state)
        before = snapshot(root, state['spec'], path)
        logs = path.parent / (path.stem + '-evidence')
        writable(root, state['spec'], logs)
        no_links(logs)
        logs.mkdir(exist_ok=True)
        artifact = logs / (uuid.uuid4().hex + '.log')
        returncode, reason, duration = _execute(case['argv'], root, artifact,
                                               case.get('timeout_seconds', 60))
        try:
            current_state = load_state(path)
            if digest(current_state) != state_before:
                raise WorkflowError('检查命令改写了任务状态；保留现状，不覆盖')
        except (WorkflowError, OSError) as exc:
            raise WorkflowError(f'{exc}；原始命令输出保留于 {artifact.relative_to(root)}') from exc
        try:
            after = snapshot(root, state['spec'], path)
        except (WorkflowError, OSError):
            after = None
        outcome = 'pass' if returncode == 0 and reason == 'exit' and before == after else 'fail'
        if before != after:
            reason = 'inputs_changed'
        evidence = {'id': uuid.uuid4().hex, 'at': now(), 'case_id': case_id, 'kind': 'command',
                    'collector': 'run-check', 'actor': state['spec']['author'], 'outcome': outcome,
                    'returncode': returncode, 'reason': reason, 'argv': case['argv'],
                    'duration_seconds': duration, 'output_limit_bytes': MAX_OUTPUT_BYTES,
                    'artifact': artifact.relative_to(root).as_posix(),
                    'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    'input_sha256': before, 'after_input_sha256': after,
                    'spec_sha256': spec_digest(state)}
        state['evidence'].append(evidence)
        event(state, 'check', case_id=case_id, evidence_id=evidence['id'], outcome=outcome)
        save_state(path, state)
    return evidence


def record_evidence(state_path, case_id, actor, artifact, outcome, statement):
    if outcome not in ('pass', 'fail') or not actor.strip() or not statement.strip():
        raise WorkflowError('人工证据必须提供 actor、pass/fail 和实际说明')
    with locked(state_path):
        state = load_state(state_path)
        require_approval(state)
        if state['status'] != 'running':
            raise WorkflowError('仅 running 任务可以追加证据')
        case = case_for(state, case_id)
        if case['kind'] == 'command':
            raise WorkflowError('命令证据必须由 run-check 实际采集，不能手填通过')
        if case['kind'] == 'review' and actor == state['spec']['author']:
            raise WorkflowError('review 必须由不同于实施者的实际审查者完成')
        root = project_root(state_path, state)
        evidence = {'id': uuid.uuid4().hex, 'at': now(), 'case_id': case_id,
                    'kind': case['kind'], 'collector': 'human-attestation',
                    'actor': actor, 'outcome': outcome, 'statement': statement,
                    'artifact': artifact, 'artifact_sha256': artifact_hash(root, artifact),
                    'input_sha256': snapshot(root, state['spec'], Path(state_path)),
                    'spec_sha256': spec_digest(state)}
        state['evidence'].append(evidence)
        event(state, 'attestation', case_id=case_id, evidence_id=evidence['id'], actor=actor)
        save_state(state_path, state)
    return evidence



def observe(state_path, role, actor, kind, started_at, ended_at, reason, artifact=None):
    """只登记真实观测片段；时间与角色身份由调用方提供，不冒充宿主计费统计。"""
    if kind not in ('work', 'wait', 'retry') or not role.strip() or not actor.strip() or not reason.strip():
        raise WorkflowError('片段需 role/actor、work|wait|retry 和实际原因')
    try:
        start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        end = datetime.fromisoformat(ended_at.replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('时间必须带时区，结束不能早于开始')
    except (ValueError, AttributeError) as exc:
        raise WorkflowError(f'无效片段时间：{exc}') from exc
    with locked(state_path):
        state = load_state(state_path)
        details = {'id': uuid.uuid4().hex, 'role': role, 'actor': actor, 'kind': kind,
                   'started_at': started_at, 'ended_at': ended_at, 'reason': reason,
                   'source': 'caller-observation'}
        if artifact is not None:
            root = project_root(state_path, state)
            details.update(artifact=artifact, artifact_sha256=artifact_hash(root, artifact))
        event(state, 'fragment', **details)
        save_state(state_path, state)
    return details


def report(state_path):
    state = load_state(state_path)
    errors = completion_errors(state_path)
    lines = [f"# {state['spec']['task_id']}", '', f"状态：{state['status']}",
             f"规则版本：{state['spec']['rules_version']}",
             f"来源版本：{state['spec']['source_version']}", '',
             '本报告是任务状态的阅读视图；人工证据与授权仍需核对真实来源。', '',
             '| 验收项 | 需求 | 类型 | 最新结果 |', '| --- | --- | --- | --- |']
    for case in state['spec']['cases']:
        matches = [e for e in state['evidence'] if e.get('case_id') == case['id']]
        result = matches[-1]['outcome'] if matches else '待验证'
        lines.append(f"| {case['id']} | {case['requirement']} | {case['kind']} | {result} |")
    lines += ['', '当前交付检查：' + ('通过（限声明输入与验收项）' if not errors else '未通过'), '']
    lines += ['- ' + error for error in errors]
    fragments = [e for e in state['events'] if e.get('type') == 'fragment']
    if fragments:
        lines += ['', '## 已记录工作片段', '', '以下是调用者提供的实际观察；并行片段不能相加当作总耗时，未观测时间和 token 不补算。', '',
                  '| 角色/执行者 | 类型 | 开始 | 结束 | 原因 |', '| --- | --- | --- | --- | --- |']
        for fragment in fragments:
            lines.append(f"| {fragment['role']}/{fragment['actor']} | {fragment['kind']} | {fragment['started_at']} | {fragment['ended_at']} | {fragment['reason']} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('create', 'approve', 'transition', 'run-check', 'evidence', 'observe', 'status', 'report'):
        sub = commands.add_parser(name)
        sub.add_argument('--state', required=True, type=Path)
        if name == 'create':
            sub.add_argument('--root', required=True, type=Path)
            sub.add_argument('--spec', required=True, type=Path)
        elif name == 'approve':
            sub.add_argument('--actor', required=True)
            sub.add_argument('--decision', required=True)
        elif name == 'transition':
            sub.add_argument('--to', required=True, choices=sorted(STATES))
            sub.add_argument('--reason', required=True)
        elif name == 'observe':
            for field in ('role', 'actor', 'kind', 'started-at', 'ended-at', 'reason'):
                sub.add_argument('--' + field, required=True)
            sub.add_argument('--artifact')
        elif name in ('run-check', 'evidence'):
            sub.add_argument('--case', required=True)
            if name == 'evidence':
                sub.add_argument('--actor', required=True)
                sub.add_argument('--artifact', required=True)
                sub.add_argument('--outcome', required=True, choices=['pass', 'fail'])
                sub.add_argument('--statement', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            result = create(args.root, args.state, read_json(args.spec))
        elif args.command == 'approve':
            result = approve(args.state, args.actor, args.decision)
        elif args.command == 'transition':
            result = transition(args.state, args.to, args.reason)
        elif args.command == 'run-check':
            result = run_check(args.state, args.case)
        elif args.command == 'evidence':
            result = record_evidence(args.state, args.case, args.actor, args.artifact, args.outcome, args.statement)
        elif args.command == 'observe':
            result = observe(args.state, args.role, args.actor, args.kind, args.started_at, args.ended_at, args.reason, args.artifact)
        elif args.command == 'report':
            print(report(args.state), end='')
            return 0
        else:
            state = load_state(args.state)
            errors = completion_errors(args.state)
            effective = 'needs_revalidation' if state['status'] == 'complete' and errors else state['status']
            result = {'state': state, 'effective_status': effective, 'completion_errors': errors}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == 'run-check' and result['outcome'] != 'pass' else 0
    except (WorkflowError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
