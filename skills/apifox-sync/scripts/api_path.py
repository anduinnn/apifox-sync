#!/usr/bin/env python3
"""接口级文件名编解码：(method, path) ↔ 相对文件路径。

用法：
    python3 api_path.py relpath <METHOD> <path>
    python3 api_path.py split <relpath>
    python3 api_path.py hash <folder> <METHOD> <path>
    python3 api_path.py -h
    python3 api_path.py --self-test

约定：
    每个接口落盘为 `<apis_dir>/<folder>/<op_relpath>`，其中 op_relpath 规则：
      - path 以 `/` 起始；按 `/` 切段，空段或根路径 `/` → 单段 `_root`
      - 各段内将 Windows 非法字符 `<>:"|?*` 替换为 `_`（`{}` 保留，因 OpenAPI 路径参数常用）
      - 末段追加 `.<METHOD>`
      - 各段用 `/` 连接，末尾加 `.json`

    示例：
      relpath("POST", "/api/device/list") == "api/device/list.POST.json"
      relpath("GET",  "/api/device/{id}") == "api/device/{id}.GET.json"
      relpath("GET",  "/")                == "_root.GET.json"
      relpath("GET",  "")                 == "_root.GET.json"

    split 为 relpath 的反解：
      split("api/device/{id}.GET.json") == ("GET", "/api/device/{id}")
      split("_root.GET.json")           == ("GET", "/")

hash_key 提供一个稳定短 hash（sha1 前 16 位），用于临时文件名：
    ${TMPPREFIX}pull-op-<hash>.json

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import hashlib
import sys
from typing import Tuple

# Windows 文件名非法字符（URL path 里罕见但做防御）。`{}` 在合法 filename 里允许，保留。
_ILLEGAL = '<>:"|?*'

# HTTP 方法白名单（OpenAPI 3.0 operation）
_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def _sanitize_segment(seg: str) -> str:
    for ch in _ILLEGAL:
        seg = seg.replace(ch, "_")
    return seg


def op_relpath(method: str, path: str) -> str:
    """(METHOD, /a/b/c) → 'a/b/c.METHOD.json'。空/根路径 → '_root.METHOD.json'。"""
    method_up = method.upper()
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        parts = ["_root"]
    parts = [_sanitize_segment(p) for p in parts]
    parts[-1] = f"{parts[-1]}.{method_up}"
    return "/".join(parts) + ".json"


def split_op_relpath(relpath: str) -> Tuple[str, str]:
    """'a/b/c.POST.json' → ('POST', '/a/b/c')；'_root.GET.json' → ('GET', '/')。

    只接受 op_relpath 产出的形态；不符合时抛 ValueError。
    """
    if not relpath.endswith(".json"):
        raise ValueError(f"not a .json file: {relpath!r}")
    trimmed = relpath[: -len(".json")]
    # 最后一个 `.` 之前是 path 最后一段，之后是 METHOD
    dot = trimmed.rfind(".")
    if dot < 0:
        raise ValueError(f"missing METHOD suffix: {relpath!r}")
    method = trimmed[dot + 1 :]
    if method.lower() not in _METHODS:
        raise ValueError(f"unknown method {method!r} in {relpath!r}")
    path_part = trimmed[:dot]
    if path_part == "_root":
        return method.upper(), "/"
    return method.upper(), "/" + path_part


def is_op_file(relpath: str) -> bool:
    """判断文件名是否符合 op 命名规则（用于扫描本地 apis 目录）。"""
    try:
        split_op_relpath(relpath)
        return True
    except ValueError:
        return False


def hash_key(folder: str, method: str, path: str) -> str:
    """稳定短 hash，用于临时切片文件名。folder|METHOD|path 三元组作为 key。"""
    key = f"{folder}|{method.upper()}|{path}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _self_test() -> int:
    # 基本 round-trip
    cases = [
        ("GET", "/"),
        ("GET", ""),
        ("POST", "/api/device/list"),
        ("GET", "/api/device/{id}"),
        ("PUT", "/api/device/{id}"),
        ("DELETE", "/api/users/{id}/children/{cid}"),
        ("PATCH", "/a"),
    ]
    expected_rel = {
        ("GET", "/"): "_root.GET.json",
        ("GET", ""): "_root.GET.json",
        ("POST", "/api/device/list"): "api/device/list.POST.json",
        ("GET", "/api/device/{id}"): "api/device/{id}.GET.json",
        ("PUT", "/api/device/{id}"): "api/device/{id}.PUT.json",
        ("DELETE", "/api/users/{id}/children/{cid}"): "api/users/{id}/children/{cid}.DELETE.json",
        ("PATCH", "/a"): "a.PATCH.json",
    }
    for method, path in cases:
        rel = op_relpath(method, path)
        assert rel == expected_rel[(method, path)], (
            f"relpath mismatch: {method} {path!r} → {rel!r}"
        )
        back_method, back_path = split_op_relpath(rel)
        # 空字符串被标准化为根路径
        expect_path = "/" if path in ("", "/") else path
        assert back_method == method.upper(), f"method round-trip: {back_method}"
        assert back_path == expect_path, (
            f"path round-trip: {path!r} → {rel!r} → {back_path!r}"
        )

    # Windows 非法字符替换
    rel = op_relpath("GET", "/a:b/c?d/e|f")
    assert rel == "a_b/c_d/e_f.GET.json", rel

    # is_op_file
    assert is_op_file("api/device/list.POST.json")
    assert is_op_file("_root.GET.json")
    assert not is_op_file("foo.json")  # 无 METHOD 段
    assert not is_op_file("foo.BAR.json")  # 非 HTTP METHOD
    assert not is_op_file("foo.txt")

    # hash_key 稳定性 + 区分度
    h1 = hash_key("设备管理/无人机", "POST", "/api/device/list")
    h2 = hash_key("设备管理/无人机", "POST", "/api/device/list")
    h3 = hash_key("设备管理/无人机", "GET", "/api/device/list")
    h4 = hash_key("设备管理", "POST", "/api/device/list")
    assert h1 == h2
    assert h1 != h3
    assert h1 != h4
    assert len(h1) == 16 and all(c in "0123456789abcdef" for c in h1)

    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return _self_test()
    if len(argv) >= 2 and argv[1] == "relpath" and len(argv) == 4:
        print(op_relpath(argv[2], argv[3]))
        return 0
    if len(argv) >= 2 and argv[1] == "split" and len(argv) == 3:
        m, p = split_op_relpath(argv[2])
        print(f"{m}\t{p}")
        return 0
    if len(argv) >= 2 and argv[1] == "hash" and len(argv) == 5:
        print(hash_key(argv[2], argv[3], argv[4]))
        return 0
    print(
        "Usage: api_path.py relpath <METHOD> <path> | split <relpath> "
        "| hash <folder> <METHOD> <path> | -h | --self-test",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
