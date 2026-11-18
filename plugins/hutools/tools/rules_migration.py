"""将已审阅的规则差异应用于同一工程；工程外批次保存原件与执行进度。"""

from contextlib import contextmanager
import difflib
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from lifecycle import destination_path, fingerprint, plan_digest, root_path, safe_path, write_json


BINDING = ".env.hutools-materials"


def target(root, name):
    path = safe_path(root, name, must_exist=False)
    for directory in path.relative_to(root).parts:
        if directory in {".git", ".codex", ".agents", ".aws"}:
            raise ValueError(f"受保护目录：{name}")
    for parent in path.parents:
        if parent == root:
            break
        if (parent / ".git").exists() or (parent / "AGENTS.md").exists():
            raise ValueError(f"独立工程或规则范围：{name}")
    if path.name == "AGENTS.md" and path.parent != root:
        raise ValueError(f"保留下级规则：{name}")
    return path


def snapshot(path):
    """保留原始换行和权限；拒绝设备、链接、特殊权限及读取时漂移。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if not path.parent.is_dir():
            raise ValueError(f"目标父目录不存在：{path.parent}")
        return None, None
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(fd)
        mode = stat.S_IMODE(st.st_mode)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or mode & 0o7000:
            raise ValueError(f"目标不是独立普通文件：{path}")
        content = stream.read().decode("utf-8")
        end = path.lstat()
        if (st.st_dev, st.st_ino, st.st_ctime_ns) != (end.st_dev, end.st_ino, end.st_ctime_ns):
            raise ValueError(f"读取期间发生变化：{path}")
        return content, mode


def check_binding(root, text):
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("MATERIALS_LIBRARY="):
        raise ValueError("本机绑定须为一行 MATERIALS_LIBRARY=绝对路径")
    location = Path(lines[0].partition("=")[2])
    if not location.is_absolute() or not location.is_dir():
        raise ValueError("本机资料库位置不可达")
    if any((p / ".git").exists() for p in (root, *root.parents)):
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=False,
                                  capture_output=True).returncode
        if git("check-ignore", "-q", "--", BINDING) != 0:
            raise ValueError("本机绑定须未被跟踪且已被 Git 忽略")


def prepare(root, preview, content, changes, batch):
    root = root_path(root)
    batch = destination_path(root, batch)
    if preview["pending"]:
        raise ValueError("存在待确认事项，不能准备写入")
    if not isinstance(changes, dict) or not all(isinstance(v, str) for v in changes.values()):
        raise ValueError("changes 须以相对路径映射到审阅后的完整 UTF-8 正文")
    updates = {r["path"] for r in preview["files"] if r["action"] == "update"}
    if set(changes) - {BINDING} != updates - {BINDING} or not updates <= changes.keys():
        raise ValueError("更新正文与审阅清单不一致")
    if BINDING in changes:
        check_binding(root, changes[BINDING])
        for row in preview["files"]:
            if row["path"] == BINDING and row["action"] != "update":
                raise ValueError("本机绑定处置冲突")
    wanted = {"AGENTS.md": content, **changes}
    watch = {Path(d["source"].strip()).as_posix() for d in preview["decisions"] if "source" in d}
    for row in preview["files"]:
        watch.add(row["path"])
        watch.update(row["consumers"])
        if row["action"] == "retire":
            if row["path"] == BINDING:
                raise ValueError("绑定不能作为旧规则退役")
            wanted[row["path"]] = None
            for user in row["consumers"]:
                if Path(row["path"]).name in (wanted.get(user) or ""):
                    raise ValueError(f"活动消费者仍含旧文件名，请核对：{user}")
    files = {}
    differences = {}
    for name, after in wanted.items():
        before, mode = snapshot(target(root, name))
        if before is None and name not in ("AGENTS.md", BINDING):
            raise ValueError(f"审阅目标已消失：{name}")
        if before == after and name in updates:
            raise ValueError(f"清单要求更新，但正文没有变化：{name}")
        files[name] = dict(before=before, after=after, mode=mode)
        lines = difflib.unified_diff((before or "").splitlines(True), (after or "").splitlines(True),
                                     fromfile=name if before is not None else "/dev/null",
                                     tofile=name if after is not None else "/dev/null")
        differences[name] = "".join(s if s.endswith("\n") else s+"\n\\ No newline at end of file\n" for s in lines)
    st = root.stat()
    plan = dict(version=1, root=str(root), identity=[st.st_dev, st.st_ino], files=files,
                review=preview["files"], decisions=preview["decisions"],
                watch={n: fingerprint(safe_path(root, n))["sha256"] for n in sorted(watch - files.keys())})
    batch.mkdir(mode=0o700)
    write_json(batch / "plan.json", plan)
    sync_directory(batch)
    sync_directory(batch.parent)
    return dict(batch=str(batch), sha256=plan_digest(plan), diff=differences, review=plan["review"])


def sync_directory(directory):
    with directory_fd(directory) as fd:
        os.fsync(fd)


@contextmanager
def directory_fd(directory):
    fd = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def mode_after(entry):
    return 0o600 if entry["mode"] is None else entry["mode"]


def expected(entry, after):
    if after:
        return entry["after"], mode_after(entry) if entry["after"] is not None else None
    return entry["before"], entry["mode"]


def check_sources(root, plan):
    for name, digest in plan["watch"].items():
        if fingerprint(safe_path(root, name))["sha256"] != digest:
            raise ValueError(f"规则出处或活动引用已变：{name}")
    if BINDING in plan["files"]:
        check_binding(root, plan["files"][BINDING]["after"])


def install(root, name, entry):
    path = target(root, name)
    with directory_fd(path.parent) as parent:
        def unchanged():
            if not os.path.samestat(target(root, name).parent.stat(), os.fstat(parent)):
                raise ValueError(f"目标目录已变：{name}")
            if snapshot(path) != expected(entry, False):
                raise ValueError(f"文件已变：{name}")

        if entry["after"] is None:
            unchanged()
            os.unlink(path.name, dir_fd=parent)
        else:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".hutools-rule-", delete=False) as tmp:
                temp = Path(tmp.name).name
                try:
                    tmp.write(entry["after"].encode("utf-8"))
                    tmp.flush()
                    os.fchmod(tmp.fileno(), mode_after(entry))
                    os.fsync(tmp.fileno())
                    unchanged()
                    if entry["before"] is None:
                        os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                    else:
                        os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                finally:
                    try:
                        os.unlink(temp, dir_fd=parent)
                    except FileNotFoundError:
                        pass
        os.fsync(parent)


def apply(root, batch, confirmation):
    root = root_path(root)
    batch = batch.absolute()
    if batch.resolve() != batch or root == batch or root in batch.parents or batch in root.parents:
        raise ValueError("恢复批次须在工程外独立目录")
    meta = batch.stat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
        raise ValueError("恢复批次须为当前用户的私有目录")
    plan = json.loads(safe_path(batch, "plan.json").read_bytes())
    st = root.stat()
    if (plan_digest(plan) != confirmation or plan["version"] != 1 or
            plan["root"] != str(root) or plan["identity"] != [st.st_dev, st.st_ino]):
        raise ValueError("工程身份或已确认清单摘要不符")
    files = plan["files"]
    with directory_fd(root) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = batch / "progress.json"
        phase = "new"
        if state.exists() or state.is_symlink():
            progress = json.loads(safe_path(batch, state.name).read_bytes())
            phase = progress["phase"]
            if progress["sha256"] != confirmation or phase not in ("started", "complete"):
                raise ValueError("批次进度记录不符")
        check_sources(root, plan)
        for name, entry in files.items():
            seen = snapshot(target(root, name))
            allowed = []
            if phase != "complete":
                allowed.append(expected(entry, False))
            if phase != "new":
                allowed.append(expected(entry, True))
            if seen not in allowed:
                raise ValueError(f"目标与本批原件或已执行结果不符：{name}")
        if phase == "new":
            write_json(state, dict(sha256=confirmation, phase="started"))
            sync_directory(batch)
        for retiring in (False, True):
            for name, entry in files.items():
                if (entry["after"] is None) == retiring and snapshot(target(root, name)) != expected(entry, True):
                    install(root, name, entry)
            # 两个阶段分别回读；第一阶段校验失败时旧文件仍在。
            for name, entry in files.items():
                if (retiring or entry["after"] is not None) and snapshot(target(root, name)) != expected(entry, True):
                    raise ValueError(f"写后核验失败：{name}")
            check_sources(root, plan)
        if phase != "complete":
            write_json(state, dict(sha256=confirmation, phase="complete"))
            sync_directory(batch)
    return dict(status="complete", batch=str(batch), sha256=confirmation,
                verified=list(files), retired=[n for n, entry in files.items() if entry["after"] is None])
