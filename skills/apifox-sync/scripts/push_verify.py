#!/usr/bin/env python3
"""比对推送后回读的远端数据与本次**实际推送**的内容，校验 folder、锚点、schema 字段一致性。

用法：
    python3 push_verify.py <remote_json>
    python3 push_verify.py -h
    python3 push_verify.py --self-test

argv:
    <remote_json>  推送完成后重新 export-openapi 得到的回读文件
                   （通常是 ${TMPPREFIX}verify.json）

env:
    TMPPREFIX  用于定位本次实际提交的 payload-update.json / payload-create.json
               （由 push_classify.py 按需写入，步骤 12 才清理，本步骤仍在）

stdout 摘要:
    全部通过：`✅ 回读校验通过：接口、folder、锚点、schema 字段均与本次推送一致`
    有不一致：`❌ 回读校验发现 N 处不一致：` 后逐条列出问题描述
    本次无实际推送：`⚠️ 本次无接口实际提交（全部为跳过冲突或无变更），跳过回读校验`

背景：`import-openapi` 返回的 counters（如 endpointCreated）只说明请求被
Apifox 接受，不能证明 folder 落对了、schema 字段落全了、`x-source-method-fq`
锚点写进去了——这些都要重新导出一次远端实际状态才能验证。push 用
`schemaOverwriteBehavior: OVERWRITE_EXISTING`，据 Apifox 官方文档该行为是
「新导入的内容会完全替换掉旧的内容」而非合并，因此回读的 schema properties
理论上应与本次 spec 完全一致：多出的字段和缺失的字段一样，都指向覆盖没有
如预期生效（例如命中了别的 Controller 的同名 schema、或 Apifox 端有缓存/
异步落库延迟），因此本模块把两者都判定为不一致。

**校验基准 = 本次实际推送，而非本次生成的完整 spec**：`push_classify.py`
对「目标 folder 与其他 folder 同时存在同 path+method」的接口走 skip 分支，
既不进 update 批次也不进 create 批次，从未提交给 Apifox；这些接口远端仍是
旧状态、大概率没有 `x-source-method-fq` 锚点。若仍以完整 spec 为基准比对，
skip 接口必然报「锚点不符」/「folder 不符」，与「跳过(冲突)」重复告知同一
批接口、且是确定性假警报。因此改为读取 `payload-update.json` /
`payload-create.json`（两者的 `input` 字段是本批次实际提交的 spec 子集，
`components` 是完整本地 spec 的深拷贝，参见 push_classify.py::build_spec，
两批次的 components 内容相同）合并出「本次实际推送的 spec」作为比对基准；
两个文件都不存在时代表本次全部接口被跳过、未发生任何实际推送，此时优雅
退出（不报错、也不误判为「校验通过」），不比对任何内容。

退出码：
    0 全部一致，或本次无实际推送
    1 存在不一致，或运行时错误
    2 参数用法错误
"""
from __future__ import annotations

import json
import os
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


def load_pushed_spec(tmpprefix: str) -> dict | None:
    """从本次实际提交的 payload 文件重建「已推送 spec」，用作回读校验基准。

    payload-update.json / payload-create.json 由 push_classify.py 按需写入
    （skip 的接口两者都不进，见模块 docstring）。两者的 "input" 字段是 JSON
    字符串形式的 spec，paths 为本批次子集，components 是完整本地 spec 的
    深拷贝（build_spec 对同一个 base spec 深拷贝，两批次的 components 内容
    相同，取任一份即可）。逐 path 按 method 合并两批次的 paths 即为本次实际
    推送的接口全集——**不可用 dict.update() 做顶层合并**：同一个 path 的不同
    method 可能被拆到两个批次（如「已有 GET 走 update，新增 DELETE 走
    create」），顶层 update() 会让后处理批次的同 path 条目整体覆盖前者，
    导致先写入的方法从合并结果中消失。

    两个文件都不存在（本次全部接口被跳过，未发生任何实际推送）时返回 None，
    调用方应据此优雅退出，不得当作「无问题」直接判定校验通过。这两个文件是
    本工具自己用 json.dump 写出的，非 Apifox 导出，故用 json.loads 而非
    load_json_loose 读取。
    """
    paths: dict = {}
    components: dict = {}
    found = False
    for name in ("payload-update.json", "payload-create.json"):
        p = Path(f"{tmpprefix}{name}")
        if not p.is_file():
            continue
        found = True
        payload = json.loads(p.read_text(encoding="utf-8"))
        batch_spec = json.loads(payload["input"])
        batch_paths = batch_spec.get("paths", {})
        if isinstance(batch_paths, dict):
            # 逐 path 做方法级合并，不可用 paths.update(batch_paths)：后者是
            # 顶层浅覆盖，若同一个 path 的不同 method 被拆到 update/create
            # 两个批次（如「已有 GET 走 update，新增 DELETE 走 create」），
            # 后处理的批次会把前一批次写入的整个 path 条目替换掉，导致先写
            # 入的方法从校验范围静默消失。
            for path_key, methods in batch_paths.items():
                if isinstance(methods, dict):
                    paths.setdefault(path_key, {}).update(methods)
                else:
                    paths[path_key] = methods
        batch_components = batch_spec.get("components", {})
        if isinstance(batch_components, dict):
            components = batch_components
    if not found:
        return None
    return {"paths": paths, "components": components}


def run(remote_path: str, tmpprefix: str) -> int:
    pushed = load_pushed_spec(tmpprefix)
    if pushed is None:
        print("⚠️ 本次无接口实际提交（全部为跳过冲突或无变更），跳过回读校验")
        return 0
    remote = load_json_loose(remote_path)
    problems = verify(pushed, remote)
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

    # 9) I2：校验基准必须是本次实际推送（payload-update/create.json 合并出的
    #    路径集合），而非本次生成的完整 spec——跳过(冲突)的接口从未进入任何
    #    payload，不应被回读校验误判为不一致。
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-pushverify-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        pushed_schemas = {"UserVO": {"properties": {"id": {}, "name": {}}}}
        update_batch_spec = {
            "paths": {"/a": {"get": {
                "x-apifox-folder": "用户管理",
                "x-source-method-fq": "UserController#list",
            }}},
            "components": {"schemas": pushed_schemas},
        }
        create_batch_spec = {
            "paths": {"/b": {"post": {
                "x-apifox-folder": "用户管理",
                "x-source-method-fq": "UserController#create",
            }}},
            "components": {"schemas": pushed_schemas},
        }
        Path(f"{prefix}payload-update.json").write_text(
            json.dumps({"input": json.dumps(update_batch_spec, ensure_ascii=False),
                        "options": {}}, ensure_ascii=False), encoding="utf-8")
        Path(f"{prefix}payload-create.json").write_text(
            json.dumps({"input": json.dumps(create_batch_spec, ensure_ascii=False),
                        "options": {}}, ensure_ascii=False), encoding="utf-8")

        pushed = load_pushed_spec(prefix)
        assert set(pushed["paths"]) == {"/a", "/b"}, pushed["paths"]
        assert pushed["components"]["schemas"] == pushed_schemas

        # 故意在同目录写一份包含 /skipped 的 spec.json——新实现必须完全不读
        # 它，只认 payload-update/create.json，避免有人"顺手"把完整 spec
        # 加回校验基准。/skipped 模拟被跳过(冲突)的旧接口：折叠 folder 与
        # 完整本地 spec 期望的不一致（因为它从未被推送，远端还是旧状态）。
        full_local_spec = {
            "paths": {
                "/a": update_batch_spec["paths"]["/a"],
                "/b": create_batch_spec["paths"]["/b"],
                "/skipped": {"get": {"x-apifox-folder": "旧目录",
                                      "x-source-method-fq": "UserController#skipped"}},
            },
            "components": {"schemas": pushed_schemas},
        }
        Path(f"{prefix}spec.json").write_text(
            json.dumps(full_local_spec, ensure_ascii=False), encoding="utf-8")
        pushed_again = load_pushed_spec(prefix)
        assert set(pushed_again["paths"]) == {"/a", "/b"}, pushed_again["paths"]

        # 远端：/a、/b 均一致；/skipped 是被跳过的旧接口，折叠在错误目录——
        # 若校验基准仍是完整 spec（含 /skipped）就会因它报错；用 payload
        # 合并出的基准则不应触及它
        remote = {
            "paths": {
                "/a": {"get": {"x-apifox-folder": "用户管理",
                               "x-source-method-fq": "UserController#list"}},
                "/b": {"post": {"x-apifox-folder": "用户管理",
                                "x-source-method-fq": "UserController#create"}},
                "/skipped": {"get": {"x-apifox-folder": "错误目录"}},
            },
            "components": {"schemas": pushed_schemas},
        }
        assert verify(pushed_again, remote) == [], verify(pushed_again, remote)
        # 反证：若仍以完整 spec 为基准，/skipped 的 folder 不符必然被捕获——
        # 证明本用例确实会区分「正确基准」与「错误基准」，不是弱断言
        assert verify(full_local_spec, remote) != []

        remote_path = tmp / "remote.json"
        remote_path.write_text(json.dumps(remote, ensure_ascii=False), encoding="utf-8")
        rc = run(str(remote_path), prefix)
        assert rc == 0

        # 两个 payload 文件都不存在（本次全部跳过，未发生任何实际推送）→
        # 优雅退出（rc=0），不得当作「校验通过」或「校验失败」误报
        empty_prefix = str(tmp) + "/empty-"
        assert load_pushed_spec(empty_prefix) is None
        rc_empty = run(str(remote_path), empty_prefix)
        assert rc_empty == 0

        # 10) I2 回归：同一个 path 的不同 method 被拆到 update/create 两个
        #    批次时（如「已有 GET，新增 DELETE」——GET 锚点命中走 update，
        #    DELETE 锚点未命中走 create），合并必须是逐 path 的方法级合并，
        #    不能是 dict.update() 式的整个 path 顶层覆盖，否则先写入的方法
        #    会被后写入批次的同 path 条目整体替换掉、静默从校验范围消失。
        multi_prefix = str(tmp) + "/multi-"
        multi_schemas = {"UserVO": {"type": "object"}}
        update_multi_spec = {
            "paths": {"/api/users/{id}": {"get": {
                "x-apifox-folder": "用户管理",
                "x-source-method-fq": "UserController#get",
            }}},
            "components": {"schemas": multi_schemas},
        }
        create_multi_spec = {
            "paths": {"/api/users/{id}": {"delete": {
                "x-apifox-folder": "用户管理",
                "x-source-method-fq": "UserController#delete",
            }}},
            "components": {"schemas": multi_schemas},
        }
        Path(f"{multi_prefix}payload-update.json").write_text(
            json.dumps({"input": json.dumps(update_multi_spec, ensure_ascii=False),
                        "options": {}}, ensure_ascii=False), encoding="utf-8")
        Path(f"{multi_prefix}payload-create.json").write_text(
            json.dumps({"input": json.dumps(create_multi_spec, ensure_ascii=False),
                        "options": {}}, ensure_ascii=False), encoding="utf-8")
        pushed_multi = load_pushed_spec(multi_prefix)
        methods = set(pushed_multi["paths"].get("/api/users/{id}", {}))
        assert methods == {"get", "delete"}, (
            "同 path 跨批次合并丢失方法（dict.update 整体覆盖 bug）: " + str(methods)
        )
    finally:
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
        print("Usage: push_verify.py <remote_json> | -h | --self-test",
              file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    _t0 = time.time()
    rc = run(argv[1], tmpprefix)
    summary = f"remote={argv[1]}"
    if rc == 0:
        debug_log("push.push_verify", "success", int((time.time() - _t0) * 1000),
                  input_summary=summary)
    else:
        debug_log("push.push_verify", "error", int((time.time() - _t0) * 1000),
                  input_summary=summary, error_detail=f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
