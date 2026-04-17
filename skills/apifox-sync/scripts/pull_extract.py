#!/usr/bin/env python3
"""按 folder 切片 + schema 递归收集 + 精简 → 每个子目录写一个临时 JSON。

用法：
    python3 pull_extract.py --folders-file <folders_json> <export_json>
    python3 pull_extract.py -h
    python3 pull_extract.py --self-test

argv:
    --folders-file <path>  JSON 数组文件，内容为用户选中的 folder 字符串数组
    <export_json>          Apifox export-openapi 写入的 JSON 文件

env:
    TMPPREFIX  临时文件前缀（必填）

输出：
    ${TMPPREFIX}pull-<encoded_folder>.json  每个 actual folder 一个精简 JSON
    （encoded 由 path_codec.encode 生成，decode 反向可得原 folder 路径）

stdout 摘要（每 folder 一行）:
    OK: "<actual_folder>" — <api_count>接口, <schema_count>Schema → <path>

等价替换 pull.md 步骤 4（含步骤 5 精简规则）内嵌 python3 逻辑。保持
KEEP_EXTENSIONS 白名单、前缀匹配语义、schema 递归收集等与原实现一致。

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# 允许从同目录导入 path_codec
sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_codec  # noqa: E402

KEEP_EXTENSIONS = {"x-apifox-folder", "x-apifox-status", "x-apifox-enum"}


def load_export(path: str) -> dict:
    """读取 export.json，带 pull.md 里的 `\\` 容错修正。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw)
    return json.loads(raw, strict=False)


def clean_extensions(obj) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key.startswith("x-") and key not in KEEP_EXTENSIONS:
                del obj[key]
        for v in obj.values():
            clean_extensions(v)
    elif isinstance(obj, list):
        for item in obj:
            clean_extensions(item)


def collect_refs(obj, refs: set) -> None:
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                refs.add(ref.split("/")[-1])
        for v in obj.values():
            collect_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            collect_refs(item, refs)


def run(folders: list[str], export_path: str, tmpprefix: str) -> int:
    data = load_export(export_path)

    # 按实际 x-apifox-folder 分组
    folder_groups: dict[str, dict] = {}
    for sel_folder in folders:
        for path, methods in data.get("paths", {}).items():
            for method, detail in methods.items():
                if isinstance(detail, dict):
                    op_folder = detail.get("x-apifox-folder", "")
                    if op_folder == sel_folder or op_folder.startswith(sel_folder + "/"):
                        folder_groups.setdefault(op_folder, {}).setdefault(path, {})[method] = detail

    if not folder_groups:
        print("WARN: 所选目录下未找到任何接口")
        return 0

    all_schemas = data.get("components", {}).get("schemas", {})

    for actual_folder in sorted(folder_groups.keys()):
        filtered_paths = folder_groups[actual_folder]

        # 递归收集 schema 引用
        refs: set[str] = set()
        collect_refs(filtered_paths, refs)

        resolved: set[str] = set()

        def resolve_schema_refs(name: str) -> None:
            if name in resolved or name not in all_schemas:
                return
            resolved.add(name)
            collect_refs(all_schemas[name], refs)
            for ref_name in list(refs):
                if ref_name not in resolved:
                    resolve_schema_refs(ref_name)

        for ref_name in list(refs):
            resolve_schema_refs(ref_name)

        filtered_schemas = {name: all_schemas[name] for name in refs if name in all_schemas}

        clean_extensions(filtered_paths)
        clean_extensions(filtered_schemas)

        result = {
            "paths": filtered_paths,
            "components": {"schemas": filtered_schemas},
        }

        safe_name = path_codec.encode(actual_folder)
        out_path = f"{tmpprefix}pull-{safe_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        api_count = sum(len(m) for m in filtered_paths.values())
        schema_count = len(filtered_schemas)
        print(f'OK: "{actual_folder}" — {api_count} 个接口, {schema_count} 个 Schema → {out_path}')

    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        # fixture：两个 folder：普通 / 含 __ 的历史目录名 + 子目录前缀匹配
        fixture = {
            "paths": {
                "/api/users": {
                    "get": {
                        "summary": "列表",
                        "x-apifox-folder": "分批测试",
                        "x-apifox-id": "should_be_removed",
                        "x-apifox-status": "released",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/User"}
                                    }
                                }
                            }
                        },
                    }
                },
                "/api/users/detail": {
                    "get": {
                        "summary": "详情",
                        "x-apifox-folder": "分批测试/A",
                    }
                },
                "/api/legacy": {
                    "post": {
                        "summary": "遗留",
                        "x-apifox-folder": "历史__组",
                    }
                },
                # 不匹配的 folder
                "/api/other": {"get": {"summary": "x", "x-apifox-folder": "其他"}},
            },
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {"roles": {"$ref": "#/components/schemas/Role"}},
                    },
                    "Role": {"type": "string"},
                    "Unused": {"type": "string"},
                }
            },
        }
        export_path = tmp / "export.json"
        export_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        prefix = str(tmp) + "/apifox-sync-"
        os.environ["TMPPREFIX"] = prefix
        folders = ["分批测试", "历史__组"]
        rc = run(folders, str(export_path), prefix)
        assert rc == 0

        # 验证 3 个切片文件：分批测试、分批测试/A（前缀匹配）、历史__组
        f1 = Path(f"{prefix}pull-{path_codec.encode('分批测试')}.json")
        f2 = Path(f"{prefix}pull-{path_codec.encode('分批测试/A')}.json")
        f3 = Path(f"{prefix}pull-{path_codec.encode('历史__组')}.json")
        assert f1.is_file(), f1
        assert f2.is_file(), f2
        assert f3.is_file(), f3

        d1 = json.loads(f1.read_text(encoding="utf-8"))
        assert "/api/users" in d1["paths"]
        # x-apifox-id 应被清理
        assert "x-apifox-id" not in d1["paths"]["/api/users"]["get"]
        # x-apifox-status 应保留
        assert d1["paths"]["/api/users"]["get"]["x-apifox-status"] == "released"
        # schema 递归收集：User + Role，但不含 Unused
        assert set(d1["components"]["schemas"].keys()) == {"User", "Role"}

        d3 = json.loads(f3.read_text(encoding="utf-8"))
        assert "/api/legacy" in d3["paths"]
        # 不应包含其他 folder
        assert "/api/other" not in d3["paths"]
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
    # 期望形态：--folders-file <path> <export_json>
    if len(argv) != 4 or argv[1] != "--folders-file":
        print(
            "Usage: pull_extract.py --folders-file <folders_json> <export_json> | -h | --self-test",
            file=sys.stderr,
        )
        return 2
    folders_file, export_path = argv[2], argv[3]
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    try:
        folders = json.loads(Path(folders_file).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: failed to read folders-file: {e}", file=sys.stderr)
        return 1
    if not isinstance(folders, list):
        print("ERROR: folders-file must be a JSON array", file=sys.stderr)
        return 1
    return run([str(f) for f in folders], export_path, tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
