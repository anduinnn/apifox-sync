#!/usr/bin/env python3
"""接口级文件名生成与解析。

用法：
    python3 api_path.py filename <summary> <METHOD> <path> [--with-method]
    python3 api_path.py sanitize <name>
    python3 api_path.py hash <folder> <METHOD> <path>
    python3 api_path.py -h
    python3 api_path.py --self-test

v1.4 命名规则：
    每个接口落盘为 `<apis_dir>/<folder>/<filename>.json`，其中 filename 规则：
      - 优先使用 operation.summary（去除非法字符、去首尾空白）
      - summary 为空 → fallback 为 path 最后一段（清洗后）
      - fallback 也为空（根路径）→ 固定用 "_root"
      - 当 with_method=True 时，文件名末尾追加 `.<METHOD>`（冲突去重 / 用户显式要求）

    示例：
      filename("创建用户", "POST", "/api/users", False) == "创建用户.json"
      filename("创建用户", "POST", "/api/users", True)  == "创建用户.POST.json"
      filename("", "GET", "/api/users", False)          == "users.json"
      filename("", "GET", "/", False)                   == "_root.json"
      filename("a/b?c", "POST", "/x", False)            == "a_b_c.json"

非法字符：Windows `<>:"|?*` + `/` + `\\` → 下划线；`{}` 保留。

hash_key 提供稳定短 hash（sha1 前 16 位），用于临时文件名：
    ${TMPPREFIX}pull-op-<hash>.json
    key = folder|METHOD|path

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

# 文件名非法字符：合并 Windows 非法字符与 `/` `\\` （`/` 会切目录，必须替换）
_ILLEGAL = '<>:"|?*/\\'

# HTTP 方法白名单（OpenAPI 3.0 operation）
_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def sanitize_filename(name: str) -> str:
    """替换非法字符为下划线，去首尾空白/点，空则返回空串。"""
    if name is None:
        return ""
    s = str(name)
    for ch in _ILLEGAL:
        s = s.replace(ch, "_")
    # 控制字符（0x00-0x1F）
    s = "".join(c if ord(c) >= 32 else "_" for c in s)
    s = s.strip().strip(".")
    return s


def _path_fallback(path: str) -> str:
    """path 最后一段（清洗后）；空/根路径 → '_root'。"""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return "_root"
    return sanitize_filename(parts[-1]) or "_root"


def op_filename(summary: str, method: str, path: str, with_method: bool = False) -> str:
    """按 v1.4 规则生成接口文件名（含 .json 后缀）。

    summary 空或清洗后为空 → 用 path 最后一段作为 fallback。
    with_method=True → 在 stem 后追加 `.<METHOD>` 后缀（调用方决定是否去重）。
    """
    stem = sanitize_filename(summary) or _path_fallback(path)
    if with_method:
        stem = f"{stem}.{method.upper()}"
    return f"{stem}.json"


def hash_key(folder: str, method: str, path: str) -> str:
    """稳定短 hash，用于临时切片文件名。folder|METHOD|path 三元组作为 key。"""
    key = f"{folder}|{method.upper()}|{path}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_op_file(file_path: str) -> Optional[tuple[str, str, dict]]:
    """读取本地 op 文件，返回 (METHOD, path, operation_dict)；非单接口切片/解析失败 → None。

    新布局不再依赖文件名 → 一律通过文件内容的 paths 第一个 entry 来识别 (method, path)。
    仅接受 paths 恰好含 1 条、method 恰好含 1 条的文件；否则视为非 op 文件。
    """
    try:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    paths = data.get("paths")
    if not isinstance(paths, dict) or len(paths) != 1:
        return None
    path = next(iter(paths))
    methods = paths[path]
    if not isinstance(methods, dict) or len(methods) != 1:
        return None
    method = next(iter(methods))
    if method.lower() not in _METHODS:
        return None
    op = methods[method]
    if not isinstance(op, dict):
        return None
    return method.upper(), path, op


def scan_folder_ops(folder_dir: str) -> list[dict]:
    """扫描 folder 目录下所有 .json，返回 [{method, path, summary, operation, abs_path, filename}, ...]。

    只识别单接口切片（paths 和 method 各 1 条）；其他 .json 忽略。
    返回顺序不保证稳定（调用方按需排序）。
    """
    out: list[dict] = []
    if not os.path.isdir(folder_dir):
        return out
    for root, _dirs, files in os.walk(folder_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(root, fn)
            parsed = parse_op_file(full)
            if parsed is None:
                continue
            method, path, op = parsed
            summary = op.get("summary", "") if isinstance(op, dict) else ""
            out.append(
                {
                    "method": method,
                    "path": path,
                    "summary": summary if isinstance(summary, str) else "",
                    "operation": op,
                    "abs_path": full,
                    "filename": os.path.relpath(full, folder_dir).replace(os.sep, "/"),
                }
            )
    return out


def _self_test() -> int:
    # sanitize
    assert sanitize_filename("hello") == "hello"
    assert sanitize_filename("a/b") == "a_b"
    assert sanitize_filename("接口名_v2") == "接口名_v2"
    assert sanitize_filename("  spaced  ") == "spaced"
    assert sanitize_filename("a?b*c<d>e|f:g\"h") == "a_b_c_d_e_f_g_h"
    assert sanitize_filename("") == ""
    assert sanitize_filename(None) == ""
    assert sanitize_filename(".hidden.") == "hidden"
    # 控制字符
    assert sanitize_filename("a\x01b") == "a_b"

    # op_filename - summary 优先
    assert op_filename("创建用户", "POST", "/api/users", False) == "创建用户.json"
    assert op_filename("创建用户", "POST", "/api/users", True) == "创建用户.POST.json"
    # 非法字符
    assert op_filename("a/b?c", "GET", "/x", False) == "a_b_c.json"
    # summary 为空 → fallback 到 path 末段
    assert op_filename("", "GET", "/api/device/list", False) == "list.json"
    assert op_filename("   ", "GET", "/api/device/{id}", False) == "{id}.json"
    # 根路径 fallback
    assert op_filename("", "GET", "/", False) == "_root.json"
    assert op_filename("", "GET", "", False) == "_root.json"
    # with_method on fallback
    assert op_filename("", "POST", "/api/x", True) == "x.POST.json"

    # hash_key 稳定性 + 区分度
    h1 = hash_key("用户管理/基础", "POST", "/api/device/list")
    h2 = hash_key("用户管理/基础", "POST", "/api/device/list")
    h3 = hash_key("用户管理/基础", "GET", "/api/device/list")
    h4 = hash_key("设备管理", "POST", "/api/device/list")
    assert h1 == h2
    assert h1 != h3
    assert h1 != h4
    assert len(h1) == 16 and all(c in "0123456789abcdef" for c in h1)

    # parse_op_file / scan_folder_ops
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-api_path-selftest-"))
    try:
        ok = tmp / "创建用户.json"
        ok.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/users": {
                            "post": {
                                "summary": "创建用户",
                                "x-apifox-folder": "测试",
                            }
                        }
                    },
                    "components": {"schemas": {}},
                }
            ),
            encoding="utf-8",
        )
        parsed = parse_op_file(str(ok))
        assert parsed is not None
        method, path, op = parsed
        assert method == "POST" and path == "/api/users"
        assert op["summary"] == "创建用户"

        # 非单接口切片
        multi = tmp / "multi.json"
        multi.write_text(
            json.dumps({"paths": {"/a": {"get": {}}, "/b": {"get": {}}}}),
            encoding="utf-8",
        )
        assert parse_op_file(str(multi)) is None

        # 非 JSON
        bad = tmp / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        assert parse_op_file(str(bad)) is None

        # scan
        (tmp / "sub").mkdir()
        nested = tmp / "sub" / "删除用户.json"
        nested.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/users/{id}": {
                            "delete": {"summary": "删除用户"}
                        }
                    },
                    "components": {"schemas": {}},
                }
            ),
            encoding="utf-8",
        )
        ops = scan_folder_ops(str(tmp))
        keys = sorted((o["method"], o["path"]) for o in ops)
        assert keys == [("DELETE", "/api/users/{id}"), ("POST", "/api/users")], keys
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
    if len(argv) >= 2 and argv[1] == "filename":
        with_method = "--with-method" in argv
        rest = [a for a in argv[2:] if a != "--with-method"]
        if len(rest) != 3:
            print(
                "Usage: api_path.py filename <summary> <METHOD> <path> [--with-method]",
                file=sys.stderr,
            )
            return 2
        summary, method, path = rest
        print(op_filename(summary, method, path, with_method))
        return 0
    if len(argv) == 3 and argv[1] == "sanitize":
        print(sanitize_filename(argv[2]))
        return 0
    if len(argv) == 5 and argv[1] == "hash":
        print(hash_key(argv[2], argv[3], argv[4]))
        return 0
    print(
        "Usage: api_path.py filename <summary> <METHOD> <path> [--with-method] "
        "| sanitize <name> | hash <folder> <METHOD> <path> | -h | --self-test",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
