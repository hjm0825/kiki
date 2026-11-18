# HuTools

安装一次，在工程中选择 HuTools 并输入“运行”。经理确认资料库和目标，按任务范围检查工程，组织需求、设计、开发、验证及资料整理。9 个技能、共同规则、角色说明和模板都在包内；使用工程不必提前准备 AGENTS.md 或额外安装同名技能，有效工程约定仍会沿用。

**当前源码候选版为 0.2.0-rc.4。** 设计从使用场景进入，每个设计任务提供可独立评审的方案，资料管理员主动维护当前文档入口。[本次设计与验证](docs/design-workflow.md) · [rc.3 验收](docs/b03-validation.md)。HuTools 在 `hutools` 分支维护。

## 安装与运行

已发布来源的添加方式：

```sh
codex plugin marketplace add https://github.com/hjm0825/kiki.git --ref hutools
```

然后通过客户端 Plugins 安装 HuTools，并新建对话选择插件。**安装后核对 `plugins/hutools/.codex-plugin/plugin.json` 的版本**。rc.2 修复了 rc.1 的插件 hooks 无法被 CLI 0.155.1 发现的问题；本地候选未发布时，远端和已安装版本不会自动更新。新增 hooks 必须在宿主 `/hooks` 中审阅信任，不自动放宽权限。目标宿主不支持 hooks 时明确保留对应执行保护的缺口。

入口先询问模式或接住已有目标，并确认记录中的本地资料库。同次接手确认过的不重复；没有资料库可记待建立，确认路径不自动创建或迁移数据。确定任务后再检查受影响源码、构建、暂存区和证据，不在菜单前展开全仓历史与未跟踪产物。

| 用途 | 结果 |
| --- | --- |
| 开发 | 明确需求和具体批次，测试先行，批准后实施、独立验证及本地交付 |
| 分析 | 读取源码与日志，区分事实/假设，给根因依据、方案和问题报告；不自动修复 |
| 咨询 | 直接回答，必要时定向调研，不强制建目录或空报告 |

处理中使用宿主选择/填写卡片，收到回答后自动接续。缺少卡片入口时如实改用文字问答。报告和项目状态放工程内，插件不采集私人材料到公共仓库。

## 运行工具

仅依赖 Python 3.10+ 标准库；受控检查命令执行当前验证范围为 POSIX。工具和说明全部随插件携带：

- [工作流](plugins/hutools/tools/workflow.md)：需求—批准—版本—证据关联，实际命令采集、独立审查留痕、暂停恢复和状态检查。
- [资料管理](plugins/hutools/tools/lifecycle.md)：精确清单、归档校验、候选审查、可恢复隔离及恢复；不提供永久删除。
- [执行检查](plugins/hutools/hooks/README.md)：受支持调用的前置拒绝、规则版本检查及交付反馈。
- [规则与职责](plugins/hutools/AGENTS.md)：共同规则唯一来源，岗位按需加载。

JSON 中的批准是来源记录，不能替代用户授权或宿主权限。hooks 是已覆盖调用的检查，不能隔离任意 shell、子进程或绕过路径。完整执行保护必须在实际宿主核验；不支持或未验证时不开放相关高风险动作。无后台服务、数据库或独立模型密钥。

## 维护与恢复

```sh
python3 scripts/check_bundle.py
python3 -m unittest discover -s tests -v
```

检查器验证安装包资源、引用和配置；行为测试包含失败/拒绝/迁移。真实客户端安装、卡片、hook 信任与拒绝另行验证。回退使用明确的先前发布提交，先核对项目任务格式和规则版本，不删除新产生的项目材料。

自有内容采用 [MIT](LICENSE)，[第三方来源及许可](plugins/hutools/THIRD_PARTY_NOTICES.md)随插件保存。

## 开发接续

- [完整需求与已确认决定](docs/requirements-and-decisions.md)
- [B02 实施方案及 M01–M07 清单](docs/b02-plan.md)
- [B03 实际修复与完整需求验收](docs/b03-validation.md)
- [当前状态与下一步](docs/implementation-status.md)
- [验收记录与历史失败](docs/b02-validation.md)

本机已在正式工作区完成 rc.2 安装验收，三个钩子均获信任，独立样例验证了实际放行、拒绝和收尾反馈。新工程需单独绑定策略，新机器需重新审阅并验证安装。[本机宿主验收](validation/b02/host-install-20261118.md)

[完整交付包、补充材料及正式路径接续](releases/README.md)。
