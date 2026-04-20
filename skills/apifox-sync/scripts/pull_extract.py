#!/usr/bin/env python3
"""按接口切片 + schema 递归收集 + 精简 → 每个接口写一个临时 JSON。

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
    ${TMPPREFIX}pull-op-<hash>.json  每个接口一个精简 JSON
    hash 由 api_path.hash_key(folder, method, path) 生成（sha1 前 16 位）。

    每个 op 文件内部结构（标准 OpenAPI 片段 + x-apifox-folder 可读出 folder 归属）：
      {
        "paths": { "<path>": { "<method>": { ..., "x-apifox-folder": "<folder>" } } },
        "components": { "schemas": { ... 仅该接口递归引用 } }
      }

stdout 摘要（每接口一行）:
    OK: "<folder>" — <METHOD> <path> (<schema_count>Schema) → <path>

每个实际 folder 以 "-- folder: <folder> (<N> 个接口) --" 作为分隔行，便于人读。

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_path  # noqa: E402

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


def resolve_all_refs(seed_refs: set, all_schemas: dict) -> set:
    """从 seed_refs 出发递归解析所有传递依赖 schema 名。"""
    refs = set(seed_refs)
    resolved: set[str] = set()
    queue = list(refs)
    while queue:
        name = queue.pop()
        if name in resolved or name not in all_schemas:
            continue
        resolved.add(name)
        new_refs: set[str] = set()
        collect_refs(all_schemas[name], new_refs)
        for n in new_refs:
            if n not in refs:
                refs.add(n)
                queue.append(n)
    return refs


def run(folders: list[str], export_path: str, tmpprefix: str) -> int:
    data = load_export(export_path)
    all_schemas = data.get("components", {}).get("schemas", {})

    # 按实际 x-apifox-folder 分组 operation
    # folder_ops: {folder: [(path, method, detail), ...]}
    folder_ops: dict[str, list[tuple[str, str, dict]]] = {}
    for sel_folder in folders:
        for path, methods in data.get("paths", {}).items():
            if not isinstance(methods, dict):
                continue
            for method, detail in methods.items():
                if not isinstance(detail, dict):
                    continue
                op_folder = detail.get("x-apifox-folder", "")
                if op_folder == sel_folder or op_folder.startswith(sel_folder + "/"):
                    folder_ops.setdefault(op_folder, []).append((path, method, detail))

    if not folder_ops:
        print("WARN: 所选目录下未找到任何接口")
        return 0

    total_ops = 0
    for actual_folder in sorted(folder_ops.keys()):
        ops = folder_ops[actual_folder]
        print(f'-- folder: "{actual_folder}" ({len(ops)} 个接口) --')
        for path, method, detail in ops:
            # 1) 递归收集该接口需要的 schemas
            seed: set[str] = set()
            collect_refs(detail, seed)
            needed = resolve_all_refs(seed, all_schemas)
            filtered_schemas = {n: all_schemas[n] for n in needed if n in all_schemas}

            # 2) 构造单接口切片
            op_copy = detail  # 原数据本轮只读，clean 会修改，用原地引用即可
            slice_paths = {path: {method: op_copy}}
            clean_extensions(slice_paths)
            clean_extensions(filtered_schemas)

            # 确保 x-apifox-folder 留在 operation 上（clean_extensions 因在白名单中已保留）
            # 容错：若 operation 丢失 folder（不太可能），显式补回
            if "x-apifox-folder" not in op_copy:
                op_copy["x-apifox-folder"] = actual_folder

            result = {
                "paths": slice_paths,
                "components": {"schemas": filtered_schemas},
            }

            key = api_path.hash_key(actual_folder, method, path)
            out_path = f"{tmpprefix}pull-op-{key}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            print(
                f'OK: "{actual_folder}" — {method.upper()} {path} '
                f"({len(filtered_schemas)} Schema) → {out_path}"
            )
            total_ops += 1

    print(f"TOTAL: {total_ops} 个接口分布在 {len(folder_ops)} 个 folder")
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
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
                    },
                    "post": {
                        "summary": "创建",
                        "x-apifox-folder": "分批测试",
                    },
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

        # 期望 4 个 op 文件：GET /api/users、POST /api/users、GET /api/users/detail、POST /api/legacy
        key_get_users = api_path.hash_key("分批测试", "GET", "/api/users")
        key_post_users = api_path.hash_key("分批测试", "POST", "/api/users")
        key_detail = api_path.hash_key("分批测试/A", "GET", "/api/users/detail")
        key_legacy = api_path.hash_key("历史__组", "POST", "/api/legacy")

        f_get = Path(f"{prefix}pull-op-{key_get_users}.json")
        f_post = Path(f"{prefix}pull-op-{key_post_users}.json")
        f_detail = Path(f"{prefix}pull-op-{key_detail}.json")
        f_legacy = Path(f"{prefix}pull-op-{key_legacy}.json")
        for p in (f_get, f_post, f_detail, f_legacy):
            assert p.is_file(), p

        # 不应有其他 folder（"其他"）的文件
        other_key = api_path.hash_key("其他", "GET", "/api/other")
        assert not Path(f"{prefix}pull-op-{other_key}.json").exists()

        d_get = json.loads(f_get.read_text(encoding="utf-8"))
        # 单接口结构
        assert list(d_get["paths"].keys()) == ["/api/users"]
        assert list(d_get["paths"]["/api/users"].keys()) == ["get"]
        # 冗余扩展被清理
        op_get = d_get["paths"]["/api/users"]["get"]
        assert "x-apifox-id" not in op_get
        assert op_get["x-apifox-folder"] == "分批测试"
        assert op_get["x-apifox-status"] == "released"
        # schema 递归：User + Role，不含 Unused
        assert set(d_get["components"]["schemas"].keys()) == {"User", "Role"}

        # 无 ref 的接口 schemas 为空
        d_post = json.loads(f_post.read_text(encoding="utf-8"))
        assert d_post["components"]["schemas"] == {}

        # 子 folder 前缀匹配生效
        d_detail = json.loads(f_detail.read_text(encoding="utf-8"))
        assert d_detail["paths"]["/api/users/detail"]["get"]["x-apifox-folder"] == "分批测试/A"

        # 历史含 __ 的 folder 正常
        d_legacy = json.loads(f_legacy.read_text(encoding="utf-8"))
        assert d_legacy["paths"]["/api/legacy"]["post"]["x-apifox-folder"] == "历史__组"
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
