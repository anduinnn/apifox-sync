#!/usr/bin/env python3
"""对远端临时文件 ${TMPPREFIX}pull-*.json 与本地 .claude/apis/ 做 diff 预览。

用法：
    python3 pull_diff.py <project_root>
    python3 pull_diff.py -h
    python3 pull_diff.py --self-test

argv:
    <project_root>  项目根目录绝对路径（用于定位 .claude/apis/）

env:
    TMPPREFIX  临时文件前缀（必填）

输入：
    glob ${TMPPREFIX}pull-*.json（排除 pull-diff.json / pull-approved.json）
    本地 .claude/apis/<actual_folder>.json

输出：
    ${TMPPREFIX}pull-diff.json  详细 diff 汇总
    stdout 人类可读摘要（NEW / SAME / DIFF 三种状态 + 接口级 +/~/-）

等价替换 pull.md 步骤 5.5 内嵌 python3 逻辑。decode 调 path_codec.decode，
还原 folder 名时与 pull_extract.encode 对称。

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_codec  # noqa: E402


def flatten_ops(data: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path, methods in data.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            out[f"{method.upper()} {path}"] = detail
    return out


def folder_from_tmp(tmp_path: str, tmpprefix: str) -> str:
    """${TMPPREFIX}pull-<encoded>.json → decoded folder。"""
    basename = os.path.basename(tmp_path)
    prefix_base = os.path.basename(tmpprefix)
    encoded = basename
    if encoded.startswith(prefix_base):
        encoded = encoded[len(prefix_base):]
    encoded = encoded.removeprefix("pull-").removesuffix(".json")
    return path_codec.decode(encoded)


def local_file_for(actual_folder: str, apis_dir: str) -> str:
    parts = actual_folder.rsplit("/", 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + ".json")
    return os.path.join(apis_dir, actual_folder + ".json")


def run(project_root: str, tmpprefix: str) -> int:
    apis_dir = os.path.join(project_root, ".claude", "apis")
    summary: dict[str, dict] = {}

    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-*.json")):
        # 排除自身产物
        if tmp_path.endswith("pull-diff.json") or tmp_path.endswith("pull-approved.json"):
            continue
        actual_folder = folder_from_tmp(tmp_path, tmpprefix)
        try:
            new_data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ERROR: 无法解析临时文件 {tmp_path}: {e}", file=sys.stderr)
            return 1
        new_ops = flatten_ops(new_data)

        local_path = local_file_for(actual_folder, apis_dir)
        if not os.path.exists(local_path):
            summary[actual_folder] = {
                "status": "new-file",
                "new": sorted(new_ops.keys()),
                "updated": [],
                "removed": [],
                "unchanged": [],
            }
            continue

        try:
            old_data = json.loads(Path(local_path).read_text(encoding="utf-8"))
        except Exception as e:
            summary[actual_folder] = {
                "status": "new-file",
                "new": sorted(new_ops.keys()),
                "updated": [],
                "removed": [],
                "unchanged": [],
                "_warn": f"本地文件解析失败，按全新文件处理: {e}",
            }
            continue

        old_ops = flatten_ops(old_data)
        added = sorted(new_ops.keys() - old_ops.keys())
        removed = sorted(old_ops.keys() - new_ops.keys())
        changed = sorted(
            [k for k in new_ops.keys() & old_ops.keys() if new_ops[k] != old_ops[k]]
        )
        same = sorted(
            [k for k in new_ops.keys() & old_ops.keys() if new_ops[k] == old_ops[k]]
        )
        if not (added or removed or changed):
            summary[actual_folder] = {
                "status": "nochange",
                "new": [],
                "updated": [],
                "removed": [],
                "unchanged": same,
            }
        else:
            summary[actual_folder] = {
                "status": "diff",
                "new": added,
                "updated": changed,
                "removed": removed,
                "unchanged": same,
            }

    with open(f"{tmpprefix}pull-diff.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    for folder in sorted(summary.keys()):
        info = summary[folder]
        local = os.path.relpath(local_file_for(folder, apis_dir), project_root)
        if info["status"] == "new-file":
            print(f"\n[NEW  ] {folder}  → {local}（本地不存在，将新建）")
            for k in info["new"]:
                print(f"    + {k}")
            if info.get("_warn"):
                print(f'    ⚠️  {info["_warn"]}')
        elif info["status"] == "nochange":
            print(f'\n[ SAME] {folder}  → {local}（无变化，{len(info["unchanged"])} 个接口）')
        else:
            print(f"\n[DIFF ] {folder}  → {local}")
            for k in info["new"]:
                print(f"    + {k}")
            for k in info["updated"]:
                print(f"    ~ {k}")
            for k in info["removed"]:
                print(f"    - {k}  （远程已删除，本地将被清理）")
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        apis = project_root / ".claude" / "apis"
        apis.mkdir(parents=True)

        def write_tmp(folder: str, content: dict) -> None:
            enc = path_codec.encode(folder)
            Path(f"{prefix}pull-{enc}.json").write_text(
                json.dumps(content, ensure_ascii=False), encoding="utf-8"
            )

        # 场景 1：new-file（本地不存在）
        write_tmp("新目录", {"paths": {"/a": {"get": {"summary": "a"}}}})
        # 场景 2：nochange（本地与远程相同）
        same_content = {"paths": {"/b": {"get": {"summary": "b"}}}}
        write_tmp("同步目录", same_content)
        (apis / "同步目录.json").write_text(
            json.dumps(same_content, ensure_ascii=False), encoding="utf-8"
        )
        # 场景 3：diff（有 new/updated/removed）
        write_tmp(
            "变化目录",
            {
                "paths": {
                    "/c_new": {"get": {"summary": "new"}},
                    "/c_changed": {"get": {"summary": "v2"}},
                }
            },
        )
        (apis / "变化目录.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/c_changed": {"get": {"summary": "v1"}},
                        "/c_removed": {"post": {"summary": "gone"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 场景 4：本地 json 解析失败
        write_tmp("损坏目录", {"paths": {"/d": {"get": {"summary": "d"}}}})
        (apis / "损坏目录.json").write_text("{{invalid json", encoding="utf-8")
        # 场景 5：含 __ 的历史目录名
        write_tmp("历史__组", {"paths": {"/legacy": {"get": {"summary": "L"}}}})

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0
        summary = json.loads(Path(f"{prefix}pull-diff.json").read_text(encoding="utf-8"))
        assert summary["新目录"]["status"] == "new-file"
        assert summary["同步目录"]["status"] == "nochange"
        assert summary["变化目录"]["status"] == "diff"
        assert "GET /c_new" in summary["变化目录"]["new"]
        assert "GET /c_changed" in summary["变化目录"]["updated"]
        assert "POST /c_removed" in summary["变化目录"]["removed"]
        assert summary["损坏目录"]["status"] == "new-file"
        assert "历史__组" in summary  # decode 还原
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
        print("Usage: pull_diff.py <project_root> | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(argv[1], tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
