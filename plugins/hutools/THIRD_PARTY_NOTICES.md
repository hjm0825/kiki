# 来源与适配

HuTools 经理入口、工程规则、模板和打包检查由本项目编写，采用[自有 MIT 许可](LICENSE)。

八项方法技能参考并改写自 [Matt Pocock / skills](https://github.com/mattpocock/skills/tree/5863806dd4e15c63174bee4e7515a4256792ecfa)，固定提交为 `5863806dd4e15c63174bee4e7515a4256792ecfa`，核对日期 2026-11-17。

保留[上游 MIT 原文](licenses/mattpocock-MIT.txt)。原文件位置、固定 URL 和下载内容 SHA-256 见 [sources.lock.json](sources.lock.json)。该摘要对应上游输入，非本地适配文件摘要。本项目不在线自动更新或加载上游文件。

| 包内技能 | 使用时机与方法 | 所需能力及可能的写入 |
| --- | --- | --- |
| [grill-with-docs](skills/grill-with-docs/SKILL.md) | 需求或设计需要共同决定时，每轮只澄清影响方案的问题 | 读取项目文档，优先用宿主卡片询问；把已确认的决定写回既有入口 |
| [grilling](skills/grilling/SKILL.md) | 用户要求推敲方案时，围绕具体前提提问 | 宿主对话或卡片；可全程在对话内完成 |
| [domain-modeling](skills/domain-modeling/SKILL.md) | 同一术语在多个调用方含义不同时，用实际例子统一定义 | 读取相关代码和项目词表，按任务更新已有术语或决定记录 |
| [research](skills/research/SKILL.md) | 围绕待决定的问题，核对本地能力、原始来源、兼容与成本 | 读取工程材料；易变外部接口需要可访问的官方依据。纯咨询可直接返回调查结果 |
| [codebase-design](skills/codebase-design/SKILL.md) | 核对接口、状态所有者、构建与独立部署组合 | 读取实际消费者及构建配置；设计进入既有方案，源码改动需有适用批次批准 |
| [tdd](skills/tdd/SKILL.md) | 已批准的行为变更，用独立预期执行红绿重构及最终回归 | 需要能运行项目测试的宿主、依赖和隔离样例；在批准范围内修改测试和实现 |
| [diagnosing-bugs](skills/diagnosing-bugs/SKILL.md) | 有报错、异常或退化时，构造可证伪的判断，核对原始输入 | 离线分析可只读；重放、插桩和修复分别核对副作用及授权，按约定保存报告 |
| [code-review](skills/code-review/SKILL.md) | 交付前按需求、实际差异和失败路径独立审查 | 读取实际工作树、暂存内容和证据；需要独立审查时由独立实例复核，输出发现及依据 |

经理[入口](skills/hutools/SKILL.md)由本项目编写。八项适配技能各自的固定输入见来源锁定清单。按任务加载所需技能，工程自身的解释器、构建工具和现场接入条件在接手时核对。随包的[工作流](tools/workflow.md)、[资料工具](tools/lifecycle.md)及[钩子](hooks/README.md)说明分别列出 Python、宿主和操作限制。

未打包上游安装器、探针脚本、外链格式模板和运行依赖；各适配技能已改为包内自包含引用。viviannnl 的流程用于此前方案比较，本包没有复制其文件或代码。
