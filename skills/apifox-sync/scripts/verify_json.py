#!/usr/bin/env python3
"""验证 JSON 文件语法是否合法。

用法：
    python3 skills/apifox-sync/scripts/verify_json.py <path>
    python3 skills/apifox-sync/scripts/verify_json.py -h
    python3 skills/apifox-sync/scripts/verify_json.py --self-test

成功：stdout 输出 `JSON_VALID`，退出码 0。
失败：stderr 输出 `JSON_INVALID: line X col Y: <msg>`，退出码 1。
文件缺失：stderr 输出 `FILE_NOT_FOUND: <path>`，退出码 1。
参数错误：stderr 输出用法提示，退出码 2。

抽出目的：替换 push-api.md 步骤 10 内联的 `python3 -c "..."`，让调用
形态统一为固定脚本路径，使白名单规则
`Bash(python3 skills/apifox-sync/scripts/verify_json.py:*)` 能一次命中，
避免每次授权。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def verify(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
    except FileNotFoundError:
        print(f"FILE_NOT_FOUND: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"JSON_INVALID: line {e.lineno} col {e.colno}: {e.msg}", file=sys.stderr)
        return 1
    print("JSON_VALID")
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-verifyjson-"))
    try:
        valid = tmp / "valid.json"
        valid.write_text('{"ok": true, "n": 1}', encoding="utf-8")
        invalid = tmp / "invalid.json"
        invalid.write_text('{"bad": ,}', encoding="utf-8")
        missing = tmp / "missing.json"

        assert verify(str(valid)) == 0, "valid.json should return 0"
        assert verify(str(invalid)) == 1, "invalid.json should return 1"
        assert verify(str(missing)) == 1, "missing.json should return 1"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 2:
        print("Usage: verify_json.py <path> | -h | --self-test", file=sys.stderr)
        return 2
    return verify(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
