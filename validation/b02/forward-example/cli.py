import json
import sys

from summary import summarize


def main():
    if len(sys.argv) != 2:
        print("usage: cli.py INPUT", file=sys.stderr)
        return 2
    try:
        # 输入路径以调用者 cwd 为基准；完整校验后才输出成功 JSON。
        with open(sys.argv[1], encoding="utf-8") as stream:
            result = summarize(stream)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
