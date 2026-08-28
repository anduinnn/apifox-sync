#!/usr/bin/env python3
"""从 Apifox export.json 中反查某 Controller 已有接口所在的 folder，作为推送目录推荐。

用法：
    python3 suggest_folder.py <export_json> <controller_fq>
    python3 suggest_folder.py -h
    python3 suggest_folder.py --self-test

argv:
    <export_json>     Apifox export-openapi 返回并写入的 JSON 文件路径
    <controller_fq>   Controller 全限定类名（对应 `x-source-controller`）

stdout：
    该 Controller 已有接口所在的 folder，按接口数降序、同数按名称字典序，每行一个；
    无匹配则无输出。

stderr / 退出码：
    0 成功（含无匹配） / 1 运行时错误 / 2 参数用法错误

抽出目的：push-api.md 步骤 8 原本推目录选择完全靠用户手选，同一 Controller
历史上已推送过的接口所在 folder 其实可从 export.json 的 `x-source-controller`
锚点反查得到，作为 `AskUserQuestion` 的预填推荐，减少选错目录的概率。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402
from debug_log import debug_log  # noqa: E402


def suggest(export: dict, controller_fq: str) -> list[str]:
    """返回该 Controller 已有接口所在的 folder，按接口数降序、同数按名称字典序。"""
    counts: dict[str, int] = {}
    paths = export.get("paths", {})
    if not isinstance(paths, dict):
        return []
    for methods in paths.values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            if detail.get("x-source-controller", "") == controller_fq:
                folder = detail.get("x-apifox-folder", "")
                if isinstance(folder, str):
                    counts[folder] = counts.get(folder, 0) + 1
    return [f for f, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def self_test() -> int:
    fixture = {
        "paths": {
            "/a": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "用户管理"}},
            "/b": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "用户管理"}},
            "/c": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "旧模块"}},
            "/d": {"get": {"x-source-controller": "com.x.OtherController",
                           "x-apifox-folder": "其他"}},
            "/e": {"get": {"x-apifox-folder": "无锚点"}},
        }
    }
    # 1) 按接口数降序：用户管理(2) 在 旧模块(1) 前
    assert suggest(fixture, "com.x.UserController") == ["用户管理", "旧模块"]
    # 2) 只命中一个
    assert suggest(fixture, "com.x.OtherController") == ["其他"]
    # 3) 无匹配 → 空列表
    assert suggest(fixture, "com.x.GhostController") == []
    # 4) 同数按名称字典序（确定性，不依赖插入序）
    # Python 默认按 Unicode code point 排序：乙(U+4E59) < 甲(U+7532)，
    # 与 list_folders.py 自测采用的排序基准一致（非拼音/笔画序）。
    tie = {"paths": {
        "/x": {"get": {"x-source-controller": "C", "x-apifox-folder": "乙"}},
        "/y": {"get": {"x-source-controller": "C", "x-apifox-folder": "甲"}},
    }}
    assert suggest(tie, "C") == ["乙", "甲"], suggest(tie, "C")
    # 5) paths 缺失不崩溃
    assert suggest({}, "C") == []
    # 6) x-apifox-folder 非字符串（如 null）不应导致 TypeError，直接跳过该条
    bad_type = {"paths": {
        "/m": {"get": {"x-source-controller": "C", "x-apifox-folder": "正常"}},
        "/n": {"get": {"x-source-controller": "C", "x-apifox-folder": None}},
    }}
    assert suggest(bad_type, "C") == ["正常"], suggest(bad_type, "C")
    # 7) 真实文件加载路径：含非法 `\` 转义 + 非字符串 folder，走 load_json_loose + suggest
    # 不应崩溃，且非字符串项被跳过（对齐 list_folders.py 的 tempfile 真实文件用例）
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-suggestfolder-"))
    try:
        p = tmp / "export.json"
        p.write_text(
            '{"paths": {'
            '"/a": {"get": {"x-source-controller": "C", "x-apifox-folder": "路径\\测试"}}, '
            '"/b": {"get": {"x-source-controller": "C", "x-apifox-folder": null}}'
            '}}',
            encoding="utf-8",
        )
        data = load_json_loose(str(p))
        result = suggest(data, "C")
        assert result == ["路径\\测试"], result
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST_OK")
    return 0


def run(export_path: str, controller_fq: str) -> int:
    data = load_json_loose(export_path)
    for folder in suggest(data, controller_fq):
        print(folder)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 3:
        print("Usage: suggest_folder.py <export_json> <controller_fq> | -h | --self-test", file=sys.stderr)
        return 2
    _t0 = time.time()
    rc = run(argv[1], argv[2])
    if rc == 0:
        debug_log("push.suggest_folder", "success", int((time.time() - _t0) * 1000),
                  input_summary=f"export={argv[1]} controller={argv[2]}")
    else:
        debug_log("push.suggest_folder", "error", int((time.time() - _t0) * 1000),
                  error_detail=f"exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
