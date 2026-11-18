#!/usr/bin/env python3
"""检查 HuTools 分发包与引用；仅读取文件，所有路径相对仓库或插件根。"""

import argparse
import json
import ast
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


REQUIRED_SKILLS = {
    "hutools", "grill-with-docs", "grilling", "domain-modeling",
    "research", "codebase-design", "tdd", "diagnosing-bugs", "code-review",
}
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\n]+)\)")


def contained(path, boundary):
    """解析符号链接后检查边界，避免 ../ 和链接逃逸。"""
    try:
        path.resolve().relative_to(boundary.resolve())
        return True
    except ValueError:
        return False


def validate(root):
    """返回可定位的错误列表；不执行插件、不请求网络、不修改待验包。"""
    root = Path(root).resolve()
    errors = []
    if not root.is_dir():
        return [f"仓库目录不存在：{root}"]

    files = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            errors.append(f"分发包不允许符号链接：{relative}")
        elif path.is_file():
            files.append(path)
    # 不跟随任何包内链接读取外部内容。
    if errors:
        return errors

    def read_json(path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("顶层必须为对象")
            return value
        except (OSError, ValueError) as exc:
            errors.append(f"{path.relative_to(root)}：{exc}")
            return {}

    marketplace_path = root / ".agents/plugins/marketplace.json"
    marketplace = read_json(marketplace_path)
    entries = marketplace.get("plugins", [])
    if not isinstance(entries, list):
        return errors + ["marketplace.plugins 必须为列表"]
    names = [e.get("name") for e in entries if isinstance(e, dict)]
    if len(names) != len(set(str(n) for n in names)):
        errors.append("marketplace 包含重复插件")
    if names != ["hutools"]:
        errors.append("marketplace 必须且只能声明本版 hutools 插件")
    if not entries or not isinstance(entries[0], dict):
        return errors + ["marketplace 缺少插件条目"]
    entry = entries[0]
    source = entry.get("source", {})
    if not isinstance(source, dict):
        return errors + ["marketplace.source 必须为对象"]
    location = source.get("path", "")
    if not isinstance(location, str) or not location.startswith("./"):
        return errors + ["marketplace.source.path 必须以 ./ 开头"]
    plugin = (root / location).resolve()
    if not contained(plugin, root):
        return errors + ["marketplace.source.path 越界"]
    if source.get("source") != "local" or not plugin.is_dir():
        return errors + ["marketplace 本地插件目录无效"]
    policy = entry.get("policy", {})
    if not isinstance(policy, dict) or policy.get("installation") != "AVAILABLE":
        errors.append("marketplace 缺少 AVAILABLE 安装策略")
    if not isinstance(policy, dict) or policy.get("authentication") != "ON_INSTALL":
        errors.append("marketplace 缺少 ON_INSTALL 认证策略")
    if entry.get("category") != "Productivity":
        errors.append("marketplace.category 应为 Productivity")

    # 当前宿主跳过 AgentPlugin 格式的 hooks；根 manifest 会优先于兼容格式。
    if (plugin / "plugin.json").exists():
        errors.append("portable plugin.json 会遮蔽本版 Codex hooks 兼容入口")
    manifest = read_json(plugin / ".codex-plugin/plugin.json")
    if manifest.get("name") != "hutools":
        errors.append("plugin.json 名称必须为 hutools")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
        errors.append("plugin.json 版本无效")
    if manifest.get("license") != "MIT":
        errors.append("plugin.json 自有内容许可必须为 MIT")
    overlay = manifest
    interface = overlay.get("interface", {})
    if not isinstance(interface, dict) or interface.get("displayName") != "HuTools":
        errors.append("plugin.json 展示名称必须为 HuTools")
    onboarding = overlay.get("onboardingSkill", "")
    if not isinstance(onboarding, str) or not onboarding.startswith("./"):
        errors.append("plugin.json 缺少相对 onboardingSkill")
    elif not contained(plugin / onboarding, plugin) or not (plugin / onboarding).is_file():
        errors.append("plugin.json onboardingSkill 不存在或越界")
    if overlay.get("hooks") != "./hooks/hooks.json":
        errors.append("hooks 必须指向包内 ./hooks/hooks.json")
    if "apps" in overlay:
        errors.append("本版没有外部 apps 服务")
    hook_data = read_json(plugin / "hooks/hooks.json")
    events = hook_data.get("hooks", {})
    if not isinstance(events, dict) or set(events) != {"SessionStart", "PreToolUse", "Stop"}:
        errors.append("hook 事件集不完整或含未交付事件")
    else:
        for event, groups in events.items():
            if not isinstance(groups, list) or not groups:
                errors.append(f"hook {event} 缺少处理器")
                continue
            for group in groups:
                expected_matcher = {"SessionStart": "startup|resume|fork", "PreToolUse": ".*", "Stop": None}[event]
                allowed_group_keys = {"hooks"} if event == "Stop" else {"hooks", "matcher"}
                if not isinstance(group, dict) or set(group) != allowed_group_keys or group.get("matcher") != expected_matcher:
                    errors.append(f"hook {event} matcher/配置范围与本版不符")
                handlers = group.get("hooks", []) if isinstance(group, dict) else []
                if not isinstance(handlers, list) or not handlers:
                    errors.append(f"hook {event} 处理器格式错误")
                    continue
                for handler in handlers:
                    if not isinstance(handler, dict) or handler.get("type") != "command" or handler.get("command") != 'python3 "${PLUGIN_ROOT}/hooks/guard.py"':
                        errors.append(f"hook {event} 必须使用包内正式处理器")
                    if not isinstance(handler, dict) or set(handler) != {"type", "command", "timeout"} or type(handler.get("timeout")) is not int or not 1 <= handler["timeout"] <= 60:
                        errors.append(f"hook {event} 必须同步执行且具有有界 timeout")
    for relative in ["AGENTS.md", "hooks/guard.py", "hooks/README.md", "tools/workflow.py", "tools/lifecycle.py", "tools/catalog.py", "tools/catalog.md"]:
        if not (plugin / relative).is_file():
            errors.append(f"缺少运行资源：{relative}")
    for path in files:
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path.relative_to(root)))
            except (SyntaxError, UnicodeError, OSError) as exc:
                errors.append(f"Python 源文件不可解析：{path.relative_to(root)}：{exc}")

    for name in sorted(REQUIRED_SKILLS):
        skill = plugin / "skills" / name / "SKILL.md"
        try:
            text = skill.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"skills/{name}/SKILL.md：{exc}")
            continue
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if not match:
            errors.append(f"skills/{name}/SKILL.md 缺少 frontmatter")
            continue
        fields = dict(re.findall(r"^([a-z_-]+):\s*(.+)$", match.group(1), re.M))
        if fields.get("name") != name or not fields.get("description", "").strip():
            errors.append(f"skills/{name}/SKILL.md 名称或描述无效")
        if "TODO" in text or "[待填写]" in text:
            errors.append(f"skills/{name}/SKILL.md 含未完成占位")

    for path in files:
        if path.name == ".env" or path.name.startswith(".env."):
            errors.append(f"发布包包含环境文件：{path.relative_to(root)}")
        if path.suffix != ".md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(root)} 无法读取：{exc}")
            continue
        for raw in LINK.findall(text):
            target = raw.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            boundary = plugin if contained(path, plugin) else root
            if not contained(resolved, boundary):
                errors.append(f"{path.relative_to(root)} 包外引用：{target}")
            elif not resolved.exists():
                errors.append(f"{path.relative_to(root)} 引用不存在：{target}")

    for relative in ["LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/mattpocock-MIT.txt"]:
        if not (plugin / relative).is_file():
            errors.append(f"缺少发布许可文件：{relative}")
    lock = read_json(plugin / "sources.lock.json")
    if not re.fullmatch(r"[0-9a-f]{40}", str(lock.get("commit", ""))):
        errors.append("sources.lock.json 未固定来源提交")
    records = lock.get("files", [])
    if not isinstance(records, list):
        errors.append("sources.lock.json files 必须为列表")
    else:
        adaptations = set()
        for record in records:
            if not isinstance(record, dict):
                errors.append("sources.lock.json 来源条目必须为对象")
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
                errors.append("sources.lock.json 来源文件缺少 SHA-256")
            if record.get("adapted_skill"):
                adaptations.add(record["adapted_skill"])
        if adaptations != REQUIRED_SKILLS - {"hutools"}:
            errors.append("sources.lock.json 适配技能清单不完整")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate(args.root)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
