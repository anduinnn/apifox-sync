#!/usr/bin/env python3
"""检测本次推送是否会静默覆盖「非本次来源」的远端 schema（命名冲突检测）。

用法：
    python3 push_schema_conflict.py <spec_json> <export_json>
    python3 push_schema_conflict.py -h
    python3 push_schema_conflict.py --self-test

argv:
    <spec_json>    本地生成的 OpenAPI spec 文件（通常是 ${TMPPREFIX}spec.json）
    <export_json>  Apifox export-openapi 写入的 JSON 文件（通常是 ${TMPPREFIX}export.json）

env:
    TMPPREFIX  临时文件路径前缀（用于写 schema-conflicts.json）

输出（仅在检出冲突时写入文件，走 TMPPREFIX）:
    ${TMPPREFIX}schema-conflicts.json   [{"schema": str, "owners": [str, ...]}, ...]

stdout 摘要:
    schema 冲突: N 个
      · <schema>  被占用: <controller1>、<controller2>
      ...
    （有冲突时追加一行覆盖提示）

背景：推送时用 schemaOverwriteBehavior: OVERWRITE_EXISTING 提交
components.schemas，同名 schema 直接覆盖、没有任何提示。响应 VO 中名为
Location/Detail/Item 这类通用名的静态内部类，schema 名会落成简单类名；若
远端已有别的 Controller 建的同名 schema，会被静默覆盖，引用它的其他接口
文档随之失真。本脚本只做检测：以 export.json 的 components.schemas 建图
（schema → 它引用的 schema），从每个 operation 直接 $ref 出发做 BFS 传播
归属（传递闭包），据此判断本次 spec 会覆盖到哪些「非本次来源」的远端
schema。不含改名逻辑（Task 4 在此基础上接入改名并接进 push 管线）。

退出码：
    0 成功（有无冲突都算成功执行，是否阻断由对话层决策）
    1 运行时错误
    2 参数用法错误
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402
from debug_log import debug_log  # noqa: E402


def refs_of(node) -> set[str]:
    """递归收集节点内所有 $ref 指向的 schema 简名。"""
    found: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            ref = cur.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                found.add(ref.rsplit("/", 1)[-1])
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return found


def build_owners(export: dict) -> dict[str, set[str]]:
    """schema 名 → 占用它的 x-source-controller 集合，含传递闭包。

    仅被其他 schema 间接引用的 schema 也会被正确归属；无 x-source-controller
    的 operation 归属为空串（来源未知）。
    """
    schemas = export.get("components", {}).get("schemas", {})
    graph = {name: refs_of(body) for name, body in schemas.items()}
    owners: dict[str, set[str]] = {}
    paths = export.get("paths", {})
    if not isinstance(paths, dict):
        return owners
    for methods in paths.values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            controller = detail.get("x-source-controller", "") or ""
            queue = list(refs_of(detail))
            seen = set(queue)
            while queue:
                name = queue.pop()
                owners.setdefault(name, set()).add(controller)
                for nxt in graph.get(name, set()):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
    return owners


def find_conflicts(spec: dict, export: dict) -> list[dict]:
    """本次 spec 中会覆盖到「非本次来源」的远端 schema，按名字典序返回。"""
    export_schemas = set(export.get("components", {}).get("schemas", {}))
    spec_schemas = set(spec.get("components", {}).get("schemas", {}))
    mine: set[str] = set()
    paths = spec.get("paths", {})
    if isinstance(paths, dict):
        for methods in paths.values():
            if not isinstance(methods, dict):
                continue
            for detail in methods.values():
                if isinstance(detail, dict) and detail.get("x-source-controller"):
                    mine.add(detail["x-source-controller"])
    owners = build_owners(export)
    conflicts = []
    for name in sorted(spec_schemas & export_schemas):
        own = owners.get(name) or {""}   # 远端有但无人引用 → 孤儿，来源未知，从严计冲突
        foreign = own - mine
        if foreign:
            conflicts.append({"schema": name, "owners": sorted(foreign)})
    return conflicts


def run_detect(spec_path: str, export_path: str, tmpprefix: str) -> int:
    spec = load_json_loose(spec_path)
    export = load_json_loose(export_path)
    conflicts = find_conflicts(spec, export)
    print(f"schema 冲突: {len(conflicts)} 个")
    for c in conflicts:
        who = "、".join(o if o else "(来源未知)" for o in c["owners"])
        print(f'  · {c["schema"]}  被占用: {who}')
    if conflicts:
        with open(f"{tmpprefix}schema-conflicts.json", "w", encoding="utf-8") as f:
            json.dump(conflicts, f, ensure_ascii=False)
        print("提示: 继续推送将以 OVERWRITE_EXISTING 覆盖上述 schema 的远端定义。")
    return 0


def self_test() -> int:
    export = {
        "paths": {
            # 同源：UserController 引用 Location
            "/u": {"get": {"x-source-controller": "com.x.UserController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Location"}}}}}}},
            # 跨源：OrderController 引用 Money
            "/o": {"get": {"x-source-controller": "com.x.OrderController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Money"}}}}}}},
            # 跨源 + 间接：DeviceController 只直接引用 Wrapper，Wrapper 引用 Detail
            "/d": {"get": {"x-source-controller": "com.x.DeviceController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Wrapper"}}}}}}},
        },
        "components": {"schemas": {
            "Location": {"type": "object"},
            "Money": {"type": "object"},
            "Wrapper": {"type": "object",
                        "properties": {"detail": {"$ref": "#/components/schemas/Detail"}}},
            "Detail": {"type": "object"},
            "Ghost": {"type": "object"},          # 孤儿：远端存在但无接口引用
        }},
    }
    spec = {
        "paths": {"/u": {"get": {"x-source-controller": "com.x.UserController"}}},
        "components": {"schemas": {
            "Location": {}, "Money": {}, "Detail": {}, "Ghost": {}, "Brand新": {},
        }},
    }

    owners = build_owners(export)
    # 传递闭包：Detail 虽只被 Wrapper 引用，也应归属 DeviceController
    assert owners["Detail"] == {"com.x.DeviceController"}, owners.get("Detail")
    assert owners["Location"] == {"com.x.UserController"}
    assert "Ghost" not in owners, "孤儿 schema 不应有归属"

    conflicts = find_conflicts(spec, export)
    names = [c["schema"] for c in conflicts]
    # Location 同源 → 不报；Money/Detail 跨源 → 报；Ghost 无主 → 报；Brand新 远端没有 → 不报
    assert names == ["Detail", "Ghost", "Money"], names
    assert conflicts[2]["owners"] == ["com.x.OrderController"], conflicts[2]
    ghost = [c for c in conflicts if c["schema"] == "Ghost"][0]
    assert ghost["owners"] == [""], ghost   # 来源未知

    # run_detect：有冲突时应写 ${TMPPREFIX}schema-conflicts.json，无冲突时不写
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-schemaconflict-"))
    try:
        export_path = tmp / "export.json"
        export_path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

        spec_path = tmp / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        prefix_conflict = str(tmp) + "/conflict-"
        rc = run_detect(str(spec_path), str(export_path), prefix_conflict)
        assert rc == 0
        out_file = Path(f"{prefix_conflict}schema-conflicts.json")
        assert out_file.exists(), "有冲突时应写 schema-conflicts.json"
        written = json.loads(out_file.read_text(encoding="utf-8"))
        assert [c["schema"] for c in written] == ["Detail", "Ghost", "Money"], written

        spec_clean_path = tmp / "spec-clean.json"
        # 仅引用同源 Location，不应产生冲突
        spec_clean = {
            "paths": {"/u": {"get": {"x-source-controller": "com.x.UserController"}}},
            "components": {"schemas": {"Location": {}}},
        }
        spec_clean_path.write_text(json.dumps(spec_clean, ensure_ascii=False), encoding="utf-8")
        prefix_clean = str(tmp) + "/clean-"
        rc = run_detect(str(spec_clean_path), str(export_path), prefix_clean)
        assert rc == 0
        assert not Path(f"{prefix_clean}schema-conflicts.json").exists(), "无冲突时不应写文件"
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
    if len(argv) != 3:
        print("Usage: push_schema_conflict.py <spec_json> <export_json> | -h | --self-test",
              file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    _t0 = time.time()
    rc = run_detect(argv[1], argv[2], tmpprefix)
    if rc == 0:
        debug_log("push.push_schema_conflict", "success", int((time.time() - _t0) * 1000),
                  input_summary=f"spec={argv[1]} export={argv[2]}")
    else:
        debug_log("push.push_schema_conflict", "error", int((time.time() - _t0) * 1000),
                  input_summary=f"spec={argv[1]} export={argv[2]}", error_detail=f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
