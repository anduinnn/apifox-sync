#!/usr/bin/env python3
"""将 rename-confirmed.json 铺平为 `{id}\\t{method} {path}` TSV，写入 stdout。

用法：
    python3 push_delete_list.py
    python3 push_delete_list.py -h
    python3 push_delete_list.py --self-test

argv:
    无（本脚本不接收文件路径；固定从 ${TMPPREFIX}rename-confirmed.json 读取）

env:
    TMPPREFIX  临时文件前缀（必填）

stdout:
    每行 `{apifox_id}\\t{METHOD} {path}`；apifox_id 可能为空字符串（老接口无 id）

================ DANGER: subshell kills TOKEN ================
调用方**必须**用进程替换 `< <(...)`，严禁 pipe 形态：

    正确：
        while IFS=$'\\t' read -r api_id label; do
            curl ... -H "Authorization: Bearer ${TOKEN}" ...
        done < <(python3 push_delete_list.py)

    错误（禁用）：
        python3 push_delete_list.py | while IFS=$'\\t' read -r api_id label; do
            curl ... -H "Authorization: Bearer ${TOKEN}" ...  # ❌ TOKEN 丢失
        done

原因：pipe 的右侧 `while read` 在子 shell 执行，未 export 的 TOKEN / PROJECT_ID
对其不可见，curl 会发送空 Authorization 头导致 401/403 假阳性，进而触发 init
循环重新索取 Token，污染新 Token 使用路径（Pre-Mortem S3）。
================================================================

等价替换 push-api.md 步骤 11.4 里 `while read < <(python3 -c "...")` 内嵌脚本。

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def run(tmpprefix: str) -> int:
    rc_path = Path(f"{tmpprefix}rename-confirmed.json")
    if not rc_path.is_file():
        # 无确认清单 → 什么都不输出（与父 shell while-read 循环兼容）
        return 0
    try:
        items = json.loads(rc_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: failed to read {rc_path}: {e}", file=sys.stderr)
        return 1
    for it in items:
        aid = it.get("apifox_id") or ""
        old = it.get("old", {})
        method = old.get("method", "")
        path = old.get("path", "")
        sys.stdout.write(f"{aid}\t{method} {path}\n")
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        fixture = [
            {
                "source_fq": "UserController#update",
                "old": {"path": "/api/users", "method": "PUT", "folder": "用户管理"},
                "new": {"path": "/api/users/{id}", "method": "PUT", "folder": "用户管理"},
                "apifox_id": "1002",
                "summary": "更新用户",
            },
            # 无 apifox_id 的老接口
            {
                "source_fq": "UserController#legacy",
                "old": {"path": "/api/old", "method": "GET", "folder": "用户管理"},
                "new": {"path": "/api/new", "method": "GET", "folder": "用户管理"},
                "apifox_id": None,
                "summary": "旧接口",
            },
        ]
        Path(f"{prefix}rename-confirmed.json").write_text(
            json.dumps(fixture, ensure_ascii=False), encoding="utf-8"
        )
        os.environ["TMPPREFIX"] = prefix
        # 捕获 stdout
        import io
        buf = io.StringIO()
        orig = sys.stdout
        try:
            sys.stdout = buf
            rc = run(prefix)
        finally:
            sys.stdout = orig
        out = buf.getvalue()
        assert rc == 0
        lines = out.splitlines()
        assert lines == [
            "1002\tPUT /api/users",
            "\tGET /api/old",
        ], f"stdout unexpected: {lines!r}"

        # 空清单（文件不存在时）
        prefix2 = str(tmp) + "/empty-"
        os.environ["TMPPREFIX"] = prefix2
        rc = run(prefix2)
        assert rc == 0
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
        print("Usage: push_delete_list.py | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
