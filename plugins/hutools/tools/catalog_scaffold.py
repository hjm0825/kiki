#!/usr/bin/env python3
"""为已确认的空工程建立可携带的双环阅读入口。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

from lifecycle import relative_path, root_path
from workflow import no_links


SECTIONS = {
    '外环/阶段': '按项目实际的大阶段和小阶段说明目标、用法、结果与待决定事项。',
    '外环/模块': '按模块汇总修改原因、效果、验证范围，并指向所属阶段。',
    '内环/阶段': '按阶段记录需求决定、实施方法、验证证据及其适用范围。',
    '内环/模块': '保存模块修改标识、版本、验证与恢复依据，供阶段记录引用。',
    '内环/数据': '记录原始资料位置、归属、采集条件与本地资料库批次。已有数据沿原位置引用。',
}


def files_for_layout():
    index = ['# 项目文档', '', '从外环查看方案与效果，进入内环追溯开发与验证依据。', '']
    files = {}
    for section, purpose in SECTIONS.items():
        label = section.replace('/', ' · ')
        index.append(f'- [{label}]({section}/README.md)　{purpose}')
        files[Path(section) / 'README.md'] = f'# {label}\n\n{purpose}\n'
    files[Path('README.md')] = '\n'.join(index) + '\n'
    return files


def create_layout(root, directory):
    target = no_links(root / relative_path(directory))
    expected = files_for_layout()
    dirs = {part for file in expected for part in file.parents}
    # 先检查整个布局。任何冲突都在写第一份文件之前报告。
    for folder in dirs:
        path = no_links(target / folder)
        if path.exists() and not path.is_dir():
            raise ValueError(f'目录位置已有文件：{folder}')
    for file in expected:
        path = no_links(target / file)
        if path.exists() and not path.is_file():
            raise ValueError(f'说明文件位置已有其他对象：{file}')
    if all((target / file).is_file() for file in expected):
        return {'directory': directory, 'created': [], 'status': 'existing'}
    if target.exists():
        for folder in dirs:
            path = target / folder
            if not path.exists():
                continue
            for entry in path.iterdir():
                name = entry.relative_to(target)
                if name not in expected and name not in dirs:
                    raise ValueError(f'已有资料，请围绕实际内容整理：{name}')
                if name in expected and entry.read_text(encoding='utf-8') != expected[name]:
                    raise ValueError(f'已有自定义文档，须先商定其归属：{name}')
    created = []
    for file, content in expected.items():
        destination = target / file
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        with destination.open('x', encoding='utf-8') as output:
            output.write(content)
        created.append(file.as_posix())
    return {'directory': directory, 'created': created, 'status': 'initialized'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--directory', default='docs', help='工程内的文档区相对位置')
    args = parser.parse_args()
    try:
        root = root_path(args.root)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = create_layout(root, args.directory)
        finally:
            os.close(fd)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        print(f'初始化双环目录：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
