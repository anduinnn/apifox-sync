#!/usr/bin/env python3
"""从 Apifox export.json 中提取所有 `x-apifox-folder` 文件夹路径，去重并按字典序输出。

用法：
    python3 list_folders.py <export_json>
    python3 list_folders.py -h
    python3 list_folders.py --self-test

argv:
    <export_json>  Apifox export-openapi 返回并写入的 JSON 文件路径

stdout：
    每行一个 folder 路径（按字典序）；空 folder（根目录）输出为空行。

stderr / 退出码：
    0 成功 / 1 运行时错误 / 2 参数用法错误

抽出目的：pull.md 步骤 2 / push-api.md 步骤 8 原本要求 Claude 对话层自读
`export.json` 去枚举 folder（因文件常超过 Read 上限），对话层若自写 inline
`python3 -c` 或 heredoc 会错过 Apifox 非法 `\\` 转义的容错，导致整个流程在
列 folder 处就崩溃（`Invalid \\escape`）。本脚本内置 `json_safe.load_json_loose`
容错，输出固定 argv 形态，避免每次授权白名单。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402


def extract_folders(data: dict) -> list[str]:
    """遍历 `paths[*][*].x-apifox-folder`，去重后字典序排序。"""
    folders: set[str] = set()
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return []
    for methods in paths.values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            folder = detail.get("x-apifox-folder", "")
            if isinstance(folder, str):
                folders.add(folder)
    return sorted(folders)


def run(export_path: str) -> int:
    data = load_json_loose(export_path)
    for folder in extract_folders(data):
        print(folder)
    return 0


def _self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-listfolders-"))
    try:
        # 1) 正常：按字典序输出去重
        p = tmp / "export.json"
        p.write_text(
            json.dumps(
                {
                    "paths": {
                        "/a": {"get": {"x-apifox-folder": "用户管理"}},
                        "/b": {
                            "post": {"x-apifox-folder": "设备管理/基础"},
                            "put": {"x-apifox-folder": "用户管理"},
                        },
                        "/c": {"get": {"x-apifox-folder": "订单管理"}},
                        "/d": {"get": {"x-apifox-folder": ""}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data = load_json_loose(str(p))
        folders = extract_folders(data)
        # Python 默认按 Unicode code point 排序：用(U+7528) < 订(U+8BA2) < 设(U+8BBE)
        assert folders == ["", "用户管理", "订单管理", "设备管理/基础"], folders

        # 2) 含非法 `\` 转义的 export → 容错后仍能列出
        bad = tmp / "bad.json"
        bad.write_text(
            '{"paths": {"/x": {"get": {"x-apifox-folder": "路径\\测试", '
            '"description": "regex \\d+"}}}}',
            encoding="utf-8",
        )
        data2 = load_json_loose(str(bad))
        folders2 = extract_folders(data2)
        assert folders2 == ["路径\\测试"], folders2

        # 3) paths 缺失 / 非 dict 不崩溃
        p3 = tmp / "empty.json"
        p3.write_text("{}", encoding="utf-8")
        assert extract_folders(load_json_loose(str(p3))) == []
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
        return _self_test()
    if len(argv) != 2:
        print("Usage: list_folders.py <export_json> | -h | --self-test", file=sys.stderr)
        return 2
    return run(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
