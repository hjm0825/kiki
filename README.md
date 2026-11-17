# HuTools

一个从“运行”开始的工程协作插件：经理与你讨论目标，按需组织调查、设计、测试审查和资料整理。

**当前版本：0.1.0-preview.1，入口与问题分析预览版。** 可以讨论开发需求、分析源码与日志、生成报告，以及自由咨询。实际业务代码修改和运行时强制拦截在下一批接入。原 `main` 分支保持不变，HuTools 在 `hutools` 分支维护。

## 安装

在支持插件的本机 Codex 环境中添加仓库来源：

```sh
codex plugin marketplace add https://github.com/hjm0825/kiki.git --ref hutools
```

然后在桌面客户端的 Plugins 目录选择 **HuTools** 来源，安装 HuTools，并新建对话。也可以在 Codex CLI 中用 `/plugins` 浏览该来源；填写卡片的呈现取决于宿主客户端。

该命令已核对 Codex CLI 0.155.1 的帮助。若你的版本没有此命令，提供 `codex --version` 和原错误，再按 [官方本地 marketplace 安装方式](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)适配。不要把默认 `main` 分支当作插件来源。

安装后在工程会话中通过 **➕ / Plugins / @** 选择 HuTools，输入 **运行**。也可明确调用 `$hutools`。本插件使用现有宿主模型与工具，无独立 MCP 服务、后台进程或额外模型密钥。

## 使用

| 你要做什么 | 经理怎样接续 | 得到什么 |
| --- | --- | --- |
| 开发功能 | 从问题和预期结果开始，读取工程并调研，逐步讨论需求和系统影响 | 可审阅的需求、方案、验收实例和实施计划；预览版到方案为止 |
| 分析问题 | 补问缺少的源码/材料位置、现象和时间，按证据调查 | 以根因和首选解决方案为核心的 Markdown 报告 |
| 其他咨询 | 直接回答，必要时定向调研 | 明确建议与理由；普通问答不强制建文件 |

例如：

```text
运行

分析问题：源码在 <源码目录>，日志在 <资料目录>。
昨天 14:20 附近出现任务取消，预期应继续运行。
报告放到 <已有问题报告目录>。
```

需要选择或补充信息时，优先在处理中弹出填写卡片，每轮 1～3 个问题。你提交后经理接续处理；已知信息不重复询问。宿主没有卡片工具时使用简短文字问答。报告和状态放在你的工程中，不写进插件安装目录。

## 内容与验证

- [版本能力与客户端试用](docs/implementation-status.md)：交付状态、验证记录和最短安装验收。
- [团队规则](plugins/hutools/skills/hutools/references/rules.md)：事实、授权、数据、测试投入和停止条件。
- [岗位职责](plugins/hutools/skills/hutools/references/roles.md)：五类职责按需启动。
- [来源与适配](plugins/hutools/THIRD_PARTY_NOTICES.md)：固定第三方版本、许可和修改说明。

仓库维护者可使用 Python 3.10+ 执行：

```sh
python3 scripts/check_bundle.py
python3 -m unittest discover -s tests -v
```

这些检查验证分发包和路径；实际安装、卡片和工具能力另按试用说明核验。

自有内容采用 [MIT 许可](LICENSE)，第三方声明随插件保留。
