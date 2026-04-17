#!/usr/bin/env python3
"""Apifox folder 名 ↔ 文件名双向编解码（`/` ↔ `__`，含 sentinel 保护）。

用法：
    python3 path_codec.py encode <folder_name>
    python3 path_codec.py decode <filename>
    python3 path_codec.py -h
    python3 path_codec.py --self-test

argv:
    encode <folder_name>  folder 路径（含 `/`）→ 安全文件名（`__` 分隔）
    decode <filename>     文件名 → folder 路径

env:
    无

stdout:
    encode/decode 后的纯字符串（末尾换行）

抽出目的：
    `pull_extract.py` 写文件时和 `pull_diff.py` / `pull_save.py` 读文件时都引用此模块，
    避免两端独立维护编码规则导致漂移；对历史文件夹名已含 `__` 的情况通过 sentinel
    `__ESCAPED__` 保证可逆（Pre-Mortem S2）。

编码规则：
    原始 folder 可能包含 `/`（层级分隔符）或历史遗留的 `__`（罕见但可能）。
    为保证编码可逆：
      1. 先把原始 `__` 全部替换为 sentinel `__ESCAPED__`（这个串不可能自然出现，
         因为我们编码后生成的文件名中不存在裸 `ESCAPED__`）
      2. 再把 `/` 替换为 `__`
    decode 时反向：先 `__` → `/`，再 `__ESCAPED__` 因为 `__` 已变成 `/`，实际上
    编码后 `__ESCAPED__` 变成 `/ESCAPED/`，decode 时要先还原 sentinel。

    具体实现：
      encode("A")       = "A"
      encode("A/B")     = "A__B"
      encode("A__B")    = "A<SENTINEL_RAW>B" → "A" + escape_marker + "B"
      encode("A/B__C")  = "A__B<SENTINEL_RAW>C"

    我们用一个不可能在文件夹名中自然出现且编码后也不混淆的 sentinel：
    在 encode 阶段将原始 `__` 先整体替换为临时标记 `\x00`（空字符，不会出现在
    任何文件系统路径中），再把 `/` 替换为 `__`；decode 反向。
    但 `\x00` 不能出现在文件名里。所以改用文字 sentinel `__ESCAPED__`。

    最终方案：
      - 规则 A：文件名里的 `__ESCAPED__` 解码为原始的 `__`
      - 规则 B：文件名里的 `__`（除去已被规则 A 消耗的部分）解码为 `/`
    实现用"先替换为占位符再替换"的经典两步法。

退出码：
    0 成功
    1 运行时错误
    2 参数用法错误
"""
from __future__ import annotations

import sys

# 临时占位符：在 encode/decode 内部用 \x01 作为 `__` 的中间态，保证 `/` 和 `__`
# 不会互相干扰。\x01 不可能出现在合法 folder 名中。
_ORIG_DBL_UNDERSCORE = "\x01"


def encode(folder_name: str) -> str:
    """folder 名 → 文件名。

    规则：
      1. 原始 `__` → 占位符（避免被第 2 步误处理）
      2. `/` → `__`
      3. 占位符 → `__ESCAPED__`

    示例：
        encode("")           == ""
        encode("A")          == "A"
        encode("A/B")        == "A__B"
        encode("A__B")       == "A__ESCAPED__B"
        encode("A/B__C")     == "A__B__ESCAPED__C"
    """
    s = folder_name.replace("__", _ORIG_DBL_UNDERSCORE)
    s = s.replace("/", "__")
    s = s.replace(_ORIG_DBL_UNDERSCORE, "__ESCAPED__")
    return s


def decode(filename: str) -> str:
    """文件名 → folder 名。

    规则（必须与 encode 严格反向）：
      1. `__ESCAPED__` → 占位符
      2. `__` → `/`
      3. 占位符 → `__`
    """
    s = filename.replace("__ESCAPED__", _ORIG_DBL_UNDERSCORE)
    s = s.replace("__", "/")
    s = s.replace(_ORIG_DBL_UNDERSCORE, "__")
    return s


def self_test() -> int:
    """双向可逆性自测，覆盖 Pre-Mortem S2 约定的 4 组 fixture。"""
    cases = [
        "",
        "A",
        "A/B",
        "A__B",
        "A/B__C",
        "分批测试",
        "分批测试/A",
        "设备管理/无人机",
        # 多重 __
        "X__Y__Z",
        # / 与 __ 混合
        "a/b__c/d__e",
    ]
    for raw in cases:
        enc = encode(raw)
        dec = decode(enc)
        assert dec == raw, f"round-trip fail: raw={raw!r} enc={enc!r} dec={dec!r}"
    # 关键断言：encode 后的字符串里不含 `/`
    for raw in cases:
        enc = encode(raw)
        assert "/" not in enc, f"encoded still contains '/': {enc!r}"
    # 明文期望
    assert encode("A/B") == "A__B"
    assert encode("A__B") == "A__ESCAPED__B"
    assert encode("A/B__C") == "A__B__ESCAPED__C"
    assert decode("A__B") == "A/B"
    assert decode("A__ESCAPED__B") == "A__B"
    assert decode("A__B__ESCAPED__C") == "A/B__C"
    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 3 or argv[1] not in ("encode", "decode"):
        print(
            "Usage: path_codec.py encode|decode <str> | -h | --self-test",
            file=sys.stderr,
        )
        return 2
    op, s = argv[1], argv[2]
    if op == "encode":
        print(encode(s))
    else:
        print(decode(s))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
