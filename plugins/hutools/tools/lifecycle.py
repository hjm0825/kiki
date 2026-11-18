#!/usr/bin/env python3
"""精确文件清单、归档核验和可恢复隔离；不扫描目录、不永久删除文件。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
from uuid import uuid4

SCHEMA_VERSION = 1
MAX_JSON_BYTES = 5 * 1024 * 1024
CATEGORIES = {'core', 'evidence', 'rebuildable', 'process', 'unknown'}
FLAGS = ('unique', 'unresolved_failure', 'active', 'recovery_required',
         'retention_satisfied', 'reproducible')


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def plan_digest(plan):
    return hashlib.sha256(canonical(plan)).hexdigest()


def root_path(root):
    path = Path(root).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_dir() or path.resolve() == Path(path.anchor):
        raise ValueError('root 必须是已存在的普通工程目录，不能是链接或文件系统根')
    return path.resolve()


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('清单路径必须是非空 POSIX 相对路径')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(x in ('', '.', '..') for x in value.split('/')):
        raise ValueError(f'非规范或越界路径：{value}')
    return path


def safe_path(root, relative, must_exist=True):
    root = root_path(root)
    rel = relative_path(relative)
    current = root
    for part in rel.parts:
        if current.exists() and not current.is_dir():
            raise ValueError(f'路径祖先不是目录：{current.relative_to(root)}')
        current /= part
        if current.is_symlink():
            raise ValueError(f'拒绝符号链接：{relative}')
    if must_exist and (not current.is_file() or not stat.S_ISREG(current.stat().st_mode)):
        raise ValueError(f'资料不存在或不是普通文件：{relative}')
    return current


def fingerprint(path):
    if path.is_symlink():
        raise ValueError('拒绝符号链接')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('仅支持普通文件')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('读取期间文件已替换')
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if path.is_symlink() or identity(before) != identity(after):
        raise ValueError('读取期间文件发生变化')
    return {'bytes': before.st_size, 'sha256': digest.hexdigest()}


def make_plan(root, project_id, task_id, files):
    root = root_path(root)
    if not isinstance(project_id, str) or not project_id.strip() or not isinstance(task_id, str) or not task_id.strip():
        raise ValueError('project_id 和 task_id 必须明确')
    if not isinstance(files, list) or not files:
        raise ValueError('files 必须是非空精确文件列表')
    records, seen = [], set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('files 条目必须是对象')
        relative = item.get('path')
        path = safe_path(root, relative)
        if relative in seen:
            raise ValueError(f'重复路径：{relative}')
        seen.add(relative)
        category = item.get('category', 'unknown')
        if category not in CATEGORIES:
            raise ValueError(f'未知资料类别：{category}')
        record = {'path': relative, **fingerprint(path), 'category': category}
        for field in FLAGS:
            record[field] = item.get(field) if type(item.get(field)) is bool else None
        for field in ('rationale', 'rebuild_method', 'retention_condition', 'authorization_ref'):
            value = item.get(field)
            record[field] = value if isinstance(value, str) else ''
        actions = item.get('allowed_actions', [])
        record['allowed_actions'] = actions if isinstance(actions, list) and all(isinstance(x, str) for x in actions) else []
        records.append(record)
    return {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'task_id': task_id,
            'created_at': now(), 'path_base': 'explicit --root', 'records': records}


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支持或缺失的资料清单版本')
    if not all(isinstance(plan.get(key), str) and plan[key].strip() for key in ('project_id', 'task_id')):
        raise ValueError('清单缺少工程/任务身份')
    records = plan.get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('清单 records 必须是非空列表')
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('无效清单条目')
        relative = str(relative_path(record.get('path')))
        digest = record.get('sha256')
        if relative in seen or type(record.get('bytes')) is not int or record['bytes'] < 0:
            raise ValueError('重复路径或无效字节数')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('无效 SHA-256')
        seen.add(relative)
    return records


def verify(root, plan):
    records = validate_plan(plan)
    root = root_path(root)
    failures = []
    for record in records:
        try:
            actual = fingerprint(safe_path(root, record['path']))
            if any(actual[key] != record[key] for key in ('bytes', 'sha256')):
                failures.append({'path': record['path'], 'reason': '内容或大小与清单不一致'})
        except (ValueError, OSError) as exc:
            failures.append({'path': record['path'], 'reason': str(exc)})
    return {'ok': not failures, 'checked': len(records), 'plan_sha256': plan_digest(plan), 'failures': failures}


def cleanup_plan(root, plan):
    checked = verify(root, plan)
    invalid = {x['path']: x['reason'] for x in checked['failures']}
    candidates, retained = [], []
    for record in plan['records']:
        reasons = []
        if record['path'] in invalid:
            reasons.append(invalid[record['path']])
        if record.get('category') not in ('rebuildable', 'process'):
            reasons.append('核心/证据/未知类别不自动处置')
        for field in ('unique', 'unresolved_failure', 'active', 'recovery_required'):
            if record.get(field) is not False:
                reasons.append(f'{field} 为真或待核验')
        for field in ('retention_satisfied', 'reproducible'):
            if record.get(field) is not True:
                reasons.append(f'{field} 未确认成立')
        for field in ('rationale', 'rebuild_method', 'retention_condition', 'authorization_ref'):
            if not isinstance(record.get(field), str) or not record[field].strip():
                reasons.append(f'缺少 {field}')
        if not isinstance(record.get('allowed_actions'), list) or 'quarantine' not in record['allowed_actions']:
            reasons.append('没有精确隔离授权记录')
        if reasons:
            retained.append({'path': record['path'], 'reasons': reasons})
        else:
            candidates.append(record['path'])
    return {'ok': checked['ok'], 'plan_sha256': plan_digest(plan), 'candidates': candidates,
            'retained': retained, 'default_action': 'review-only', 'bytes': sum(
                r['bytes'] for r in plan['records'] if r['path'] in candidates)}


def load_json(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('JSON 必须是普通文件，大小不超过 5 MiB')
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data, exclusive=False, *, max_bytes=None):
    path = Path(path)
    if not path.parent.is_dir() or path.is_symlink() or any(p.is_symlink() for p in [path.parent, *path.parent.parents]):
        raise ValueError('输出目录必须已存在且不能通过符号链接写入')
    if exclusive:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, temp = tempfile.mkstemp(prefix='.hutools-receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            if max_bytes is not None and os.fstat(stream.fileno()).st_size > max_bytes:
                raise OverflowError('JSON 超过主回执的读取上限')
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def destination_path(root, destination):
    root = root_path(root)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('目标必须是尚不存在的新批次目录，不覆盖已有资产')
    parent = destination.parent
    if not parent.is_dir() or any(p.is_symlink() for p in [parent, *parent.parents]):
        raise ValueError('目标父目录必须已存在且不含符号链接')
    destination = parent.resolve() / destination.name
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError('目标与来源工程目录不能包含彼此')
    return destination


def checked_source(root, plan):
    root = root_path(root)
    result = verify(root, plan)
    if not result['ok']:
        raise ValueError('输入核验失败，未执行操作：' + json.dumps(result['failures'], ensure_ascii=False))
    return root


def begin_receipt(root, plan, destination, operation):
    destination.mkdir(mode=0o700)
    (destination / 'files').mkdir(mode=0o700)
    receipt = {'schema_version': SCHEMA_VERSION, 'operation': operation, 'status': 'in-progress',
               'plan_sha256': plan_digest(plan), 'started_at': now(), 'completed_paths': []}
    write_json(destination / 'plan.json', plan, exclusive=True)
    write_json(destination / 'receipt.json', receipt, exclusive=True)
    return receipt


def archive(root, plan, destination):
    root = checked_source(root, plan)
    destination = destination_path(root, destination)
    if shutil.disk_usage(destination.parent).free < sum(r['bytes'] for r in plan['records']):
        raise ValueError('目标可用空间小于本批内容大小，未开始复制')
    receipt = begin_receipt(root, plan, destination, 'archive')
    try:
        for record in plan['records']:
            source = safe_path(root, record['path'])
            target = safe_path(destination / 'files', record['path'], must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as inp, target.open('xb') as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if fingerprint(target) != {k: record[k] for k in ('bytes', 'sha256')}:
                raise ValueError('复制内容与清单不一致')
            if fingerprint(source) != {k: record[k] for k in ('bytes', 'sha256')}:
                raise ValueError('复制期间原件变化')
            receipt['completed_paths'].append(record['path'])
            write_json(destination / 'receipt.json', receipt)
        if not verify(root, plan)['ok'] or not verify(destination / 'files', plan)['ok']:
            raise ValueError('最终完整性核验失败')
        receipt.update(status='complete', finished_at=now())
        write_json(destination / 'receipt.json', receipt)
        return {'ok': True, 'operation': 'archive', 'source_preserved': True,
                'receipt': str(destination / 'receipt.json'), 'plan_sha256': plan_digest(plan)}
    except (ValueError, OSError) as exc:
        receipt.update(status='partial', error=str(exc), finished_at=now())
        write_json(destination / 'receipt.json', receipt)
        raise ValueError('归档未完成；原件和部分目标均保留，见目标 receipt.json') from exc


def confirmation(plan, token):
    if token != plan_digest(plan):
        raise ValueError('确认值必须等于当前精确清单 SHA-256；该值只绑定清单，不替代用户授权')


def quarantine(root, plan, destination, token):
    confirmation(plan, token)
    root = checked_source(root, plan)
    review = cleanup_plan(root, plan)
    if review['retained']:
        raise ValueError('清单存在应保留项，必须单独形成获批的候选清单')
    destination = destination_path(root, destination)
    if any(safe_path(root, r['path']).stat().st_dev != destination.parent.stat().st_dev for r in plan['records']):
        raise ValueError('隔离仅支持同文件系统；跨盘请先 archive 核验并另行审查')
    receipt = begin_receipt(root, plan, destination, 'quarantine')
    try:
        for record in plan['records']:
            source = safe_path(root, record['path'])
            if fingerprint(source) != {k: record[k] for k in ('bytes', 'sha256')}:
                raise ValueError('隔离前原件变化')
            target = safe_path(destination / 'files', record['path'], must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('隔离目标冲突')
            os.rename(source, target)
            receipt['completed_paths'].append(record['path'])
            write_json(destination / 'receipt.json', receipt)
            if fingerprint(target) != {k: record[k] for k in ('bytes', 'sha256')}:
                raise ValueError('隔离期间原件变化，保留隔离文件以人工复核')
        receipt.update(status='complete', finished_at=now())
        write_json(destination / 'receipt.json', receipt)
        return {'ok': True, 'operation': 'quarantine', 'permanent_delete': False,
                'receipt': str(destination / 'receipt.json'), 'plan_sha256': plan_digest(plan)}
    except (ValueError, OSError) as exc:
        receipt.update(status='partial', error=str(exc), finished_at=now())
        write_json(destination / 'receipt.json', receipt)
        raise ValueError('隔离未完成，未自动回滚或删除；根据精确清单检查原位置/隔离位置后 restore') from exc


def write_restore_progress(path, receipt, detail_path):
    """主回执须可直接重读。超出的历史仅供审阅，续接始终核验两侧文件。"""
    if not detail_path.exists():
        try:
            write_json(path, receipt, max_bytes=MAX_JSON_BYTES)
            return
        except OverflowError:
            pass
    # 同一次尝试更新同一详情；每次重试使用新路径，保留前一尝试及其历史链接。
    write_json(detail_path, receipt)
    head = {'schema_version': SCHEMA_VERSION, 'operation': 'quarantine',
            'plan_sha256': receipt['plan_sha256'], 'status': receipt['status'],
            'restore_detail': detail_path.name,
            'moved_in_attempt': len(receipt['restore_attempts'][-1]['moved_paths'])}
    if receipt.get('quarantine_status') in ('in-progress', 'partial', 'complete'):
        head['quarantine_status'] = receipt['quarantine_status']
    if receipt['status'] == 'restored':
        head['restored_at'] = receipt['restored_at']
    write_json(path, head)


def restore(root, plan, quarantine_dir, token):
    confirmation(plan, token)
    root = root_path(root)
    directory = root_path(quarantine_dir)
    receipt_path = safe_path(directory, 'receipt.json')
    receipt = load_json(receipt_path)
    if receipt.get('operation') != 'quarantine' or receipt.get('plan_sha256') != plan_digest(plan):
        raise ValueError('恢复目录不属于该隔离清单')
    moves = []
    # 全量预检后再移动；中断后重新按内容和精确位置识别，不仅相信回执。
    for record in validate_plan(plan):
        source = safe_path(directory / 'files', record['path'], must_exist=False)
        target = safe_path(root, record['path'], must_exist=False)
        expected = {k: record[k] for k in ('bytes', 'sha256')}
        if source.exists():
            if target.exists() or fingerprint(source) != expected:
                raise ValueError('恢复位置冲突或隔离文件已变化，保持原位')
            # 目标的最近已存在父目录决定移动是否跨盘。
            parent = next(p for p in target.parents if p.exists())
            if source.stat().st_dev != parent.stat().st_dev:
                raise ValueError('恢复仅支持同文件系统')
            moves.append((record['path'], source, target))
        elif not target.exists() or fingerprint(target) != expected:
            raise ValueError('原位置和隔离位置不能确认完整资产，停止恢复')
    result = {'ok': True, 'operation': 'restore', 'restored': len(moves),
              'plan_sha256': receipt['plan_sha256']}
    if not moves and receipt.get('status') == 'restored':
        return result

    # 保留隔离完成/中断时的证据。每次恢复单独记录，以便重试后仍能追溯失败。
    if receipt.get('status') in ('in-progress', 'partial', 'complete'):
        receipt.setdefault('quarantine_status', receipt['status'])
    attempts = receipt.setdefault('restore_attempts', [])
    if not isinstance(attempts, list):
        raise ValueError('恢复回执的 restore_attempts 必须是列表')
    attempt = {'started_at': now(), 'status': 'restoring', 'moved_paths': []}
    attempts.append(attempt)
    receipt['status'] = 'restoring'
    receipt.pop('moved_in_attempt', None)
    detail_path = directory / f'restore-history-{uuid4().hex}.json'
    if detail_path.exists() or detail_path.is_symlink():
        raise ValueError('恢复详情路径冲突')
    write_restore_progress(receipt_path, receipt, detail_path)
    try:
        for relative, source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ValueError(f'恢复期间出现目标冲突：{relative}')
            os.rename(source, target)
            attempt['moved_paths'].append(relative)
            write_restore_progress(receipt_path, receipt, detail_path)
        if not verify(root, plan)['ok']:
            raise ValueError('恢复后核验失败，保留两侧资产并人工复核')
        attempt.update(status='restored', finished_at=now())
        finished = {**receipt, 'status': 'restored', 'restored_at': attempt['finished_at']}
        write_restore_progress(receipt_path, finished, detail_path)
    except (ValueError, OSError) as exc:
        attempt.update(status='restore-partial', error=str(exc), finished_at=now())
        receipt['status'] = 'restore-partial'
        write_restore_progress(receipt_path, receipt, detail_path)
        raise ValueError('恢复未完成；保留两侧文件，根据清单核对后可再次 restore') from exc
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('plan', help='读取精确文件列表，创建不可覆盖的指纹清单')
    create.add_argument('--root', required=True)
    create.add_argument('--spec', required=True)
    create.add_argument('--out', required=True)
    for name in ('verify', 'cleanup', 'archive', 'quarantine', 'restore'):
        command = sub.add_parser(name)
        command.add_argument('--root', required=True)
        command.add_argument('--plan', required=True)
        if name in ('archive', 'quarantine', 'restore'):
            command.add_argument('--destination', required=True)
        if name in ('quarantine', 'restore'):
            command.add_argument('--confirm', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'plan':
            spec = load_json(args.spec)
            if not isinstance(spec, dict):
                raise ValueError('spec 必须是对象')
            plan = make_plan(args.root, spec.get('project_id'), spec.get('task_id'), spec.get('files'))
            write_json(args.out, plan, exclusive=True)
            result = {'ok': True, 'plan_sha256': plan_digest(plan), 'files': len(plan['records'])}
        else:
            plan = load_json(args.plan)
            if args.action == 'verify':
                result = verify(args.root, plan)
            elif args.action == 'cleanup':
                result = cleanup_plan(args.root, plan)
            elif args.action == 'archive':
                result = archive(args.root, plan, args.destination)
            elif args.action == 'quarantine':
                result = quarantine(args.root, plan, args.destination, args.confirm)
            else:
                result = restore(args.root, plan, args.destination, args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
