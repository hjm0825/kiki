def summarize(lines):
    result = {}
    for line in lines:
        level = line.split(":")[0]
        result[level] = result.get(level, 0) + 1
    return result
