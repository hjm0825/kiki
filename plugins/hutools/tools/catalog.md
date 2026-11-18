# 双环整理记录工具

`catalog.py` 将管理员的盘点结果和用户的逐项决定保存在工程自己的 JSON 状态文件。将状态文件与轻量盘点记录一起从现有内环索引挂接。管理员先确定整套范围，实际对照文件和批次清单、整理导航并核实链接；CLI 提供持久状态和完成条件。工程可使用已有导航、资料索引和库内清单，大型原始资料继续沿原位置保存。

## 启动选择与空工程

每次用户启动工程工作流都单独确认是否整理；跳过时无需调用本工具，也不改写持久标记。若本次同意整理且经核查工程文档和资料均为空，先确认适合的目录布局。默认方案可执行 `python3 "$PLUGIN_ROOT/tools/catalog_scaffold.py" --root "$PWD" --directory docs`。它为外环的阶段和模块、内环的阶段、模块和数据建立阅读入口；不会填写虚构的阶段或生成整理完成标记。已形成的布局可安全重复使用。有既存资料或希望采用不同结构时，按用户确认的调整方案处理。

## 一次运行

下例假定当前目录为工程根，已确认的本地资料库由环境变量 `MATERIALS_LIBRARY` 提供。示例范围、ID 和所有示例文件名都应换成本工程实际位置。状态文件与审阅材料采用分开的文件。执行前从工程的本机绑定记录取得用户已确认的路径。

```sh
catalog() {
  python3 "$PLUGIN_ROOT/tools/catalog.py" --root "$PWD" \
    --library-root "$MATERIALS_LIBRARY" --state "$PWD/docs/catalog-state.json" "$@"
}
catalog begin --project my-project --rules dual-ring-v1 \
  --scope documents=project:docs --scope evidence=project:runs \
  --scope archive=library:my-project-batch
catalog status
```

每次接手都声明完整的已确认范围。范围清单与整理规则版本在同一项目内保持稳定，新增范围或换绑时据实际变化复核。换机或在相同绝对路径更换挂载卷时先确认绑定，再在本次完整 `begin` 命令后加 `--rebind-library`。这会重新核对库范围，保存旧决定的历史，工程内已完成范围可继续沿用。工程没有外部资料库且用户确认待建时，可先登记已有工程范围，将尚需资料的具体缺口挂在相关范围。遇到先前已登记但当前未挂载的库，保留其范围及绑定记录。

管理员遍历对应范围，处理能核实的断链，将报告、场景和封存批次分类。按大阶段、小阶段和模块交叉链接外环的结果与内环的依据，实际走通阅读路径。盘点报告记录范围、日期、来源、逐项或批次清单、排除理由，以及仍未核实的资料。完成某个范围的这一步后登记：

```sh
catalog review --scope documents --outer docs/README.md \
  --inner docs/engineering.md --inventory docs/inventory.md
```

三个路径都相对于工程根，可引用同一份文档中已有的不同职责；管理员自行核验内容确实承担相应职责。状态输出用于向用户说明目前是首次全量（full）、断点接续（resume）还是按任务增量（incremental）。增量运行只处理实际受影响的证据与范围。例如 R1 方案有变动，先执行 `catalog touch --scope documents --reason 'R1 方案变更'`，保存本次范围待复核的原因；更新相关资料与链接后对同一范围再次执行 `review`。过程中退出，下一次会回到未完成的范围。原件的完整性检验按该任务真正需要的深度进行。

## 遇到无法修复的缺口

```sh
catalog gap --scope archive --id sample-record --item batch/sample-record \
  --finding '原始录包已加密，无法核实内容' --impact '这次试验的轨迹不能独立重放'
```

向用户显示资料身份与影响，请求决定。等待和无答复时保留缺口。确实取得资料并验证后，用相对于工程根的可读记录关闭：

```sh
catalog close --scope archive --id sample-record --fixed docs/recovery-verification.md
```

如果用户明确指定结束修复，则使用实际答复和可追溯出处。下面的变量应分别取自用户原意与真实对话或已确认决定记录，不可预填：

```sh
catalog close --scope archive --id sample-record \
  --accepted "$ITEM_SPECIFIC_USER_DECISION" --reference "$DECISION_REFERENCE"
```

核对剩余缺口及每个范围的审阅状态，只有全部满足才报告本轮完成。关闭方式、原始事实和使用局限随记录保存；对用户展示“已修复”或“按你决定结束修复”。无法访问范围时工具会暴露相应缺口，管理员需核对绑定并说明受影响资料。部署时在新的工作目录使用同一项目身份和索引，重新确认本机库绑定。

## 非空工程的实体整理

在原 begin 命令中加 --layout-scope 对应文档范围，再运行 inspect --scope 范围。管理员根据实盘准备 JSON 清单。directory 是已登记的工程文档目录。moves 是旧文件到新文件的映射。pages 是新 Markdown 路径与完整正文。pairs 列出阶段和模块每个外环页及其直接链接的内环页。keep 列出原位保留的文档入口。references 补充工程内需修改的 Markdown（如未随 Git 管理的本机契约），非 Git 工程显式列入目录外引用。edits 为原文件指定“原文、新文”对，在相对链接重写后应用，要求原文唯一。各文件位置相对工程根。先运行 arrange --scope 范围 --plan 清单，再加 --apply --recovery 工程外恢复包 执行。重复调用原清单可核对结果，最后 review。恢复包内 before/ 保存本批原件，files.json 登记目标及指纹。
