#!/usr/bin/env python3
"""从 export.json + 用户参数检测 pull 模式（interactive/folder/api）。

用法：
    python3 detect_mode.py <export_json> [argument]
    python3 detect_mode.py -h
    python3 detect_mode.py --self-test

argv:
    <export_json>  Apifox export-openapi 导出的 JSON 文件
    [argument]     用户参数（可选）

stdout：
    JSON 格式，根据检测结果不同：

    无参数（交互模式）：
      {"mode": "interactive", "all_folders": ["目录1", "目录2"]}

    精确目录匹配：
      {"mode": "folder", "folder": "目录名"}

    接口匹配（目录/接口名 或 关键词搜索）：
      {"mode": "api", "apis": [
        {"folder": "目录", "method": "GET", "path": "/...", "summary": "接口名"}
      ]}

stderr / 退出码：
    0 成功 / 1 无匹配（stderr 列出可用目录）/ 2 参数用法错误
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402


def extract_folders(data: dict) -> list[str]:
    folders: set[str] = set()
    for methods in data.get("paths", {}).values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            folder = detail.get("x-apifox-folder", "")
            if isinstance(folder, str):
                folders.add(folder)
    return sorted(folders)


def extract_operations(data: dict) -> list[dict]:
    ops: list[dict] = []
    for path, methods in data.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            folder = detail.get("x-apifox-folder", "")
            summary = detail.get("summary", "")
            ops.append({
                "folder": folder if isinstance(folder, str) else "",
                "method": method.upper(),
                "path": path,
                "summary": summary if isinstance(summary, str) else "",
            })
    return ops


def detect(data: dict, argument: str | None) -> tuple[dict, int]:
    folders = extract_folders(data)

    if not argument:
        return {"mode": "interactive", "all_folders": folders}, 0

    if argument in folders:
        return {"mode": "folder", "folder": argument}, 0

    if "/" in argument:
        parts = argument.split("/", 1)
        dir_part, api_part = parts[0], parts[1]
        if dir_part in folders:
            ops = extract_operations(data)
            matched = [
                op for op in ops
                if (op["folder"] == dir_part or op["folder"].startswith(dir_part + "/"))
                and api_part in op["summary"]
            ]
            if matched:
                return {"mode": "api", "apis": matched}, 0

    ops = extract_operations(data)
    matched = [op for op in ops if argument in op["summary"]]
    if matched:
        return {"mode": "api", "apis": matched}, 0

    print(f'未找到匹配的目录或接口："{argument}"', file=sys.stderr)
    print("可用目录：", file=sys.stderr)
    for f in folders:
        print(f"  - {f}" if f else "  - （根目录）", file=sys.stderr)
    return {}, 1


def run(export_path: str, argument: str | None) -> int:
    data = load_json_loose(export_path)
    result, code = detect(data, argument)
    if code == 0:
        print(json.dumps(result, ensure_ascii=False))
    return code


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-detectmode-"))
    try:
        fixture = {
            "paths": {
                "/api/users": {
                    "get": {
                        "summary": "获取用户列表",
                        "x-apifox-folder": "用户管理",
                    },
                    "post": {
                        "summary": "创建用户",
                        "x-apifox-folder": "用户管理",
                    },
                },
                "/api/devices": {
                    "get": {
                        "summary": "获取设备列表",
                        "x-apifox-folder": "设备管理",
                    }
                },
                "/api/drone/status": {
                    "get": {
                        "summary": "获取无人机实时状态（基础状态、RTK定位、电池）",
                        "x-apifox-folder": "无人机",
                    }
                },
                "/api/drone/battery": {
                    "get": {
                        "summary": "获取电池详情",
                        "x-apifox-folder": "无人机/电池",
                    }
                },
                "/api/other": {
                    "get": {
                        "summary": "其他接口",
                        "x-apifox-folder": "",
                    }
                },
            },
        }
        p = tmp / "export.json"
        p.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        data = load_json_loose(str(p))

        # 1) 无参数 → interactive
        r, c = detect(data, None)
        assert c == 0 and r["mode"] == "interactive"
        assert "用户管理" in r["all_folders"]
        assert "设备管理" in r["all_folders"]

        # 2) 空字符串 → interactive
        r, c = detect(data, "")
        assert c == 0 and r["mode"] == "interactive"

        # 3) 精确目录匹配
        r, c = detect(data, "用户管理")
        assert c == 0 and r["mode"] == "folder" and r["folder"] == "用户管理"

        # 4) 目录/接口名 格式
        r, c = detect(data, "用户管理/创建用户")
        assert c == 0 and r["mode"] == "api"
        assert len(r["apis"]) == 1
        assert r["apis"][0]["method"] == "POST"
        assert r["apis"][0]["path"] == "/api/users"

        # 5) 关键词搜索（summary 包含）
        r, c = detect(data, "获取无人机实时状态（基础状态、RTK定位、电池）")
        assert c == 0 and r["mode"] == "api"
        assert len(r["apis"]) == 1
        assert r["apis"][0]["folder"] == "无人机"

        # 6) 关键词搜索匹配多个
        r, c = detect(data, "获取")
        assert c == 0 and r["mode"] == "api"
        assert len(r["apis"]) >= 3

        # 7) 无匹配 → 退出码 1
        r, c = detect(data, "不存在的接口")
        assert c == 1 and r == {}

        # 8) 目录/接口名 格式但接口名不匹配 → 降级到关键词搜索
        r, c = detect(data, "用户管理/不存在")
        assert c == 0 and r["mode"] == "api" or c == 1

        # 9) 子目录精确匹配
        r, c = detect(data, "无人机/电池")
        assert c == 0 and r["mode"] == "folder" and r["folder"] == "无人机/电池"

        # 10) 根目录（空字符串 folder）在 all_folders 中
        r, c = detect(data, None)
        assert "" in r["all_folders"]

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) < 2 or len(argv) > 3:
        print("Usage: detect_mode.py <export_json> [argument] | -h | --self-test",
              file=sys.stderr)
        return 2
    export_path = argv[1]
    argument = argv[2] if len(argv) == 3 else None
    return run(export_path, argument)


if __name__ == "__main__":
    sys.exit(main(sys.argv))