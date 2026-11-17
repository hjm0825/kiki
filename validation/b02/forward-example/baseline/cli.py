import json
import sys
from summary import summarize

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as stream:
        print(json.dumps(summarize(stream), sort_keys=True))
