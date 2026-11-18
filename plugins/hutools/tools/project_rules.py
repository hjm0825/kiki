"""按已核定条目渲染工程规则；不从仓库文字推定授权。"""

import argparse
import difflib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


def single_line(value):
    if not isinstance(value, str) or not value.strip() or len(value.splitlines()) != 1:
        raise ValueError("规则及出处须为非空单行文字")
    return value.strip()


def render(root, decisions):
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("没有已核定条目，请先向用户确认适用规则")
    lines = ["# 工程开发约定", ""]
    for item in decisions:
        if not isinstance(item, dict) or set(item) not in (
            {"rule", "source"}, {"rule", "confirmation"}
        ):
            raise ValueError("每项须提供 rule 和唯一的 source 或 confirmation")
        rule = single_line(item["rule"])
        if "source" in item:
            source = Path(single_line(item["source"]))
            if source.is_absolute() or ".." in source.parts:
                raise ValueError("出处必须是工程根相对路径")
            resolved = (root / source).resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError("出处不是工程内文件")
            origin = f"工程出处：`{source.as_posix()}`"
        else:
            origin = "确认依据：" + single_line(item["confirmation"])
        lines.append(f"- {rule}（{origin}）")
    return "\n".join(lines) + "\n"


def initialize(root, content):
    target = root / "AGENTS.md"
    with tempfile.NamedTemporaryFile(dir=root, prefix=".hutools-agents-") as draft:
        draft.write(content)
        draft.flush()
        try:
            os.link(draft.name, target)
            return "已创建"
        except FileExistsError:
            pass
    with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as existing:
        metadata = os.fstat(existing.fileno())
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size == len(content) and existing.read() == content:
            return "内容一致，未改动"
    raise ValueError("已有 AGENTS.md 与候选不同；请审阅差异，不自动覆盖")


def review_path(root, value):
    path = Path(single_line(value))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"预览路径须位于工程内：{value}")
    item = root / path
    if item.resolve(strict=True) != item or not item.is_file():
        raise ValueError(f"预览只接受现存、无符号链接的普通文件：{path}")
    return path.as_posix()


def migration_preview(root, decisions, content, rows):
    target = root / "AGENTS.md"
    exists = target.exists() or target.is_symlink()
    before = ""
    if exists:
        review_path(root, "AGENTS.md")
        before = target.read_bytes().decode("utf-8")
    root_action = "create"
    if exists:
        root_action = "keep" if before == content else "replace"
    allowed = {
        "rules": {"keep", "update", "retire", "pending"},
        "template": {"keep", "update", "retire", "pending"},
        "mixed": {"keep", "update", "pending"},
        "document": {"keep", "update", "pending"},
        "unknown": {"pending"},
    }
    files = {}
    if not isinstance(rows, list):
        raise ValueError("逐文件审阅清单须为数组")
    sources = {(root / d["source"].strip()).resolve() for d in decisions if "source" in d}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "path", "role", "scope", "action", "reason", "rules", "consumers"
        }:
            raise ValueError("审阅项字段不完整或未知")
        path = review_path(root, row["path"])
        if path == "AGENTS.md" or path in files:
            raise ValueError(f"重复或与根目标冲突：{path}")
        role, action, scope = (single_line(row[key]) for key in ("role", "action", "scope"))
        if action not in allowed.get(role, ()) or scope not in ("root", "nested"):
            raise ValueError(f"文件职责、范围与处置不一致：{path}")
        if (scope == "nested" or Path(path).name == "AGENTS.md") and action not in ("keep", "pending"):
            raise ValueError(f"保留下级作用域：{path}")
        numbers = row["rules"]
        if not isinstance(numbers, list) or any(
            type(n) is not int or not 1 <= n <= len(decisions) for n in numbers
        ) or len(set(numbers)) != len(numbers):
            raise ValueError(f"迁入规则序号无效：{path}")
        if action == "retire":
            if role == "rules" and not numbers:
                raise ValueError(f"未登记已迁入的规则：{path}")
            if root / path in sources:
                raise ValueError(f"候选仍引用拟退役出处：{path}")
        if not isinstance(row["consumers"], list):
            raise ValueError(f"引用该文件的活动路径须为数组：{path}")
        consumers = [review_path(root, p) for p in row["consumers"]]
        files[path] = dict(row, path=path, role=role, action=action, scope=scope,
                           reason=single_line(row["reason"]), consumers=consumers)

    # 只有已安排更新的活动引用才能与退役建议并列；清单本身不授予写权限。
    for path, row in files.items():
        if row["action"] == "retire":
            for consumer in row["consumers"]:
                if consumer == "AGENTS.md" and root_action == "replace":
                    continue
                if files.get(consumer, {}).get("action") != "update":
                    raise ValueError(f"拟退役文件仍有未安排更新的活动引用：{path} ← {consumer}")
    diff = difflib.unified_diff(before.splitlines(True), content.splitlines(True),
                                fromfile="AGENTS.md" if exists else "/dev/null",
                                tofile="AGENTS.md")
    diff_text = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                        for line in diff)
    return {
        "root_action": root_action,
        "diff": diff_text,
        "decisions": decisions,
        "files": list(files.values()),
        "pending": [p for p, row in files.items() if row["action"] == "pending"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="工程绝对路径")
    parser.add_argument("--decisions", type=Path, help="已核定的 JSON 条目输入")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="仅首次创建，默认输出预览")
    mode.add_argument("--migration-preview", type=Path, help="逐文件审阅清单，只读输出迁移差异和处置")
    mode.add_argument("--apply", type=Path, metavar="批次目录", help="执行或接续已审阅的批次")
    parser.add_argument("--prepare", type=Path, metavar="新批次目录", help="在工程外保存原件和精确差异")
    parser.add_argument("--changes", type=Path, help="已审阅的导航和绑定新正文 JSON")
    parser.add_argument("--confirm", help="用户所确认的准备结果 SHA256")
    args = parser.parse_args()
    try:
        if not args.root.is_absolute() or not args.root.is_dir():
            raise ValueError("请指定已存在的工程绝对路径")
        root = args.root.resolve(strict=True)
        if args.apply or args.prepare:
            if args.root != root:
                raise ValueError("切换目标必须使用真实工程绝对路径")
            # 隔离 Python 不自动将脚本目录加入模块路径。
            if sys.flags.isolated:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
            import rules_migration
        if args.apply:
            if not args.confirm or args.decisions or args.prepare or args.changes:
                raise ValueError("--apply 须单独与 --root、--confirm 使用")
            print(json.dumps(rules_migration.apply(root, args.apply, args.confirm), ensure_ascii=False))
            return
        if (not args.decisions or args.confirm or (args.prepare and not args.migration_preview)
                or (args.changes and not args.prepare)):
            raise ValueError("生成须提供 --decisions；--prepare 配合 --migration-preview 使用")
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
        content = render(root, decisions)
        if args.migration_preview:
            rows = json.loads(args.migration_preview.read_text(encoding="utf-8"))
            report = migration_preview(root, decisions, content, rows)
            if args.prepare:
                changes = json.loads(args.changes.read_bytes()) if args.changes else {}
                report = rules_migration.prepare(root, report, content, changes, args.prepare)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.init:
            print(initialize(root, content.encode("utf-8")))
        else:
            sys.stdout.write(content)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"规则未生成：{exc}\n")


if __name__ == "__main__":
    main()
