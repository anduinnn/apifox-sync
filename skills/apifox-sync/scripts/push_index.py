#!/usr/bin/env python3
"""对 Apifox export-openapi 结果构建双向索引。

用法：
    python3 push_index.py <export_json>
    python3 push_index.py -h
    python3 push_index.py --self-test

argv:
    <export_json>  Apifox export-openapi 写入的 JSON 文件（通常是 ${TMPPREFIX}export.json）

env:
    TMPPREFIX  临时文件路径前缀（用于写 existing.json / by-source.json）

输出（写入文件，走 TMPPREFIX）:
    ${TMPPREFIX}existing.json   {"METHOD:path": [folder, ...]}
    ${TMPPREFIX}by-source.json  {source_fq: {path, method, folder, apifox_id, controller, summary}}

stdout 摘要:
    INDEXED: N, WITH_SOURCE: M

等价替换 push-api.md 步骤 11.1 内嵌 python3 逻辑。保持变量命名、字段、正则、
容错逻辑（`re.sub(r'\\\\(?!["\\\\/bfnrtu])', ...)`）与原实现一致。

退出码：
    0 成功
    1 运行时错误（如 export.json 缺失 / 无 paths 字段）
    2 参数用法错误
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


def extract_apifox_id(detail: dict) -> str | None:
    """优先 x-apifox-id；降级从 x-run-in-apifox URL 提取 api-{id}(-run)。"""
    aid = detail.get("x-apifox-id")
    if aid:
        return str(aid)
    run_url = detail.get("x-run-in-apifox", "")
    m = re.search(r"/apis/api-(\d+)(?:-run|$)", run_url)
    return m.group(1) if m else None


def build_index(data: dict) -> tuple[dict, dict]:
    """构建 existing / by_source 索引。与 push-api.md 11.1 等价。"""
    existing: dict[str, list[str]] = {}
    by_source: dict[str, dict] = {}
    for path, methods in data.get("paths", {}).items():
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            folder = detail.get("x-apifox-folder", "")
            key = f"{method.upper()}:{path}"
            existing.setdefault(key, []).append(folder)
            source_fq = detail.get("x-source-method-fq")
            if source_fq:
                by_source[source_fq] = {
                    "path": path,
                    "method": method.upper(),
                    "folder": folder,
                    "apifox_id": extract_apifox_id(detail),
                    "controller": detail.get("x-source-controller", ""),
                    "summary": detail.get("summary", ""),
                }
    return existing, by_source


def load_export(path: str) -> dict:
    """读取 export.json，带 push-api.md 里的 `\\` 容错修正。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # 与 push-api.md 步骤 11.1 正则一致
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw)
    return json.loads(raw, strict=False)


def run(export_json: str, tmpprefix: str) -> int:
    data = load_export(export_json)
    if "paths" not in data:
        print("ERROR: export.json has no 'paths' field", file=sys.stderr)
        return 1
    existing, by_source = build_index(data)
    with open(f"{tmpprefix}existing.json", "w", encoding="utf-8") as f:
        json.dump(existing, f)
    with open(f"{tmpprefix}by-source.json", "w", encoding="utf-8") as f:
        json.dump(by_source, f, ensure_ascii=False)
    print(f"INDEXED: {len(existing)}, WITH_SOURCE: {len(by_source)}")
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        # fixture：3 种 operation：有锚点、无锚点、无 x-run-in-apifox
        fixture = {
            "paths": {
                "/api/users": {
                    "get": {
                        "summary": "列表",
                        "x-apifox-folder": "用户管理",
                        "x-source-method-fq": "UserController#list",
                        "x-run-in-apifox": "https://app.apifox.com/web/project/1/apis/api-10086-run",
                        "x-source-controller": "UserController",
                    },
                    "post": {
                        "summary": "无锚点的旧接口",
                        "x-apifox-folder": "用户管理",
                    },
                },
                "/api/devices": {
                    "get": {
                        "summary": "设备列表",
                        "x-apifox-folder": "设备管理",
                        "x-source-method-fq": "DeviceController#list",
                        # 无 x-run-in-apifox & 无 x-apifox-id → apifox_id=None
                    }
                },
            }
        }
        export_path = tmp / "export.json"
        export_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        prefix = str(tmp) + "/apifox-sync-"
        os.environ["TMPPREFIX"] = prefix
        rc = run(str(export_path), prefix)
        assert rc == 0

        existing = json.loads(Path(f"{prefix}existing.json").read_text(encoding="utf-8"))
        by_src = json.loads(Path(f"{prefix}by-source.json").read_text(encoding="utf-8"))
        assert existing == {
            "GET:/api/users": ["用户管理"],
            "POST:/api/users": ["用户管理"],
            "GET:/api/devices": ["设备管理"],
        }, f"existing mismatch: {existing}"
        assert set(by_src.keys()) == {
            "UserController#list",
            "DeviceController#list",
        }, f"by_source keys: {by_src.keys()}"
        assert by_src["UserController#list"]["apifox_id"] == "10086"
        assert by_src["DeviceController#list"]["apifox_id"] is None
        assert by_src["UserController#list"]["folder"] == "用户管理"
        assert by_src["UserController#list"]["method"] == "GET"
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
        print("Usage: push_index.py <export_json> | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(argv[1], tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
