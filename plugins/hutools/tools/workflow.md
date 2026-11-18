# 任务状态与证据

`workflow.py` 将一个批准批次的需求、验收项、输入版本、实际检查结果和工作片段放在同一个 JSON 中。它检查交付记录的一致性；是否覆盖真实需求、审查是否独立以及用户确认是否真实，仍由经理/独立 reviewer 核对。

## 使用边界

- `approve` **登记**实际用户决定，不生成用户授权。它和 JSON 都不是不可伪造的授权系统；不能让执行者可写的状态文件充当唯一权限边界。宿主沙箱、工具审批与受保护授权另行验证。
- `writable_paths` 限制本工具自身的状态/日志落点，**不限制被执行命令的系统写入**。`run-check` 执行已批准的完整 argv，命令可能有安装、网络、写入等副作用，先审查并通过宿主权限控制；本工具不解析任意 shell 安全性，不替代沙箱。不要把密码、令牌或敏感原始资料放入 argv、spec 或测试输出。
- 需要强制保护却无法确认宿主覆盖时，不使用 `run-check` 执行相关高风险命令。持续 shell、脱离进程组的后台子进程等不在本工具可证明的隔离能力内。
- 状态 JSON 需要唯一写入入口；普通调用使用独占锁和原子替换。锁存在时明确拒绝，不自动判陈旧或抢锁。异常中断后先核实进程/证据，再手动处理精确锁文件；不得为了“恢复”清空历史。

## 最小规格

以下 `spec.json` 由项目已确认计划生成，路径相对所选工程根。`inputs` 应覆盖受影响源码、测试、配置、必要输入和规则文件；只写一个版本标签而漏掉实际文件，不能证明版本一致。

```json
{
  "task_id": "example-fix-01",
  "rules_version": "hutools-0.2.0",
  "source_version": "base-commit-plus-approved-diff",
  "author": "developer-1",
  "requirements": ["R1"],
  "inputs": ["src", "tests", "project-rules.md"],
  "writable_paths": ["records/example-fix-01"],
  "requires_independent_review": true,
  "cases": [
    {
      "id": "regression",
      "requirement": "R1",
      "kind": "command",
      "argv": ["python3", "-B", "-m", "unittest", "discover", "-s", "tests"],
      "timeout_seconds": 60,
      "required": true
    },
    {"id": "review", "requirement": "R1", "kind": "review", "required": true}
  ]
}
```

`requirements` 中每项至少对应一个必需验收项。验收项 `kind` 为 `command`、`manual` 或 `review`，默认必需；非必需观察项可设 `required: false`。高风险/跨模块/正式交付按工程规则设 `requires_independent_review: true`，此时必须存在必需 review 项。审查者名称不得等于实施者；名称不同本身不能证明实际独立。

输入可以是文件或目录，目录递归指纹包含每层目录路径/类型、文件名和内容，空子目录的新增、删除和改名都会使旧证据失效；拒绝路径上跳、符号链接和特殊输入文件。不要将任务文件所在目录或它的上级列为输入，以免状态/证据自引用。项目根相对状态文件目录保存，移动整个工程后不依赖原根；外部输入要先按工程约定登记和核验，不能用符号链接静默借入。

## 命令

令 `WORKFLOW` 指向已安装插件中的 `tools/workflow.py`，`PROJECT` 为已确认工程根。`STATE` 指向工程内的任务 JSON，可传绝对路径或相对调用目录的路径。检查命令从工程根运行，登记的日志路径始终相对工程根，便于整体迁移。

```bash
python3 "$WORKFLOW" create --root "$PROJECT" --state "$STATE" --spec "$PROJECT/spec.json"
python3 "$WORKFLOW" approve --state "$STATE" --actor user --decision '实际用户确认消息或记录标识'
python3 "$WORKFLOW" transition --state "$STATE" --to running --reason '批准范围内开始执行'
python3 "$WORKFLOW" run-check --state "$STATE" --case regression
python3 "$WORKFLOW" evidence --state "$STATE" --case review --actor reviewer-1 \
  --artifact records/example-fix-01/review.md --outcome pass --statement '实际独立审查范围和限制'
python3 "$WORKFLOW" transition --state "$STATE" --to complete --reason '必需验收与独立审查已核对'
python3 "$WORKFLOW" status --state "$STATE"
python3 "$WORKFLOW" report --state "$STATE"
```

报告输出到 stdout；沿工程原入口保存为阅读视图，不维护第二份当前状态。报告不打印完整命令或原始日志。`status` 是查询：退出码 0 仅代表查询成功，须读取 `completion_errors` 和 `effective_status`，不能据此认为验收通过。

`run-check` 仅执行冻结在 case 中的 argv，不接受临时替换命令，也不通过 shell 拼接。当前执行器验收范围为 POSIX；其他平台可读取状态/登记人工证据，运行器会明确拒绝。每项最多 3600 秒、输出前缀最多 1 MiB；超限、超时、启动失败、非零退出或检查期间输入变化均记失败。默认 timeout 为 60 秒。超限日志标为截断失败，不能当完整原始证据；失败日志继续保留。成功退出只是这个命令项的证据，不等于全部需求通过。

人工项和审查项需要真实已存在的报告文件、执行者、结论和说明，记录为 `human-attestation`；不会伪装成机器运行结果。命令项禁止手填 pass。证据绑定规格、当前输入指纹、产物哈希及实际命令退出状态；原始命令输出保存于状态文件旁的 `<任务文件名>-evidence/`。检查命令直接改写任务 JSON 时拒绝覆盖该变化，返回日志位置供复核。

## 状态及恢复

`planned → approved → running → complete` 为主路径；running 可 paused/failed，paused/failed 在批准仍适用时恢复 running。complete 可转 paused 以复核变更，不支持重复 complete 或直接重新执行检查。批准定义被改动后必须重新取得对应范围确认；paused/failed 可以登记新批准，旧事件保留。

`complete` 前逐项核对必需 case 的最新结果、当前输入/规格指纹、证据文件及独立审查。新版本重测将追加证据，原有失败记录保留。已完成任务的输入或必要证据发生变化后，`status` 的 `effective_status` 和 `report` 顶部的状态均为 `needs_revalidation`，`completion_errors` 说明原因。报告表格展示每项最近一次登记的历史结果。读取原始状态的消费者可用 `completion_errors` 复核当前有效性。

未知 schema 版本、损坏 JSON、丢失输入和失效引用报可定位错误，不自动迁移或修补原始证据。状态容量超过 8 MiB 时停止追加并要求合理拆分任务，不自动裁剪历史。锁期间运行检查，经理不能用状态切换来假装已停止正在执行的命令；先通过实际进程/宿主工具中断并确认停止，再记录暂停。

## 协作观察

```bash
python3 "$WORKFLOW" observe --state "$STATE" --role tester --actor worker-2 \
  --kind retry --started-at '2026-11-17T10:00:00+08:00' --ended-at '2026-11-17T10:03:00+08:00' \
  --reason '上次临时样例创建失败，在获准环境重测' \
  --result '样例运行通过，已核对输出' --artifact records/example-fix-01/retry.md
```

时间需带时区，结束不早于开始，示例时间应替换成实际观察。类型为 work/wait/retry。片段与任务事件同存，报告展示角色、执行者、起止、原因、结果及证据路径。`--result` 和 `--artifact` 均可省略，旧片段缺失的项目显示为“未登记”。提供证据时由工具登记文件摘要。并行片段的耗时不能相加当作总耗时，未知的耗时和 token 保持缺测。这些记录的来源为 `caller-observation`。

## 可导入接口

`load_state(path)` 返回已检查基本结构的字典；`project_root(path, state)` 解析工程根；`require_approval(state)` 核查登记批准与规格一致；`completion_errors(path)` 返回交付缺口列表（空列表只说明声明范围检查通过）。另外提供 `create`、`approve`、`transition`、`run_check`、`record_evidence`、`observe`、`report`。调用方必须处理 `WorkflowError` 和文件异常，不能失败后默认通过。
