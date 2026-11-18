"""按文档管理员的语义分类清单迁移实体 Markdown，原始证据就地引用。"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

from lifecycle import relative_path
from workflow import no_links

LINK = re.compile(r'(?P<open>\]\()(?P<target>[^()\n]+)\)')
RINGS = ('外环', '内环')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local(root, value):
    path = no_links(root / relative_path(value))
    if any(part.startswith('.') for part in path.relative_to(root).parts):
        raise ValueError(f'文档路径不能进入隐藏目录：{value}')
    return path


def markdown(root, value):
    path = local(root, value)
    if path.suffix.lower() != '.md' or path.name == 'AGENTS.md':
        raise ValueError(f'仅支持普通工程 Markdown：{value}')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError(f'文档非独立普通文件：{value}')
    return path


def inventory(root, directory):
    base = local(root, directory)
    found = []
    for p in sorted(base.rglob('*.md')):
        if any(part.startswith('.') for part in p.relative_to(base).parts):
            continue
        markdown(root, p.relative_to(root).as_posix())
        found.append(p.relative_to(root).as_posix())
    return found


def refs(text, transform):
    # 围栏中的示例和原始输出逐字保留。
    result = []
    fence = None
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token.startswith(fence):
                fence = None
        elif fence is None:
            line = LINK.sub(lambda m: '](' + transform(m['target']) + ')', line)
        result.append(line)
    return ''.join(result)


def destination(parent, ref):
    path = unquote(ref.split('#', 1)[0])
    if not path or path.startswith(('/', '<')) or re.match(r'^[a-zA-Z][\w+.-]*:', path):
        return None
    return Path(os.path.normpath(parent / path))


def discover(root, plan):
    result = {root / name for name in inventory(root, plan['directory'])}
    if (root / '.git').exists():
        names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-co',
                                         '--exclude-standard', '-z', '--', '*.md'])
        result.update(root / os.fsdecode(name) for name in names.split(b'\0') if name)
    result.update(local(root, name) for name in plan.get('references', []))
    return {p for p in result if p.is_file() and p.name != 'AGENTS.md' and
            not any(part.startswith('.') for part in p.relative_to(root).parts)}


def coverage(root, layout):
    base = local(root, layout['directory'])
    actual = set(inventory(root, layout['directory']))
    keep = set(layout['keep'])
    unexpected = [n for n in actual - keep if not in_ring(root / n, base)]
    if unexpected:
        raise ValueError('待归位文档：' + '、'.join(sorted(unexpected)[:8]))
    for name in layout['targets']:
        if not markdown(root, name).is_file():
            raise ValueError('缺少实体文档：' + name)
    verify_pairs(root, base, layout['pairs'], lambda p: p.read_text())
    if keep:
        known = {root / name for name in actual}
        pending = [markdown(root, name) for name in keep]
        seen = set(pending)
        while pending:
            page = pending.pop()
            def visit(ref):
                target = destination(page.parent, ref)
                if target in known and target not in seen:
                    pending.append(target)
                    seen.add(target)
                return ref
            refs(page.read_text(), visit)
        missing = sorted(str(p.relative_to(root)) for p in known - seen)
        if missing:
            raise ValueError('文档入口无法到达：' + '、'.join(missing[:8]))


def in_ring(path, base):
    return any(path.is_relative_to(base / ring) for ring in RINGS)


def verify_pairs(root, base, pairs, read):
    if not pairs:
        raise ValueError('缺少阶段或模块的双环阅读路径')
    for outer, inner in pairs:
        a, b = markdown(root, outer), markdown(root, inner)
        if not a.is_relative_to(base / RINGS[0]) or not b.is_relative_to(base / RINGS[1]):
            raise ValueError(f'阅读路径跨越实体目录边界：{outer} / {inner}')
        links = set()
        def collect(ref):
            links.add(destination(a.parent, ref))
            return ref
        refs(read(a), collect)
        if b not in links or not read(b).strip():
            raise ValueError(f'外环未连接内环正文：{outer} → {inner}')


def build(root, plan):
    base = local(root, plan['directory'])
    moves = {markdown(root, a): markdown(root, b) for a, b in plan['moves'].items()}
    pages = {markdown(root, n): s for n, s in plan['pages'].items()}
    targets = list(moves.values()) + list(pages)
    if (len(set(targets)) != len(targets) or any(p.exists() for p in targets)
            or any(not in_ring(p, base) for p in targets)
            or any(not p.is_relative_to(base) or not p.is_file() for p in moves)):
        raise ValueError('来源缺失、目标冲突或目标在双环目录之外')
    patches = {markdown(root, n): edits for n, edits in plan.get('edits', {}).items()}
    keep = set(plan['keep'])
    for n in inventory(root, plan['directory']):
        if not in_ring(root / n, base) and n not in keep and root / n not in moves:
            raise ValueError('清单遗漏工程文档：' + n)
    original, output = {}, {}
    for f in sorted(discover(root, plan) | set(moves) | set(patches)):
        src = markdown(root, f.relative_to(root).as_posix())
        old = src.read_bytes()
        dest = moves.get(src, src)
        def replace(ref):
            point = destination(src.parent, ref)
            if point is None or (point not in moves and src == dest):
                return ref
            path, sep, frag = ref.partition('#')
            if point not in moves and dest.parent == src.parent:
                return ref
            result = os.path.relpath(moves.get(point, point), dest.parent)
            return result + ('/' if path.endswith('/') else '') + sep + frag
        new = refs(old.decode(), replace)
        for before, after in patches.get(src, []):
            if not before or new.count(before) != 1:
                raise ValueError(f'正文原文或替换位置不唯一：{src} {before[:55]}')
            new = new.replace(before, after)
        data = new.encode()
        if data != old or dest != src:
            original[src] = old
            output[dest] = data
    # 必要时补齐入口或小节，其正文也须在预览时存在。
    for p in patches:
        if not p.is_file():
            raise ValueError(f'需要调整的入口不存在：{p}')
    for p, body in pages.items():
        output[p] = body.encode()
    def future(p):
        return output[p].decode() if p in output else p.read_text()
    verify_pairs(root, base, plan['pairs'], future)
    for p in targets:
        def validate(ref):
            dest = destination(p.parent, ref)
            if dest and (dest in moves or (dest not in output and not dest.exists())):
                raise ValueError(f'整理后引用失效：{p} → {ref}')
            return ref
        refs(future(p), validate)
    layout = {'directory': plan['directory'], 'keep': sorted(keep), 'pairs': plan['pairs'],
              'targets': sorted(str(p.relative_to(root)) for p in targets)}
    return original, output, moves, layout


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
        name = Path(tmp.name)
    try:
        name.chmod(mode)
        name.replace(path)
    finally:
        name.unlink(missing_ok=True)


def arrange(root, plan, recovery, apply, previous=None):
    signature = digest(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode())
    if previous and signature in [previous.get('digest'), *previous.get('earlier_batches', [])]:
        coverage(root, previous)
        return previous, False
    # 若全部文档已落盘、仅状态写入中断，可核对原收据后恢复登记。
    if apply and recovery is not None and recovery.exists():
        archive = no_links(recovery.absolute())
        if archive.is_relative_to(root):
            raise ValueError('恢复包须在工程外')
        with ZipFile(archive) as z:
            record = json.loads(z.read('files.json'))
            receipt = record.get('layout', {})
            if record.get('plan') != signature or receipt.get('digest') != signature:
                raise ValueError('恢复包与本批输入不匹配')
            if record.get('moves') != plan['moves'] or receipt['directory'] != plan['directory']:
                raise ValueError('恢复包归属不匹配')
            if any(markdown(root, n).exists() for n in plan['moves']):
                raise ValueError('原件仍在，保留恢复包以核对中断位置')
            for name, fingerprint in record['targets'].items():
                if digest(markdown(root, name).read_bytes()) != fingerprint:
                    raise ValueError('恢复时目标文件与已写入指纹不同：' + name)
            coverage(root, receipt)
            return receipt, True
    original, output, moves, layout = build(root, plan)
    layout.update(digest=signature, moved=len(moves), created=len(plan['pages']),
                  updated=len(original) - len(moves))
    if previous:
        # 小批导航修订也必须保留已归位阶段与模块的完成依据。
        relocate = lambda name: plan['moves'].get(name, name)
        old_targets = {relocate(n) for n in previous['targets']}
        layout['targets'] = sorted(old_targets | set(layout['targets']))
        inherited = {relocate(a): relocate(b) for a, b in previous['pairs']}
        inherited.update(dict(layout['pairs']))
        layout['pairs'] = list(map(list, inherited.items()))
        layout['earlier_batches'] = [*previous.get('earlier_batches', []), previous['digest']]
    if not apply:
        return layout, False
    base_dir = local(root, layout['directory'])
    recovery = no_links(recovery.absolute())
    if recovery.is_relative_to(root) or recovery.exists():
        raise ValueError('恢复包须是工程外新的本机文件')
    # 先留存工作树字节。意外断电时使用包内路径映射定位原件和部分写入。
    with ZipFile(recovery, 'x', ZIP_DEFLATED) as z:
        for p, data in original.items():
            z.writestr('before/' + str(p.relative_to(root)), data)
        z.writestr('files.json', json.dumps({
            'plan': signature, 'targets': {str(p.relative_to(root)): digest(v) for p,v in output.items()},
            'moves': plan['moves'], 'layout': layout}, ensure_ascii=False))
    with ZipFile(recovery) as z:
        if z.testzip() or any(z.read('before/' + str(p.relative_to(root))) != v for p,v in original.items()):
            raise ValueError('工作树恢复包核验失败')
    written, retired = [], []
    try:
        for p, data in output.items():
            expected = original.get(p)
            if (p.read_bytes() if p.exists() else None) != expected:
                raise ValueError(f'预览后目标已变化：{p}')
            write(p, data)
            written.append(p)
        for p in moves:
            if p.read_bytes() != original[p]:
                raise ValueError(f'迁移期间原件已变化：{p}')
            p.unlink()
            retired.append(p)
        coverage(root, layout)
    except BaseException:
        for p in retired:
            if not p.exists():
                write(p, original[p])
        for p in reversed(written):
            if p.exists() and p.read_bytes() == output[p]:
                if p in original:
                    write(p, original[p])
                else:
                    p.unlink()
        raise
    for parent in sorted({q for p in moves for q in p.parents if q != base_dir and q.is_relative_to(base_dir)}, key=lambda x: -len(x.parts)):
        try:
            parent.rmdir()
        except OSError:
            pass  # 保留非空目录和并发新文件。
    return layout, True
