#!/usr/bin/env python3
"""记录双环资料盘点与逐项缺口决定。状态文件相对项目根保存证据引用。"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import catalog_layout
from lifecycle import relative_path, root_path
from workflow import no_links, now, read_json, save_state


SCHEMA = 'hutools-catalog-1'
UNAVAILABLE = 'scope-unavailable'


def required(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('标识、判断和决定依据必须明确填写')
    return text.strip()


def new_scope(source):
    return {'source': source, 'source_generation': 1, 'review': None, 'gaps': {}, 'history': []}


def blank_state():
    return {'schema': SCHEMA, 'project': None, 'rules': None, 'binding': None,
            'scopes': {}, 'last_completed_at': None}


def split_source(value):
    kind, separator, relative = value.partition(':')
    if not separator or kind not in ('project', 'library'):
        raise ValueError(f'范围源须为 project:相对路径 或 library:相对路径：{value}')
    if relative != '.':
        relative_path(relative)
    return kind, relative


def scope_definitions(values):
    result = {}
    for value in values:
        key, sep, source = value.partition('=')
        key = required(key)
        if not sep or key in result:
            raise ValueError(f'范围声明有误或标识重复：{value}')
        split_source(source)
        result[key] = source
    return result


def binding_id(library):
    # 原路径保存在本机绑定。携带索引时只带用于发现绑定变化的摘要。
    if library is None:
        return None
    try:
        machine = Path('/etc/machine-id').read_bytes().strip()
    except OSError:
        machine = b''
    identity = machine or os.fsencode(os.uname().nodename)
    return hashlib.sha256(identity + bytes([0]) + os.fsencode(library)).hexdigest()


def source_problem(source, root, library):
    kind, relative = split_source(source)
    base = root if kind == 'project' else library
    if base is None:
        return '本机资料库尚未绑定'
    try:
        path = no_links(base / relative)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            with os.scandir(path) as entries:
                next(entries, None)
        elif stat.S_ISREG(mode):
            with path.open('rb') as stream:
                stream.read(1)
        else:
            return '范围入口不是普通目录或文件'
    except (OSError, ValueError) as exc:
        return f'范围入口不可访问（{type(exc).__name__}）'
    return None


def verify_evidence(root, reference, state_path):
    path = no_links(root / relative_path(reference))
    if path == state_path or not path.is_file():
        raise ValueError(f'证据须为项目内已有的普通文件：{reference}')
    with path.open('rb') as stream:
        if not stream.read(1):
            raise ValueError(f'证据文件为空：{reference}')


def invalidate(scope, reason):
    if scope['review'] is not None:
        scope['history'].append({'at': now(), 'reason': reason, 'review': scope['review']})
    scope['review'] = None
    scope['pending_reason'] = reason


def availability_id(generation):
    return UNAVAILABLE if generation == 1 else f'{UNAVAILABLE}@{generation}'


def restore_source_generations(scope):
    """旧索引的换绑历史仍可辨别此前开放的缺口；保留其原始来源。"""
    if 'source_generation' in scope:
        return
    revisions = [entry for entry in scope['history'] if 'source' in entry and 'gaps' in entry]
    current = len(revisions) + 1
    scope['source_generation'] = current
    for key, gap in list(scope['gaps'].items()):
        generation, source = current, scope['source']
        for number, entry in enumerate(revisions, 1):
            earlier = entry['gaps'].get(key)
            if earlier and earlier['opened_at'] == gap['opened_at'] and earlier['item'] == gap['item']:
                generation, source = number, entry['source']
                break
        gap.update(source_generation=generation, source=source)
        if key == UNAVAILABLE and generation > 1:
            scope['gaps'][availability_id(generation)] = scope['gaps'].pop(key)


def reset_source(scope, new_source):
    reason = '范围或本机资料库绑定变化'
    invalidate(scope, reason)
    scope['history'].append({'at': now(), 'source': scope['source'],
                             'source_generation': scope['source_generation'], 'reason': reason, 'gaps': deepcopy(scope['gaps'])})
    # 旧版关闭记录只对原来源有效；旧的开放问题须继续处理。
    scope['gaps'] = {key: gap for key, gap in scope['gaps'].items()
                     if gap['resolution'] is None}
    scope.pop('layout', None)
    scope.pop('layout_directory', None)
    scope['source'] = new_source
    scope['source_generation'] += 1


def begin(state, args):
    definitions = scope_definitions(args.scope)
    if state['project'] not in (None, args.project):
        raise ValueError('索引属于另一项目，请先核对工程身份')
    omitted = state['scopes'].keys() - definitions.keys()
    if omitted:
        raise ValueError('缺少已登记范围，不可隐式移除：' + ', '.join(sorted(omitted)))
    binding = binding_id(args.library_root)
    has_library = any(source.startswith('library:') for source in definitions.values())
    if args.rebind_library and (not has_library or not binding):
        raise ValueError('重新绑定须提供确认后的资料库及其范围')
    if has_library and state['binding'] and not binding:
        raise ValueError('本次没有提供已确认的库根；已登记绑定保留')
    for key, source in definitions.items():
        if key not in state['scopes']:
            state['scopes'][key] = new_scope(source)
            continue
        scope = state['scopes'][key]
        if source != scope['source'] or (
                source.startswith('library:') and (binding != state['binding'] or args.rebind_library)):
            reset_source(scope, source)
        elif args.rules != state['rules']:
            invalidate(scope, '整理规则变化')
    state.update(project=args.project, rules=args.rules, binding=binding if has_library else None)


def reopen_gap(scope, key):
    gap = scope['gaps'][key]
    scope['history'].append({'at': now(), 'id': key, 'gap': deepcopy(gap)})
    gap.update(opened_at=now(), resolution=None)


def open_gap(scope, key, item, finding, impact):
    previous = scope['gaps'].get(key)
    fields = {'item': item, 'finding': finding, 'impact': impact,
              'source': scope['source'], 'source_generation': scope['source_generation']}
    if previous:
        if all(previous[k] == v for k, v in fields.items()):
            if previous['resolution'] and previous['resolution']['kind'] == 'fixed':
                # 调用方再次明确报告实际缺口，上一轮修复留在历史中。
                reopen_gap(scope, key)
            return
        raise ValueError(f'该缺口标识已有不同记录，请核对或使用独立标识：{key}')
    scope['gaps'][key] = dict(fields, opened_at=now(), resolution=None)


def check_scope(scope, root, library, state_path):
    problem = source_problem(scope['source'], root, library)
    key = availability_id(scope['source_generation'])
    previous = scope['gaps'].get(key)
    if problem:
        if not previous:
            open_gap(scope, key, scope['source'], problem, '原始范围及内容未能核验')
        elif previous['resolution'] and previous['resolution']['kind'] == 'fixed':
            reopen_gap(scope, key)
            previous['finding'] = problem
    for key, gap in scope['gaps'].items():
        resolution = gap['resolution']
        if resolution and resolution['kind'] == 'fixed':
            try:
                verify_evidence(root, resolution['evidence'], state_path)
            except (OSError, ValueError):
                reopen_gap(scope, key)
    if scope['review']:
        try:
            if scope.get('layout_directory'):
                if not scope.get('layout'):
                    raise ValueError('上次只完成了盘点')
                catalog_layout.coverage(root, scope['layout'])
            for reference in scope['review']['evidence'].values():
                verify_evidence(root, reference, state_path)
        except (OSError, ValueError) as exc:
            invalidate(scope, f'盘点依据不可用（{type(exc).__name__}）')
    return problem


def update_scope(args, scope, problem, root, state_path):
    if args.command == 'touch':
        invalidate(scope, args.reason)
    elif args.command == 'review':
        if scope.get('layout_directory'):
            if not scope.get('layout'):
                raise ValueError('须先执行实体整理')
            catalog_layout.coverage(root, scope['layout'])
        evidence = {'outer': args.outer, 'inner': args.inner, 'inventory': args.inventory}
        for reference in evidence.values():
            verify_evidence(root, reference, state_path)
        if scope['review'] is None or scope['review']['evidence'] != evidence:
            invalidate(scope, '盘点依据更新')
            scope['review'] = {'at': now(), 'evidence': evidence}
        scope.pop('pending_reason', None)
    elif args.command == 'gap':
        if args.id == UNAVAILABLE or args.id.startswith(UNAVAILABLE + '@'):
            raise ValueError(f'{UNAVAILABLE} 是系统范围可读性缺口')
        open_gap(scope, args.id, args.item, args.finding, args.impact)
    elif args.command == 'close':
        gap = scope['gaps'].get(args.id)
        if not gap:
            raise ValueError(f'此范围没有该缺口：{args.id}')
        if args.fixed:
            if args.reference:
                raise ValueError('--reference 仅用于明确的用户决定')
            if args.id == availability_id(scope['source_generation']) and problem:
                raise ValueError('范围仍不可读，无法记为已修复')
            verify_evidence(root, args.fixed, state_path)
            decision = {'kind': 'fixed', 'evidence': args.fixed}
        else:
            decision = {'kind': 'accepted', 'decision': args.accepted,
                        'reference': required(args.reference)}
        if gap['resolution']:
            if all(gap['resolution'].get(k) == v for k, v in decision.items()):
                return
            raise ValueError('已关闭缺口的处置不可覆盖；新发现应另行登记')
        gap['resolution'] = dict(decision, at=now())


def view(state):
    scopes = {}
    for key, scope in state['scopes'].items():
        opened = [k for k, g in scope['gaps'].items() if g['resolution'] is None]
        reviewed = scope['review'] is not None
        scopes[key] = {k: v for k, v in scope.items() if k != 'history'}
        scopes[key].update(reviewed=reviewed, open_gaps=opened, complete=reviewed and not opened)
    complete = bool(scopes) and all(s['complete'] for s in scopes.values())
    started = state.get('last_completed_at') or any(
        s['gaps'] or s['review'] or s.get('pending_reason') for s in scopes.values())
    mode = 'incremental' if complete else ('resume' if started else 'full')
    return {'project': state['project'], 'rules': state['rules'], 'mode': mode,
            'complete': complete, 'last_completed_at': state.get('last_completed_at'), 'scopes': scopes}


@contextmanager
def writer(path):
    # 父目录 inode 在状态文件替换时保持稳定，异常退出也会释放锁。
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def load_state(path, root):
    if not path.exists():
        return blank_state()
    value = read_json(path)
    if value.get('schema') != SCHEMA or value.get('state_location') != str(path.relative_to(root)):
        raise ValueError('索引类型或项目内定位不匹配，保留原文件')
    return value


def execute(args, path, root):
    state = load_state(path, root)
    original = deepcopy(state)
    for scope in state['scopes'].values():
        restore_source_generations(scope)
    if args.command == 'begin':
        begin(state, args)
    elif not state['project'] and args.command != 'status':
        raise ValueError('先用 begin 登记工程和全部范围')
    has_library = any(scope['source'].startswith('library:') for scope in state['scopes'].values())
    binding_change = has_library and binding_id(args.library_root) != state['binding']
    if binding_change and args.command != 'status':
        raise ValueError('请先向用户核对资料库，再用 begin 记录新绑定')
    if args.command == 'begin':
        for key in args.layout_scope or []:
            scope = state['scopes'][key]
            if not scope['source'].startswith('project:'):
                raise ValueError('实体文档范围须为工程目录')
            scope['layout_directory'] = scope['source'].split(':', 1)[1]
    problems = {}
    for key, scope in state['scopes'].items():
        if binding_change and scope['source'].startswith('library:'):
            invalidate(scope, '本次资料库绑定尚未确认')
        else:
            problems[key] = check_scope(scope, root, args.library_root, path)
    if args.command in ('inspect', 'arrange'):
        scope = state['scopes'][args.scope]
        directory = scope.get('layout_directory')
        if not directory or problems.get(args.scope):
            raise ValueError('须先登记可读的实体文档范围')
        if args.command == 'inspect':
            return {'directory': directory, 'files': catalog_layout.inventory(root, directory),
                    'layout': scope.get('layout'), 'mode': view(state)['mode']}
        plan = read_json(args.plan)
        if plan['directory'] != directory:
            raise ValueError('清单与已登记范围不同')
        if args.apply and not args.recovery:
            raise ValueError('执行时须指定工程外的恢复包')
        layout, changed = catalog_layout.arrange(root, plan, args.recovery, args.apply, scope.get('layout'))
        if changed:
            scope['layout'] = layout
            invalidate(scope, '实体整理已执行，待复核阅读入口')
            save_state(path, state)
        return dict(layout, changed=changed)
    if args.command not in ('begin', 'status'):
        if args.scope not in state['scopes']:
            raise ValueError(f'范围未登记：{args.scope}')
        update_scope(args, state['scopes'][args.scope], problems.get(args.scope), root, path)
    result = view(state)
    if args.command != 'status' and original != state:
        state['state_location'] = str(path.relative_to(root))
        if result['complete']:
            state['last_completed_at'] = now()
        save_state(path, state)
        result = view(state)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path, help='项目内持久索引文件')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='项目根')
    parser.add_argument('--library-root', type=Path, help='本次确认的本机库根')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='只读核对当前模式和各范围的缺口')
    start = commands.add_parser('begin', help='对照全部约定范围接手')
    start.add_argument('--rebind-library', action='store_true', help='换机或同址换卷后按用户确认复核库范围')
    start.add_argument('--layout-scope', action='append', help='实体双环的工程文档范围 ID')
    for command in ('inspect', 'arrange'):
        sub = commands.add_parser(command)
        sub.add_argument('--scope', required=True)
        if command == 'arrange':
            sub.add_argument('--plan', required=True, type=Path)
            sub.add_argument('--apply', action='store_true')
            sub.add_argument('--recovery', type=Path)
    start.add_argument('--project', required=True, type=required)
    start.add_argument('--rules', required=True, type=required)
    start.add_argument('--scope', required=True, action='append', metavar='ID=来源:相对路径')
    for name in ('review', 'gap', 'close', 'touch'):
        p = commands.add_parser(name)
        p.add_argument('--scope', required=True, type=required)
        if name == 'review':
            for key in ('outer', 'inner', 'inventory'):
                p.add_argument('--' + key, required=True, type=required)
        elif name == 'touch':
            p.add_argument('--reason', required=True, type=required)
        else:
            p.add_argument('--id', required=True, type=required)
            if name == 'gap':
                for key in ('item', 'finding', 'impact'):
                    p.add_argument('--' + key, required=True, type=required)
            else:
                outcome = p.add_mutually_exclusive_group(required=True)
                outcome.add_argument('--fixed', type=required)
                outcome.add_argument('--accepted', type=required)
                p.add_argument('--reference', type=required)
    args = parser.parse_args()
    try:
        root = root_path(args.root)
        path = no_links(args.state if args.state.is_absolute() else root / args.state)
        if path == root or not path.is_relative_to(root):
            raise ValueError('索引必须在工程根内，按工程根解析相对路径')
        if args.library_root is not None:
            if not args.library_root.is_absolute():
                raise ValueError('本机库根须明确给出绝对路径')
            args.library_root = no_links(args.library_root)
        if args.command == 'status':
            result = execute(args, path, root)
        else:
            with writer(path):
                result = execute(args, path, root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f'资料索引：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
