# 独立开发试行样例

本样例为生成的隔离输入，不含业务资料。修复后统计INFO/WARN/ERROR，大小写不敏感，空行忽略，未知/格式错误明确拒绝。

在本目录运行 `python3 -B -m unittest -v test_summary.py`。当前7项通过；baseline保存修改前两模块。红态应将baseline两模块与同一test_summary.py复制到独立临时目录再运行，不能覆盖当前实现。作者原根不存在迁移实测通过；独立reviewer另核复制版本跨cwd运行。

原input.log SHA256：c349497b87b1001d4227ed780402688945cac511604f29f4b29ffc1e7100dd31。必要来源与限制见上级review.md。
