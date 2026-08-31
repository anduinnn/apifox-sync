#!/usr/bin/env python3
"""检测本次推送是否会静默覆盖「非本次来源」的远端 schema，并支持加前缀改名。

用法：
    python3 push_schema_conflict.py <spec_json> <export_json>
    python3 push_schema_conflict.py <spec_json> --apply-prefix <前缀>
    python3 push_schema_conflict.py -h
    python3 push_schema_conflict.py --self-test

argv（检测模式）:
    <spec_json>    本地生成的 OpenAPI spec 文件（通常是 ${TMPPREFIX}spec.json）
    <export_json>  Apifox export-openapi 写入的 JSON 文件（通常是 ${TMPPREFIX}export.json）

argv（改名模式）:
    <spec_json>  待改名的 OpenAPI spec 文件，就地重写
    <前缀>       加在冲突 schema 名前的前缀（如 Controller 简单类名去掉 Controller 后缀）

env:
    TMPPREFIX  临时文件路径前缀（检测模式用于写 schema-conflicts.json；
               改名模式用于读取同一份 schema-conflicts.json 取待改名清单）

输出（仅在检出冲突时写入文件，走 TMPPREFIX）:
    ${TMPPREFIX}schema-conflicts.json   [{"schema": str, "owners": [str, ...]}, ...]

stdout 摘要（检测模式）:
    schema 冲突: N 个
      · <schema>  被占用: <controller1>、<controller2>
      ...
    （有冲突时追加一行覆盖提示）

stdout 摘要（改名模式）:
    已改名 N 个 schema（前缀 <前缀>）
      · <旧名> → <前缀><旧名>
      ...
    若新名撞上「本次不改名的既有 schema」（链式改名——如待改名集合同时含
    Money 和 OrderMoney、前缀恰为 Order——不算冲突，会正确改名），不做任何
    修改，冲突清单打到 stderr，退出码 1。

背景：推送时用 schemaOverwriteBehavior: OVERWRITE_EXISTING 提交
components.schemas，同名 schema 直接覆盖、没有任何提示。响应 VO 中名为
Location/Detail/Item 这类通用名的静态内部类，schema 名会落成简单类名；若
远端已有别的 Controller 建的同名 schema，会被静默覆盖，引用它的其他接口
文档随之失真。检测模式：以 export.json 的 components.schemas 建图
（schema → 它引用的 schema），从每个 operation 直接 $ref 出发做 DFS 传播
归属（传递闭包），据此判断本次 spec 会覆盖到哪些「非本次来源」的远端
schema；其中内容与远端定义等价（忽略 Apifox 注入的 x-apifox-* 扩展字段）
的一律跳过——覆盖是 no-op，典型场景是 R_String/Page_XxxVO 这类框架包装
schema，天然被多个 Controller 共同引用，否则每次推送都会误报。改名模式：
读取检测模式写下的 schema-conflicts.json，把其中列出的 schema 键加前缀并
同步重写 spec 内所有指向它们的 $ref，就地覆盖 spec_json。

退出码：
    0 成功（检测模式有无冲突都算成功执行，是否阻断由对话层决策）
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


def schemas_of(doc: dict) -> dict:
    """安全取出 doc["components"]["schemas"]，非 dict 一律降级为空字典。

    export.json / spec.json 均可能因上游异常而结构畸形（如 components 是
    字符串、schemas 是列表），与 push_verify.py 对 remote 的归一化保持一致，
    避免 AttributeError/TypeError 中断整个检测流程。
    """
    components = doc.get("components", {})
    components = components if isinstance(components, dict) else {}
    schemas = components.get("schemas", {})
    return schemas if isinstance(schemas, dict) else {}


def _strip_apifox_ext(node):
    """递归剥离 Apifox 注入的 x-apifox-* 扩展字段（如属性顺序 x-apifox-orders）。

    用于判断 schema 内容是否语义等价：远端导出的 schema 常带这类元数据，与
    本地新生成的 spec 逐字节比较必然不等，但这些字段不影响 schema 语义。
    """
    if isinstance(node, dict):
        return {
            k: _strip_apifox_ext(v)
            for k, v in node.items()
            if not (isinstance(k, str) and k.startswith("x-apifox-"))
        }
    if isinstance(node, list):
        return [_strip_apifox_ext(v) for v in node]
    return node


def schema_equivalent(a, b) -> bool:
    """判断两份 schema 定义是否语义等价（忽略 Apifox 注入的扩展字段）。"""
    return _strip_apifox_ext(a) == _strip_apifox_ext(b)


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
    schemas = schemas_of(export)
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
    """本次 spec 中会覆盖到「非本次来源」的远端 schema，按名字典序返回。

    内容与远端定义等价（见 schema_equivalent）的一律跳过：覆盖是 no-op，
    不构成真实冲突——典型场景是 R_String/Page_XxxVO 这类框架包装 schema，
    天然被多个 Controller 共同引用，若不降噪则每次推送都会误报。
    """
    export_schemas_dict = schemas_of(export)
    spec_schemas_dict = schemas_of(spec)
    export_schemas = set(export_schemas_dict)
    spec_schemas = set(spec_schemas_dict)
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
        if not foreign:
            continue
        if schema_equivalent(spec_schemas_dict[name], export_schemas_dict[name]):
            continue
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


def _rewrite_refs(node, mapping: dict[str, str]) -> None:
    """就地重写节点内所有指向 mapping 旧名的 $ref。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            old = ref.rsplit("/", 1)[-1]
            if old in mapping:
                node["$ref"] = f"#/components/schemas/{mapping[old]}"
        for v in node.values():
            _rewrite_refs(v, mapping)
    elif isinstance(node, list):
        for v in node:
            _rewrite_refs(v, mapping)


def apply_prefix(spec: dict, names: set[str], prefix: str) -> int:
    """把 names 中的 schema 键加上 prefix，并同步全部 $ref。返回改名数量。

    改名前预检真冲突：新名撞上「本次不改名的既有 schema」（即
    kept = set(schemas) - set(mapping) 中的键）。命中即不做任何修改、把冲突
    清单打到 stderr、返回 -1（不是「改名数量」，调用方需按负数判定失败，不可
    当计数用）。

    链式改名（如 names 同时含 A 和 XA、prefix=X，A 的新名 XA 恰是批次内另一个
    待改名的旧键）不是冲突：XA 这个名字正在被腾空，不构成真撞车。一次性重建
    schemas 字典（而非在原字典上 pop/assign）天然让这种情形顺序无关且安全——
    重建时读的是改名前的原始内容，不受处理顺序影响。

    mapping 的值恒为 f"{prefix}{n}"，prefix 固定、names 互异时新名必然互异，
    「两个不同旧名映射到同一新名」在当前构造下不可达；下面的重复目标检查留作
    防御性代码，不指望它会触发。
    """
    schemas = schemas_of(spec)
    mapping = {n: f"{prefix}{n}" for n in names if n in schemas}
    if not mapping:
        return 0
    kept = set(schemas) - set(mapping)
    targets = list(mapping.values())
    collisions = sorted({
        t for t in targets
        if t in kept or targets.count(t) > 1   # 后者当前不可达，见上方 docstring
    })
    if collisions:
        print("ERROR: 改名冲突，未做任何修改：", file=sys.stderr)
        for c in collisions:
            print(f"  · {c} 已被既有 schema 占用", file=sys.stderr)
        return -1
    new_schemas = {mapping.get(k, k): v for k, v in schemas.items()}
    spec["components"]["schemas"] = new_schemas
    _rewrite_refs(spec, mapping)
    return len(mapping)


def run_apply(spec_path: str, prefix: str, tmpprefix: str) -> int:
    conflict_file = Path(f"{tmpprefix}schema-conflicts.json")
    if not conflict_file.is_file():
        print("无冲突清单，跳过改名")
        return 0
    conflicts = json.loads(conflict_file.read_text(encoding="utf-8"))
    names = {c["schema"] for c in conflicts}
    spec = load_json_loose(spec_path)
    n = apply_prefix(spec, names, prefix)
    if n < 0:
        return 1
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"已改名 {n} 个 schema（前缀 {prefix}）")
    for name in sorted(names):
        print(f"  · {name} → {prefix}{name}")
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

    # --apply-prefix：键改名 + 全部 $ref 同步（含数组、allOf 等嵌套位置）
    spec2 = {
        "paths": {"/u": {"get": {
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "array",
                           "items": {"$ref": "#/components/schemas/Money"}}}}}},
        }}},
        "components": {"schemas": {
            "Money": {"type": "object"},
            "Order": {"type": "object", "allOf": [
                {"$ref": "#/components/schemas/Money"},
                {"type": "object",
                 "properties": {"m": {"$ref": "#/components/schemas/Money"}}},
            ]},
        }},
    }
    n = apply_prefix(spec2, {"Money"}, "Order")
    assert n == 1, n
    schemas2 = spec2["components"]["schemas"]
    assert "OrderMoney" in schemas2 and "Money" not in schemas2, list(schemas2)
    # 未列入改名的 schema 保持原名
    assert "Order" in schemas2
    # 所有 $ref 均已重写，spec 中不应再出现旧引用
    dumped = json.dumps(spec2, ensure_ascii=False)
    assert "#/components/schemas/Money" not in dumped, dumped
    assert dumped.count("#/components/schemas/OrderMoney") == 3, dumped
    # 不存在的名字不报错、不计数
    assert apply_prefix(spec2, {"NotThere"}, "X") == 0

    # --apply-prefix 冲突守卫 1：新名撞上「本次不改名的既有 schema」
    # OrderMoney 是与 Money 改名目标同名、但并未列入本次改名清单的既有 schema，
    # 若无守卫，pop/assign 会静默用 Money 的内容覆盖它——这正是本功能要防止的
    # 静默覆盖，本身犯了同一种错误。
    spec5 = {
        "components": {"schemas": {
            "Money": {"type": "object", "tag": "money-orig"},
            "OrderMoney": {"type": "object", "tag": "unrelated-existing"},
        }},
    }
    before5 = json.loads(json.dumps(spec5, ensure_ascii=False))
    n = apply_prefix(spec5, {"Money"}, "Order")
    assert n < 0, n   # 冲突不是「改名数量」，用负数区分，调用方不可当计数用
    assert spec5 == before5, "冲突时不应做任何修改"

    # --apply-prefix 链式改名（不是冲突，必须成功）：names 同时含 A 和 XA、
    # prefix=X 时，A 的新名 XA 恰是批次内另一个待改名的旧键——但 XA 这个名字
    # 正在被腾空，不构成真撞车。一次性重建 schemas 字典应保证顺序无关地正确
    # 完成：XA 拿到原 A 的内容，XXA 拿到原 XA 的内容，且两处 $ref 分别指向
    # 正确的新名（不能互相混淆，也不能有一个仍留旧引用）。
    spec6 = {
        "paths": {"/x": {"get": {
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "object", "properties": {
                    "a": {"$ref": "#/components/schemas/A"},
                    "xa": {"$ref": "#/components/schemas/XA"},
                }}}}}},
        }}},
        "components": {"schemas": {
            "A": {"type": "object", "tag": "a-orig"},
            "XA": {"type": "object", "tag": "xa-orig"},
        }},
    }
    n = apply_prefix(spec6, {"A", "XA"}, "X")
    assert n == 2, n
    schemas6 = spec6["components"]["schemas"]
    assert set(schemas6) == {"XA", "XXA"}, list(schemas6)
    assert schemas6["XA"]["tag"] == "a-orig", schemas6["XA"]     # A 的内容随 A→XA 落到 XA
    assert schemas6["XXA"]["tag"] == "xa-orig", schemas6["XXA"]  # XA 的内容随 XA→XXA 落到 XXA，未被途中覆盖丢失
    props = spec6["paths"]["/x"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert props["a"]["$ref"] == "#/components/schemas/XA", props["a"]
    assert props["xa"]["$ref"] == "#/components/schemas/XXA", props["xa"]

    # I3(a) 降噪：foreign schema 内容与远端等价（仅差 Apifox 注入的
    # x-apifox-* 元数据）时覆盖是 no-op，不应计入冲突——典型场景是
    # R_String 这类被多个 Controller 共享的框架包装 schema。真正内容不同
    # 的 foreign schema 必须仍然报冲突，否则会掩盖真实覆盖风险。
    export_equiv = {
        "paths": {
            "/o": {"get": {"x-source-controller": "com.x.OrderController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/R_String"}}}}}}},
        },
        "components": {"schemas": {
            "R_String": {
                "type": "object",
                "properties": {"code": {"type": "integer"}, "data": {"type": "string"}},
                "x-apifox-orders": ["code", "data"],   # 远端注入的排序元数据
            },
        }},
    }
    spec_equiv_same = {
        "paths": {"/u": {"get": {"x-source-controller": "com.x.UserController"}}},
        "components": {"schemas": {
            # 语义与远端完全相同，仅缺少远端注入的 x-apifox-orders
            "R_String": {
                "type": "object",
                "properties": {"code": {"type": "integer"}, "data": {"type": "string"}},
            },
        }},
    }
    assert find_conflicts(spec_equiv_same, export_equiv) == [], \
        find_conflicts(spec_equiv_same, export_equiv)

    spec_equiv_diff = {
        "paths": {"/u": {"get": {"x-source-controller": "com.x.UserController"}}},
        "components": {"schemas": {
            # 内容真的不同（多一个字段），必须仍然报冲突
            "R_String": {
                "type": "object",
                "properties": {"code": {"type": "integer"}, "data": {"type": "string"},
                               "extra": {"type": "boolean"}},
            },
        }},
    }
    conflicts_diff = find_conflicts(spec_equiv_diff, export_equiv)
    assert [c["schema"] for c in conflicts_diff] == ["R_String"], conflicts_diff

    # 类型守卫一致性：components/schemas 非 dict（畸形导出/畸形 spec）时应
    # 安全降级为空字典，而不是抛 AttributeError/TypeError 中断整个流程——
    # 与 push_verify.py 对 remote 的归一化保持一致。
    malformed_export = {"paths": {}, "components": "oops"}
    assert schemas_of(malformed_export) == {}
    assert build_owners(malformed_export) == {}, build_owners(malformed_export)

    malformed_spec = {"paths": {}, "components": ["not", "a", "dict"]}
    assert schemas_of(malformed_spec) == {}
    assert find_conflicts(malformed_spec, export) == [], find_conflicts(malformed_spec, export)

    malformed_spec_apply = {"components": "oops", "paths": {}}
    assert apply_prefix(malformed_spec_apply, {"Anything"}, "X") == 0

    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) == 4 and argv[2] == "--apply-prefix":
        mode, summary = "apply", f"spec={argv[1]} prefix={argv[3]}"
    elif len(argv) == 3 and argv[2] != "--apply-prefix":
        mode, summary = "detect", f"spec={argv[1]} export={argv[2]}"
    else:
        print("Usage: push_schema_conflict.py <spec_json> <export_json> "
              "| <spec_json> --apply-prefix <前缀> | -h | --self-test",
              file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    _t0 = time.time()
    if mode == "apply":
        rc = run_apply(argv[1], argv[3], tmpprefix)
        step = "push.push_schema_conflict.apply"
    else:
        rc = run_detect(argv[1], argv[2], tmpprefix)
        step = "push.push_schema_conflict"
    if rc == 0:
        debug_log(step, "success", int((time.time() - _t0) * 1000), input_summary=summary)
    else:
        debug_log(step, "error", int((time.time() - _t0) * 1000),
                  input_summary=summary, error_detail=f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
