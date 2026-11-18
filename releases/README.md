# HuTools 交付包与恢复

本批候选：0.2.0-rc.2，日期2026-11-17。完整源码包为 `HuTools-0.2.0-rc.2-full.zip`，对应源码提交和 SHA-256 在随包 `delivery.json` 与 `SHA256SUMS` 中登记。

完整包包含独立插件（9技能、规则、角色、模板、Python工具与hooks）、已确认需求Q1–Q26/D01–D17、B02完整方案M01–M07、源码及测试、版本化任务/原始检查日志、独立审查、复现实例和补充材料归档。必要运行资源全部位于 `plugins/hutools`，不依赖本机临时目录。

`HuTools-supporting-materials-20261117.zip` 保留本次临时目录中的原始红绿与宿主沙箱输出、试行记录、历史发布包、第三方来源快照及协议schema；逐文件校验值和原临时相对位置见 [清单](supporting-materials-manifest.json)。内部旧绝对临时路径只是历史出处，不是新机器运行依赖。第三方材料按原许可保留。源代码的旧版本还可从Git历史恢复。

## 在正式路径接续

在用户选择的父目录运行：

```bash
git clone --branch hutools https://github.com/hjm0825/kiki.git HuTools
cd HuTools
python3 -B scripts/check_bundle.py
python3 -B plugins/hutools/tools/workflow.py status --state "$PWD/validation/b02/task.json"
```

从 [README](../README.md) 进入需求、方案、状态与验收。2026-11-17 的归档包保留当日 `host-install` 待验的快照。正式工作区于 2026-11-18 完成本机 rc.2 的信任、绑定、放行、拒绝及 Stop 检查。[本机宿主验收](../validation/b02/host-install-20261118.md)。换机时仍需核对实际安装并重新测试。全量回归命令为 `python3 -B -m unittest discover -s tests -v`，本批 73 项已通过。

本批结束后不再在原临时工作区继续研发。清理只覆盖已归入此交付范围的精确HuTools临时目录；先核验远端包和逐文件内容，再确认无活动占用、无新改动后清理。其他程序临时文件不属于本批。公共包不带账号凭据、仿真业务材料或资料库本机绑定；新机器重新确认本地资料库，不能把Git索引当原始外部数据副本。
