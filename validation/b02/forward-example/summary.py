def summarize(lines):
    result = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        level, separator, _message = line.partition(":")
        if not separator:
            raise ValueError(f"line {line_number}: missing ':' separator")
        level = level.strip().upper()
        if level not in {"INFO", "WARN", "ERROR"}:
            raise ValueError(f"line {line_number}: unknown level {level!r}")
        result[level] = result.get(level, 0) + 1
    return result
