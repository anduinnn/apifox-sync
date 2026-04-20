#!/usr/bin/env python3
"""glob ${TMPPREFIX}pull-op-*.json → 写 ${TMPPREFIX}pull-approved.json（全量 approved folder 清单）。

用法：
    python3 pull_approve_all.py
    python3 pull_approve_all.py -h
    python3 pull_approve_all.py --self-test

argv:
    无

env:
    TMPPREFIX  临时文件前缀（必填）

行为：
    扫描所有 ${TMPPREFIX}pull-op-*.json（接口级切片），从每个切片里读取
    operation.x-apifox-folder 去重后写入 ${TMPPREFIX}pull-approved.json 作为
    字符串数组。"全部覆盖" 分支调用此脚本即可自动 approve 全部 folder。

stdout:
    无（静默）

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path


def extract_folder(op_tmp_path: str) -> str | None:
    try:
        data = json.loads(Path(op_tmp_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    paths = data.get("paths", {})
    if not isinstance(paths, dict) or len(paths) != 1:
        return None
    path = next(iter(paths))
    methods = paths[path]
    if not isinstance(methods, dict) or len(methods) != 1:
        return None
    op = next(iter(methods.values()))
    if not isinstance(op, dict):
        return None
    folder = op.get("x-apifox-folder")
    return folder if isinstance(folder, str) else None


def run(tmpprefix: str) -> int:
    folders: set[str] = set()
    for p in glob.glob(f"{tmpprefix}pull-op-*.json"):
        folder = extract_folder(p)
        if folder is not None:
            folders.add(folder)
    out = sorted(folders)
    with open(f"{tmpprefix}pull-approved.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"

        def write_op(folder: str, method: str, path: str) -> None:
            import hashlib
            key = hashlib.sha1(
                f"{folder}|{method}|{path}".encode("utf-8")
            ).hexdigest()[:16]
            data = {
                "paths": {
                    path: {
                        method.lower(): {
                            "summary": "t",
                            "x-apifox-folder": folder,
                        }
                    }
                },
                "components": {"schemas": {}},
            }
            Path(f"{prefix}pull-op-{key}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

        # 同一 folder 多接口 → 去重
        write_op("简单", "GET", "/a")
        write_op("简单", "POST", "/b")
        # 含 / 与 __
        write_op("父/子", "GET", "/c")
        write_op("历史__组", "GET", "/d")

        # 应被忽略的 sentinel 文件
        Path(f"{prefix}pull-diff.json").write_text("{}", encoding="utf-8")
        Path(f"{prefix}pull-approved.json").write_text("[]", encoding="utf-8")

        os.environ["TMPPREFIX"] = prefix
        rc = run(prefix)
        assert rc == 0
        approved = json.loads(
            Path(f"{prefix}pull-approved.json").read_text(encoding="utf-8")
        )
        assert approved == sorted(["简单", "父/子", "历史__组"]), (
            f"approved unexpected: {approved!r}"
        )

        # 空场景
        for f in glob.glob(f"{prefix}pull-op-*.json"):
            os.remove(f)
        rc2 = run(prefix)
        assert rc2 == 0
        approved2 = json.loads(
            Path(f"{prefix}pull-approved.json").read_text(encoding="utf-8")
        )
        assert approved2 == []
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
