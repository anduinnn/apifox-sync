#!/usr/bin/env python3
"""对本地 spec 与远端 index 做 update/rename/create/skip 分类。

用法：
    python3 push_classify.py <spec_json>
    python3 push_classify.py -h
    python3 push_classify.py --self-test

argv:
    <spec_json>  本地生成的 OpenAPI spec 文件（通常是 ${TMPPREFIX}spec.json）

env:
    TMPPREFIX  临时文件前缀（必填）

输入（由 push_index.py 产出）:
    ${TMPPREFIX}existing.json
    ${TMPPREFIX}by-source.json

输出文件（按需写入；为空则不写）:
    ${TMPPREFIX}payload-update.json   AUTO_MERGE 批次
    ${TMPPREFIX}payload-create.json   CREATE_NEW 批次
    ${TMPPREFIX}rename-list.json      重命名候选清单

stdout 摘要:
    更新批次: N 个接口
    新建批次: M 个接口
    重命名候选(死接口待确认): K 个
    [按 rename-list 展开的明细]
    跳过(冲突): X 个接口

等价替换 push-api.md 步骤 11.2 内嵌 python3 逻辑。分类规则严格按 11.2：
    - update: source 锚点命中 & path/method 相同
    - rename: source 锚点命中 & path/method 变化
    - create: 无锚点 & 目标文件夹内无同 path+method
    - skip : 目标文件夹已有 & 其他文件夹也有（跨文件夹冲突）

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path


def classify(spec: dict, existing: dict, by_source: dict) -> tuple[dict, dict, list, list]:
    """返回 (update_paths, create_paths, rename_list, skipped)。

    与 push-api.md 步骤 11.2 等价。
    """
    update_paths: dict = {}
    create_paths: dict = {}
    rename_list: list = []
    skipped: list = []

    for path, methods in spec.get("paths", {}).items():
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            target_folder = detail.get("x-apifox-folder", "")
            source_fq = detail.get("x-source-method-fq")
            key = f"{method.upper()}:{path}"

            if source_fq and source_fq in by_source:
                prev = by_source[source_fq]
                if prev["path"] == path and prev["method"] == method.upper():
                    existing_folders = existing.get(key, [])
                    other_folders = [f for f in existing_folders if f != target_folder]
                    if target_folder in existing_folders and other_folders:
                        skipped.append(
                            f'{method.upper()} {path} (目标: {target_folder}, 冲突: {", ".join(other_folders)})'
                        )
                    else:
                        update_paths.setdefault(path, {})[method] = detail
                else:
                    rename_list.append(
                        {
                            "source_fq": source_fq,
                            "old": {
                                "path": prev["path"],
                                "method": prev["method"],
                                "folder": prev["folder"],
                            },
                            "new": {
                                "path": path,
                                "method": method.upper(),
                                "folder": target_folder,
                            },
                            "apifox_id": prev["apifox_id"],
                            "summary": prev.get("summary") or detail.get("summary", ""),
                        }
                    )
                    create_paths.setdefault(path, {})[method] = detail
                continue

            # 无锚点：降级到 path+method 匹配
            existing_folders = existing.get(key, [])
            if target_folder in existing_folders:
                other_folders = [f for f in existing_folders if f != target_folder]
                if other_folders:
                    skipped.append(
                        f'{method.upper()} {path} (目标: {target_folder}, 冲突: {", ".join(other_folders)})'
                    )
                else:
                    update_paths.setdefault(path, {})[method] = detail
            else:
                create_paths.setdefault(path, {})[method] = detail

    return update_paths, create_paths, rename_list, skipped


def build_spec(base_spec: dict, paths: dict) -> dict:
    s = copy.deepcopy(base_spec)
    s["paths"] = paths
    return s


def build_payload(spec_obj: dict, behavior: str, update_folder: bool) -> dict:
    return {
        "input": json.dumps(spec_obj, ensure_ascii=False),
        "options": {
            "endpointOverwriteBehavior": behavior,
            "schemaOverwriteBehavior": "OVERWRITE_EXISTING",
            "updateFolderOfChangedEndpoint": update_folder,
            "prependBasePath": False,
        },
    }


def run(spec_json_path: str, tmpprefix: str) -> int:
    spec = json.loads(Path(spec_json_path).read_text(encoding="utf-8"))
    existing = json.loads(Path(f"{tmpprefix}existing.json").read_text(encoding="utf-8"))
    by_source = json.loads(Path(f"{tmpprefix}by-source.json").read_text(encoding="utf-8"))

    update_paths, create_paths, rename_list, skipped = classify(spec, existing, by_source)

    if update_paths:
        payload = build_payload(build_spec(spec, update_paths), "AUTO_MERGE", False)
        with open(f"{tmpprefix}payload-update.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    if create_paths:
        payload = build_payload(build_spec(spec, create_paths), "CREATE_NEW", True)
        with open(f"{tmpprefix}payload-create.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    if rename_list:
        with open(f"{tmpprefix}rename-list.json", "w", encoding="utf-8") as f:
            json.dump(rename_list, f, ensure_ascii=False)

    update_count = sum(len(m) for m in update_paths.values())
    create_count = sum(len(m) for m in create_paths.values())
    print(f"更新批次: {update_count} 个接口")
    print(f"新建批次: {create_count} 个接口")
    print(f"重命名候选(死接口待确认): {len(rename_list)} 个")
    for r in rename_list:
        old, new = r["old"], r["new"]
        tag = r.get("summary") or r["source_fq"]
        print(f"  · {tag}")
        print(f'    旧: {old["method"]} {old["path"]}  (folder={old["folder"]})')
        print(f'    新: {new["method"]} {new["path"]}  (folder={new["folder"]})')
    if skipped:
        print(f"跳过(冲突): {len(skipped)} 个接口")
        for s in skipped:
            print(f"  - {s}")
        print(
            "提示: 以上接口在多个文件夹中存在同 path+method，AUTO_MERGE 无法精确更新目标文件夹的接口。"
            "请在 Apifox 中手动删除其他文件夹的重复接口后重新推送。"
        )
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        # existing: 远端已有的 key → folders
        existing = {
            "GET:/api/users": ["用户管理"],          # 用于 update
            "PUT:/api/users": ["用户管理"],          # 用于 rename (旧)
            "POST:/api/legacy": ["旧模块", "新模块"],  # 用于 skip
        }
        # by_source: source_fq → 远端元数据
        by_source = {
            "UserController#list": {
                "path": "/api/users",
                "method": "GET",
                "folder": "用户管理",
                "apifox_id": "1001",
                "controller": "UserController",
                "summary": "列出用户",
            },
            "UserController#update": {
                "path": "/api/users",
                "method": "PUT",
                "folder": "用户管理",
                "apifox_id": "1002",
                "controller": "UserController",
                "summary": "更新用户",
            },
        }
        # spec: 本地生成的 OpenAPI
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/users": {
                    "get": {
                        "summary": "列出用户",
                        "x-apifox-folder": "用户管理",
                        "x-source-method-fq": "UserController#list",
                    }
                },
                # rename: 锚点命中但 path 变化
                "/api/users/{id}": {
                    "put": {
                        "summary": "更新用户",
                        "x-apifox-folder": "用户管理",
                        "x-source-method-fq": "UserController#update",
                    }
                },
                # create: 无锚点 & 目标文件夹无同 key
                "/api/orders": {
                    "post": {
                        "summary": "创建订单",
                        "x-apifox-folder": "订单管理",
                    }
                },
                # skip: 无锚点 & 跨文件夹冲突
                "/api/legacy": {
                    "post": {
                        "summary": "遗留",
                        "x-apifox-folder": "新模块",
                    }
                },
            },
        }
        Path(f"{prefix}existing.json").write_text(
            json.dumps(existing, ensure_ascii=False), encoding="utf-8"
        )
        Path(f"{prefix}by-source.json").write_text(
            json.dumps(by_source, ensure_ascii=False), encoding="utf-8"
        )
        spec_path = tmp / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(spec_path), prefix)
        assert rc == 0

        upd = json.loads(Path(f"{prefix}payload-update.json").read_text(encoding="utf-8"))
        crt = json.loads(Path(f"{prefix}payload-create.json").read_text(encoding="utf-8"))
        ren = json.loads(Path(f"{prefix}rename-list.json").read_text(encoding="utf-8"))

        upd_spec = json.loads(upd["input"])
        assert "/api/users" in upd_spec["paths"]
        assert "get" in upd_spec["paths"]["/api/users"]
        assert upd["options"]["endpointOverwriteBehavior"] == "AUTO_MERGE"
        assert upd["options"]["updateFolderOfChangedEndpoint"] is False

        crt_spec = json.loads(crt["input"])
        # create 批次：rename 的新接口 + 订单管理的新增
        assert "/api/orders" in crt_spec["paths"]
        assert "/api/users/{id}" in crt_spec["paths"]
        assert crt["options"]["endpointOverwriteBehavior"] == "CREATE_NEW"
        assert crt["options"]["updateFolderOfChangedEndpoint"] is True

        assert len(ren) == 1
        assert ren[0]["source_fq"] == "UserController#update"
        assert ren[0]["old"]["path"] == "/api/users"
        assert ren[0]["new"]["path"] == "/api/users/{id}"
        assert ren[0]["apifox_id"] == "1002"
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
        print("Usage: push_classify.py <spec_json> | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(argv[1], tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
