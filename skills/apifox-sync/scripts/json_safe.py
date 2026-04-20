#!/usr/bin/env python3
"""共享的宽松 JSON 加载工具，专门应对 Apifox 导出 JSON 中可能出现的非法 `\\` 转义。

用法（作为模块）：
    from json_safe import load_json_loose
    data = load_json_loose(path)

命令行自测：
    python3 json_safe.py --self-test

背景：Apifox 用户可在 description / example 等字段输入字面反斜杠（如
`C:\\Users\\...` 或正则 `\\d+`），部分历史版本导出时未正确转义为 `\\\\`，导致
标准 `json.load` 抛 `Invalid \\escape`。本模块先尝试严格解析，失败时对所有"非合法
JSON 转义"的裸 `\\` 补一个转义再重试，避免全流程在对话层/对话层外各写一份容错。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

# JSON 合法转义字符集合：\" \\ \/ \b \f \n \r \t \uXXXX
_VALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def sanitize_backslashes(raw: str) -> str:
    """把所有不符合 JSON 合法转义的裸 `\\` 补成 `\\\\`。"""
    return _VALID_ESCAPE.sub(r"\\\\", raw)


def load_json_loose(path: str) -> dict:
    """读取 JSON 文件。先用 `strict=False` 解析（允许字符串内控制字符）；
    若遇非法反斜杠转义，对裸 `\\` 做一次修正再解析。

    Apifox 导出的 description/example 常含裸 `\\t`/`\\n`/`\\r`，严格模式会抛
    `Invalid control character`；同时历史版本可能未正确转义用户输入的 `\\`，
    产生 `Invalid \\escape`。两者都在此处统一容错。
    """
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        if "Invalid \\escape" not in e.msg:
            raise
        return json.loads(sanitize_backslashes(raw), strict=False)


def _self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-jsonsafe-"))
    try:
        # 1) 合法 JSON 正常解析
        p1 = tmp / "ok.json"
        p1.write_text('{"a": 1, "b": "hi\\n"}', encoding="utf-8")
        assert load_json_loose(str(p1)) == {"a": 1, "b": "hi\n"}

        # 2) 含非法 `\` 转义（模拟 Apifox 导出）→ 容错后解析成功
        # 构造一个字段值含字面反斜杠且未正确转义：例如 {"re": "\d+"}
        p2 = tmp / "bad_escape.json"
        p2.write_text('{"re": "\\d+", "path": "C:\\Users\\x"}', encoding="utf-8")
        data = load_json_loose(str(p2))
        assert data["re"] == "\\d+", data["re"]
        assert data["path"] == "C:\\Users\\x", data["path"]

        # 3) 合法转义（\n \t \uXXXX）不应被破坏
        p3 = tmp / "mixed.json"
        p3.write_text(
            '{"tab": "a\\tb", "unicode": "\\u4e2d", "bad": "x\\y"}',
            encoding="utf-8",
        )
        data3 = load_json_loose(str(p3))
        assert data3["tab"] == "a\tb"
        assert data3["unicode"] == "中"
        assert data3["bad"] == "x\\y"

        # 4) 语法错误（与反斜杠无关）仍然抛出
        p4 = tmp / "broken.json"
        p4.write_text('{"a": ,}', encoding="utf-8")
        try:
            load_json_loose(str(p4))
            raise AssertionError("expected JSONDecodeError for broken.json")
        except json.JSONDecodeError:
            pass
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
        return _self_test()
    print("Usage: json_safe.py --self-test | -h", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
