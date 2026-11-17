# 执行检查与本机接入

本工具检查 **已绑定项目、宿主确实送入 hook 的调用**。它不是沙箱，不会自动信任 hooks、修改全局配置、批准权限升级，也不证明任意 shell/子进程只能写批准文件。

## 启用顺序

1. 安装后按宿主 `/hooks` 审阅插件内 `hooks.json` 和 `guard.py`。未信任不会执行；新版本变化须重新审阅。不能用绕过信任参数冒称用户批准。
2. 核对宿主保护：工作目录、可写目录、被保护资产、插件和授权绑定对执行者的写权限；持续 shell、write_stdin、MCP、嵌套工具、子进程均在边界调查内。只有对实际承诺的路径做拒绝试验后，才能登记相应能力。保护无法建立则不进行对应高风险执行。
3. 用户在本机插件数据目录 `PLUGIN_DATA` 中准备 `hutools-policy.json`，或使用 `--policy` 指定工程外文件。本工具不会创建或批准该文件；位置检查不等于权限隔离。普通任务记录中的“approve”只是确认留痕，不能替代受保护的授权来源。
4. 新会话验证 SessionStart、允许调用、拒绝调用和失败路径确实触发。未绑定时只报告“未启用”，不把缺绑定解释为已保护；未绑定不会影响其他工程。有效绑定只匹配其工程根及子目录。

## 策略格式 v1

以下是需要用户填写的结构示例，不是有效批准，也不应放入使用工程自动生效。`project_root` 为本机绝对绑定；换目录/换机器重新确认，不写入公共包。所有文件列表精确到文件，不支持通配符或目录授权。

```json
{
  "schema_version": 1,
  "project_root": "/absolute/project",
  "batch_id": "TASK-01",
  "decision_ref": "用户确认记录的定位",
  "mode": "approved",
  "rules_sha256": "当前规则文件摘要",
  "expires_at": "明确批准的截止时刻，ISO8601并含时区",
  "write_paths": ["src/example.py"],
  "delete_paths": [],
  "approved_calls": [],
  "task_state": "records/task.json"
}
```

`mode` 可为 `read_only`、`approved`、`paused`。后两者分别表示按清单审核调用、暂停本批调用。read_only 不放行通用副作用工具，调查应使用宿主已验证的只读入口；这里不会通过猜测命令字符串授予只读权限。任务状态字段可省略，设置后 Stop 调用[工作流检查器](../tools/workflow.py)复核，不建立另一份任务状态。

取得规则摘要：

```sh
python3 /已安装插件根/hooks/guard.py --rules-digest
```

调用审批格式为 `{"tool":"Bash","sha256":"..."}`。对真实 hook JSON 使用 `--fingerprint` 输出工具名、完整参数和真实 cwd 的摘要；**摘要不是批准**，审核命令、参数、脚本版本及副作用后，由用户写入受保护策略。脚本内容变化仍须重审，参数摘要不能锁定间接代码。

```sh
python3 /已安装插件根/hooks/guard.py --fingerprint < call.json
```

补丁只允许 write_paths 内的精确文件；拒绝 `..`、符号链接、硬链接文件和控制配置目录。删除须在 delete_paths；移动同时核对写入目标和删除源。非补丁调用须精确列入 approved_calls，未知调用拒绝；有意拒绝 write_stdin 与顶层嵌套执行入口，但宿主绕过 hook 的入口仍由宿主负责保护。不要为了运行构建把任意 shell 或脚本自动视为安全。

## 交付与失败

策略错误、过期、规则摘要变化和已暂停批次会拒绝受控调用。PreToolUse 输出正式 `deny`；解析错误 exit 2。未通过的 Stop 返回继续反馈，若已续跑一次仍失败则停止重复并要求如实交接。Stop 不能阻止用户手改 JSON 或口头误报，JSON 校验器不能冒充权限系统。

缺解释器、脚本未部署、hook 超时/宿主解析失败或非覆盖工具的行为必须在目标宿主实测；处理器单测不能证明这些场景失效关闭。这里不声称跨宿主完整安全隔离。hook 仅读取，不采集完整命令、凭据或工具输出；任务过程的有界记录由工作流工具负责。

官方依据：[hooks](https://learn.chatgpt.com/docs/hooks)、[插件打包](https://developers.openai.com/plugins/build/plugins)。插件 hooks 不适用于云端编排的 ChatGPT Work，见[支持范围](https://learn.chatgpt.com/docs/plugins)。
