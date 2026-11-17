# 精确资料清单、归档与恢复

B02-M06。工具位于安装包内的 `tools/lifecycle.py`，仅依赖 Python 3 标准库。它核对精确文件及内容，不递归扫描工程、不根据文件名或年龄猜测价值、不永久删除文件。清单及回执是任务证据，通过既有任务记录引用，不另建工程当前状态台账。

## 输入与路径

所有资料路径使用 `/` 分隔、相对显式 `--root`；拒绝绝对路径、`..`、重复项、目录、非普通文件与符号链接（包括根和 JSON 输入的祖先目录）。`--root` 必须是已存在工程目录，不得为文件系统根。清单没有保存机器绝对根；迁移后用同一工程身份和新 `--root` 重新核验。命令行中的 spec、plan、out、destination 路径按调用者给出的路径解析；可用绝对路径消除 cwd 歧义。

`plan --spec` 输入如下；示例中的事实必须由实际调查替换，不能把示例直接视作授权：

```json
{
  "project_id": "example-project",
  "task_id": "example-task",
  "files": [
    {
      "path": "results/rebuildable-sample.txt",
      "category": "rebuildable",
      "unique": false,
      "unresolved_failure": false,
      "active": false,
      "recovery_required": false,
      "retention_satisfied": true,
      "reproducible": true,
      "rationale": "本任务已复核；无受支持版本或其他工作区依赖",
      "rebuild_method": "在本任务报告中记录且已验证的重建命令",
      "retention_condition": "用户约定的复核及回退保留期均结束",
      "authorization_ref": "本任务已记录的精确隔离批准",
      "allowed_actions": ["quarantine"]
    }
  ]
}
```

工具读取文件生成 schema_version=1 清单，登记 project_id、task_id、生成时间、精确路径、字节数、SHA-256 及分类元数据。类别为 core、evidence、rebuildable、process、unknown；缺少的事实为未知。`"false"` 这样的字符串不作为布尔事实接受。`plan_sha256` 是整个清单的规范 JSON 摘要；任何文件身份或批准元数据变化都会改变它。

清理候选只允许 rebuildable/process，并要求非唯一、无未决失败、无活动引用、无恢复依赖、保留条件已满足、可重建、理由/重建方法/保留条件/批准引用完整，且 allowed_actions 含 quarantine。核心资产、关键证据或未知项保留，不能因有备份就自动变为过程数据。当前代码不自行判断活动进程、其他 worktree 引用或用户确认真假；这些事实必须由经理/资料角色调查并由适用授权支持。清单里的批准引用和命令确认值只是关联与防误操作，不是可信身份或安全权限。

## 命令

以下路径是调用格式示例；真实操作前核对实际绑定和精确范围。安装后的脚本路径是 `插件根/tools/lifecycle.py`。

```bash
python3 plugins/hutools/tools/lifecycle.py plan --root ./project --spec ./spec.json --out ./batch.json
python3 plugins/hutools/tools/lifecycle.py verify --root ./project --plan ./batch.json
python3 plugins/hutools/tools/lifecycle.py cleanup --root ./project --plan ./batch.json
python3 plugins/hutools/tools/lifecycle.py archive --root ./project --plan ./batch.json --destination ./archive-batch
```

- `plan`：只读取指定文件和 spec；输出必须是新文件，不覆盖已有清单。
- `verify`：逐文件检查存在、类型、字节数与 SHA-256，返回失败位置；无写入。
- `cleanup`：只输出候选、预计字节数和逐项保留理由；无文件移动或删除。`ok` 表示内容核验结果，不表示所有条目可清理，必须查看 candidates/retained。
- `archive`：目标必须是尚不存在的新批次目录，父目录已存在且无符号链接；目标和来源根不能包含彼此。复制到 `files/` 后核对原件和副本，保存 plan.json 与 receipt.json。失败保留全部原件和部分目标，回执标 partial，不自动清理重试。归档完成不授权删除来源。

可恢复隔离需要已经审阅的精确批准和清单摘要，命令格式为：

```text
python3 插件根/tools/lifecycle.py quarantine --root 工程根 --plan 清单路径 --destination 新隔离目录 --confirm 清单SHA256
python3 插件根/tools/lifecycle.py restore --root 恢复工程根 --plan 清单路径 --destination 已有隔离目录 --confirm 清单SHA256
```

`quarantine` 要求整份清单都符合候选条件且内容仍一致；不静默跳过保留项。源文件和目标父目录必须同一文件系统，通过逐文件移动完成隔离；保存每项进度。它不减少文件系统总占用，也不永久删除隔离副本。跨盘请先 archive，后续移除来源须单独审查。`restore` 全量检查原位置和隔离位置，只有内容符合清单、目标不存在时才恢复；已有用户文件绝不覆盖。隔离或恢复中断后，依据精确清单检查两侧位置，可再次 restore 接续；不能只凭回执推断文件所在位置。

## 失败、安全与生命周期

- 必须在资料停止变化、无活动写入和已获实际宿主权限的条件下操作。工具不是 OS 隔离，不对恶意同 UID 并发修改、可写授权记录篡改或工具绕过提供强制保护。文件变化检测能拒绝已观察到的变化，不能替代操作期间的静止条件。
- JSON 读取限制为普通文件且不超过 5 MiB，避免无界控制输入；文件内容按 1 MiB 块计算指纹和复制。archive 开始前检查目标空间至少容纳清单字节数，实际 I/O 失败仍保留 partial。
- 归档/隔离目录包含 plan.json、receipt.json 和 files/；回执区分 in-progress、partial、complete、restored。它们随对应任务/批次保留到恢复窗口结束、无未决失败且替代材料已核验，随后进入明确审查；工具不自动过期或清理。
- 任务报告记录资料归属、创建/核验命令、原/目标绑定、清单身份、状态及退出条件。大数据不写入报告；保留失败证据，不用后续成功覆盖。
- 工作片段、等待、重试和操作者引用沿唯一任务记录/工作流事件保存，包含实际起止、目的、结果和必要定位；未知时间/token 写未知，不在资料清单另建协作指标台账。

## 本批验证

2026-11-17，先写 13 个行为测试，在产品模块尚不存在时执行，13 例因明确的 lifecycle.py 缺失报错；这是功能尚未提供的红态，不是算法缺陷复现。实现后 13/13 通过。自审增加中断隔离恢复、复制 I/O 失败、隔离文件变化拒绝恢复，随后补充祖先链接拒绝及安装态独立脚本/说明验证，最终 18/18 通过。

命令：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_lifecycle.py -v`。测试仅使用每例独立 TemporaryDirectory：覆盖路径越界/符号链接/重复、未知与风险标记保留、指纹变化、归档冲突、原件保留、清单确认、同机换目录原根消失、隔离恢复及异常路径。测试临时文件退出时由标准库回收，不涉及真实项目资料。未测试第二机器或故障断电耐久性；操作期间静止条件及宿主权限边界仍需对应使用环境核验。
