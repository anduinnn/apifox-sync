#!/usr/bin/env python3
"""glob ${TMPPREFIX}pull-*.json → 写 ${TMPPREFIX}pull-approved.json（全量 approved）。

用法：
    python3 pull_approve_all.py
    python3 pull_approve_all.py -h
    python3 pull_approve_all.py --self-test

argv:
    无

env:
    TMPPREFIX  临时文件前缀（必填）

行为：
    扫描所有 ${TMPPREFIX}pull-*.json（排除 pull-diff.json / pull-approved.json），
    用 path_codec.decode 还原 folder 名，写入 ${TMPPREFIX}pull-approved.json 作为
    字符串数组。

stdout:
    无（静默）

等价替换 pull.md 步骤 5.5 "全部覆盖" 分支内嵌 python3 逻辑。

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_codec  # noqa: E402


def run(tmpprefix: str) -> int:
    folders: list[str] = []
    prefix_base = os.path.basename(tmpprefix)
    for p in glob.glob(f"{tmpprefix}pull-*.json"):
        if p.endswith("pull-diff.json") or p.endswith("pull-approved.json"):
            continue
        basename = os.path.basename(p)
        encoded = basename
        if encoded.startswith(prefix_base):
            encoded = encoded[len(prefix_base):]
        encoded = encoded.removeprefix("pull-").removesuffix(".json")
        folders.append(path_codec.decode(encoded))
    folders.sort()
    with open(f"{tmpprefix}pull-approved.json", "w", encoding="utf-8") as f:
        json.dump(folders, f, ensure_ascii=False)
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"

        def write_tmp(folder: str) -> None:
            enc = path_codec.encode(folder)
            Path(f"{prefix}pull-{enc}.json").write_text("{}", encoding="utf-8")

        write_tmp("简单")
        write_tmp("父/子")
        write_tmp("历史__组")
        # 故意放两个应被排除的 sentinel 文件
        Path(f"{prefix}pull-diff.json").write_text("{}", encoding="utf-8")
        Path(f"{prefix}pull-approved.json").write_text("{}", encoding="utf-8")

        os.environ["TMPPREFIX"] = prefix
        rc = run(prefix)
        assert rc == 0
        approved = json.loads(
            Path(f"{prefix}pull-approved.json").read_text(encoding="utf-8")
        )
        assert approved == sorted(["简单", "父/子", "历史__组"]), (
            f"approved unexpected: {approved!r}"
        )
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
    if len(argv) != 1:
        print("Usage: pull_approve_all.py | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
