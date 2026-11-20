#!/usr/bin/env python3
"""盘点云效单号目录、复用可核验附件、保存详细分析报告。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from uuid import uuid4

from catalog import writer
from lifecycle import canonical, fingerprint, load_json, relative_path, root_path, safe_path, write_json


REPORT = "详细分析报告.md"


def component(value):
    path = relative_path(value)
    if len(path.parts) != 1 or value.startswith(".") or not value.isprintable():
        raise ValueError("单号和文件名须为单个可见路径分量")
    return value


def inventory(directory):
    files = []
    skipped = []

    def walk_error(error):
        raise error

    for base, dirs, names in os.walk(directory, onerror=walk_error):
        base = Path(base)
        for name in dirs[:]:
            if (base / name).is_symlink():
                dirs.remove(name)
                skipped.append(str((base / name).relative_to(directory)))
        for name in names:
            path = base / name
            rel = str(path.relative_to(directory))
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                files.append({"path": rel, "size": info.st_size})
            else:
                skipped.append(rel)
    return {"path": str(directory), "files": sorted(files, key=lambda f: f["path"]),
            "skipped": sorted(skipped)}


def attachment(directory, args):
    identity = {k: getattr(args, k) for k in ("project", "attachment", "revision")}
    if not all(v.strip() for v in identity.values()):
        raise ValueError("项目、附件和修订标识须来自本单")
    key = hashlib.sha256(canonical(identity)).hexdigest()
    folder = safe_path(directory, f"云效附件/{key}", must_exist=False)
    record = safe_path(directory, f"云效附件/{key}/来源.json", must_exist=False)
    expected = None
    if record.exists():
        meta = load_json(record)
        if not isinstance(meta, dict) or meta.get("source") != identity:
            raise ValueError(f"请核对附件来源记录：{record}")
        generation = component(meta["copy"])
        name = component(meta["name"])
        copy = safe_path(folder, f"{generation}/{name}", must_exist=False)
        expected = meta["fingerprint"]
        if copy.is_file() and fingerprint(copy) == expected:
            return {"path": str(copy)}
    if args.action == "lookup":
        return {"path": None}

    name = component(args.name)
    download = Path(args.file)
    if download.is_symlink() or not download.is_file():
        raise ValueError("下载输入须为已经完成的普通文件")
    folder.mkdir(parents=True, exist_ok=True)
    generation = uuid4().hex
    destination = folder / generation
    # 文件写完并核对后才提交来源记录。修复损坏附件时保留此前的副本。
    with tempfile.TemporaryDirectory(dir=folder) as staging:
        staged = Path(staging) / "payload"
        shutil.copyfile(download, staged)
        digest = fingerprint(staged)
        if digest != fingerprint(download):
            raise ValueError("下载文件在归档期间变化，请重新核对附件")
        if record.exists() and digest != expected:
            raise ValueError("补取内容与该修订的原始指纹不同，请核对云效修订标识和下载结果")
        destination.mkdir()
        copy = destination / name
        staged.replace(copy)
    write_json(record, {"source": identity, "copy": generation, "name": name,
                        "fingerprint": digest})
    return {"path": str(copy)}


def replace_bytes(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def save_report(directory, body):
    if not body.decode("utf-8").strip():
        raise ValueError("详细分析报告不能为空")
    path = safe_path(directory, REPORT, must_exist=False)
    if path.exists():
        if not path.is_file():
            raise ValueError("详细分析报告的位置已有其他类型的文件")
        old = path.read_bytes()
        if old == body:
            return {"path": str(path)}
        digest = hashlib.sha256(old).hexdigest()
        backup = safe_path(directory, f"历史报告/{digest}.md", must_exist=False)
        backup.parent.mkdir(exist_ok=True)
        if backup.exists():
            if not backup.is_file() or backup.read_bytes() != old:
                raise ValueError(f"历史报告内容不匹配，请核对：{backup}")
        else:
            replace_bytes(backup, old)
    replace_bytes(path, body)
    return {"path": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("inspect", "lookup", "store", "report"):
        cmd = actions.add_parser(name)
        cmd.add_argument("--parent", type=Path, required=True)
        cmd.add_argument("--issue", required=True)
        if name in ("lookup", "store"):
            for field in ("project", "attachment", "revision"):
                cmd.add_argument("--" + field, required=True)
        if name == "store":
            cmd.add_argument("--file", type=Path, required=True)
            cmd.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        parent = root_path(args.parent)
        directory = safe_path(parent, component(args.issue), must_exist=False)
        if args.action == "inspect":
            directory.mkdir(mode=0o700, exist_ok=True)
        # 各次 CLI 调用共用单号目录 inode 锁，报告留史与新版本切换串行。
        with writer(directory / REPORT):
            if args.action == "inspect":
                result = inventory(directory)
            elif args.action == "report":
                result = save_report(directory, sys.stdin.buffer.read())
            else:
                result = attachment(directory, args)
        print(json.dumps(result, ensure_ascii=False))
    except (KeyError, ValueError, TypeError, OSError) as exc:
        parser.exit(1, f"云效单据归档失败：{exc}\n")


if __name__ == "__main__":
    main()
