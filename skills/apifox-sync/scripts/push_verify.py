#!/usr/bin/env python3
"""比对推送后回读的远端数据与本次生成的 spec，校验 folder、锚点、schema 字段一致性。

用法：
    python3 push_verify.py <spec_json> <remote_json>
    python3 push_verify.py -h
    python3 push_verify.py --self-test

argv:
    <spec_json>    本次推送生成的 OpenAPI spec 文件（通常是 ${TMPPREFIX}spec.json）
    <remote_json>  推送完成后重新 export-openapi 得到的回读文件
                   （通常是 ${TMPPREFIX}verify.json）

stdout 摘要:
    全部通过：`✅ 回读校验通过：接口、folder、锚点、schema 字段均与本次推送一致`
    有不一致：`❌ 回读校验发现 N 处不一致：` 后逐条列出问题描述

背景：`import-openapi` 返回的 counters（如 endpointCreated）只说明请求被
Apifox 接受，不能证明 folder 落对了、schema 字段落全了、`x-source-method-fq`
锚点写进去了——这些都要重新导出一次远端实际状态才能验证。push 用
`schemaOverwriteBehavior: OVERWRITE_EXISTING`，据 Apifox 官方文档该行为是
「新导入的内容会完全替换掉旧的内容」而非合并，因此回读的 schema properties
理论上应与本次 spec 完全一致：多出的字段和缺失的字段一样，都指向覆盖没有
如预期生效（例如命中了别的 Controller 的同名 schema、或 Apifox 端有缓存/
异步落库延迟），因此本模块把两者都判定为不一致。

退出码：
    0 全部一致
    1 存在不一致，或运行时错误
    2 参数用法错误
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402
from debug_log import debug_log  # noqa: E402


def verify(spec: dict, remote: dict) -> list[str]:
    """比对本次 spec 与推送后回读的远端数据，返回问题列表（空 = 全部通过）。

    对 `paths`/`components` 等顶层字段做防御性 isinstance 校验：remote 来自
    export-openapi 的 HTTP 回读，理论上结构恒定，但不应假设外部接口永远返回
    预期形状——一旦某个字段异常，应把对应 operation/schema 判定为「远端不
    存在」而不是让 TypeError/AttributeError 中断整个校验流程。
    """
    problems: list[str] = []

    spec_paths = spec.get("paths", {})
    spec_paths = spec_paths if isinstance(spec_paths, dict) else {}
    remote_paths = remote.get("paths", {})
    remote_paths = remote_paths if isinstance(remote_paths, dict) else {}

    for path, methods in spec_paths.items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            label = f"{method.upper()} {path}"
            remote_methods = remote_paths.get(path)
            r = remote_methods.get(method) if isinstance(remote_methods, dict) else None
            if not isinstance(r, dict):
                problems.append(f"{label}: 远端不存在")
                continue
            # x-apifox-folder 缺省语义：空串代表「项目根目录」，本身是合法值，
            # 不是「未设置」的标记。用 `or ""` 把「键缺失」与「显式 None」两种
            # 写法都归一化成根目录，避免同一含义的两种表达被误判为不一致。
            want_folder = detail.get("x-apifox-folder") or ""
            got_folder = r.get("x-apifox-folder") or ""
            if want_folder != got_folder:
                problems.append(
                    f'{label}: folder 不符（期望 "{want_folder}"，实际 "{got_folder}"）'
                )
            want_fq = detail.get("x-source-method-fq")
            if want_fq and want_fq != r.get("x-source-method-fq"):
                problems.append(
                    f'{label}: 锚点不符（期望 {want_fq}，实际 {r.get("x-source-method-fq")}）'
                )

    spec_components = spec.get("components", {})
    spec_components = spec_components if isinstance(spec_components, dict) else {}
    spec_schemas = spec_components.get("schemas", {})
    spec_schemas = spec_schemas if isinstance(spec_schemas, dict) else {}
    remote_components = remote.get("components", {})
    remote_components = remote_components if isinstance(remote_components, dict) else {}
    remote_schemas = remote_components.get("schemas", {})
    remote_schemas = remote_schemas if isinstance(remote_schemas, dict) else {}

    for name, body in spec_schemas.items():
        r = remote_schemas.get(name)
        if not isinstance(r, dict):
            problems.append(f"schema {name}: 远端不存在")
            continue
        want_props = body.get("properties", {}) if isinstance(body, dict) else {}
        got_props = r.get("properties", {})
        want = set(want_props) if isinstance(want_props, dict) else set()
        got = set(got_props) if isinstance(got_props, dict) else set()
        if want - got:
            problems.append(f"schema {name}: 远端缺少字段 {sorted(want - got)}")
        if got - want:
            # OVERWRITE_EXISTING 语义下多出字段同样代表覆盖未如预期生效，
            # 见模块 docstring「背景」一节。
            problems.append(f"schema {name}: 远端多出字段 {sorted(got - want)}")
    return problems


def run(spec_path: str, remote_path: str) -> int:
    spec = load_json_loose(spec_path)
    remote = load_json_loose(remote_path)
    problems = verify(spec, remote)
    if not problems:
        print("✅ 回读校验通过：接口、folder、锚点、schema 字段均与本次推送一致")
        return 0
    print(f"❌ 回读校验发现 {len(problems)} 处不一致：")
    for p in problems:
        print(f"  - {p}")
    return 1


def self_test() -> int:
    spec = {
        "paths": {"/a": {"get": {
            "x-apifox-folder": "用户管理",
            "x-source-method-fq": "UserController#list",
        }}},
        "components": {"schemas": {"UserVO": {"properties": {"id": {}, "name": {}}}}},
    }
    # 1) 完全一致 → 无问题
    ok = {
        "paths": {"/a": {"get": {
            "x-apifox-folder": "用户管理",
            "x-source-method-fq": "UserController#list",
        }}},
        "components": {"schemas": {"UserVO": {"properties": {"id": {}, "name": {}}}}},
    }
    assert verify(spec, ok) == [], verify(spec, ok)

    # 2) 接口不存在
    assert any("远端不存在" in p for p in verify(spec, {"paths": {}, "components": {}}))

    # 3) folder 落错
    bad_folder = json.loads(json.dumps(ok))
    bad_folder["paths"]["/a"]["get"]["x-apifox-folder"] = "根目录"
    assert any("folder 不符" in p for p in verify(spec, bad_folder)), verify(spec, bad_folder)

    # 4) 锚点丢失
    bad_fq = json.loads(json.dumps(ok))
    del bad_fq["paths"]["/a"]["get"]["x-source-method-fq"]
    assert any("锚点不符" in p for p in verify(spec, bad_fq)), verify(spec, bad_fq)

    # 5) schema 字段缺失
    bad_schema = json.loads(json.dumps(ok))
    del bad_schema["components"]["schemas"]["UserVO"]["properties"]["name"]
    problems = verify(spec, bad_schema)
    assert any("缺少字段" in p and "name" in p for p in problems), problems

    # 6) schema 整体不存在
    no_schema = json.loads(json.dumps(ok))
    no_schema["components"]["schemas"] = {}
    assert any("schema UserVO" in p and "远端不存在" in p for p in verify(spec, no_schema))

    # 7) folder 缺省语义等价：spec 缺失该 key（未显式声明目录）与远端显式
    #    None，都代表根目录，是同一件事的两种写法，不应报「folder 不符」。
    #    没有归一化时，"" != None 会被误判为不一致。
    spec_root_folder = json.loads(json.dumps(spec))
    del spec_root_folder["paths"]["/a"]["get"]["x-apifox-folder"]
    remote_root_folder = json.loads(json.dumps(ok))
    remote_root_folder["paths"]["/a"]["get"]["x-apifox-folder"] = None
    assert verify(spec_root_folder, remote_root_folder) == [], \
        verify(spec_root_folder, remote_root_folder)

    # 8) 远端结构异常（paths/components 不是 dict）不应崩溃，应优雅判定为
    #    「远端不存在」而不是抛异常中断整个回读校验
    malformed_remote = {"paths": "not-a-dict", "components": "not-a-dict-either"}
    problems8 = verify(spec, malformed_remote)
    assert any("远端不存在" in p for p in problems8), problems8

    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 3:
        print("Usage: push_verify.py <spec_json> <remote_json> | -h | --self-test",
              file=sys.stderr)
        return 2
    _t0 = time.time()
    rc = run(argv[1], argv[2])
    summary = f"spec={argv[1]} remote={argv[2]}"
    if rc == 0:
        debug_log("push.push_verify", "success", int((time.time() - _t0) * 1000),
                  input_summary=summary)
    else:
        debug_log("push.push_verify", "error", int((time.time() - _t0) * 1000),
                  input_summary=summary, error_detail=f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
