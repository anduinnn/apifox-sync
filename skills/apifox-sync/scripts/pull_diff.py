#!/usr/bin/env python3
"""对远端临时 op 切片 ${TMPPREFIX}pull-op-*.json 与本地 .claude/apis/ 做 diff 预览。

用法：
    python3 pull_diff.py <project_root>
    python3 pull_diff.py -h
    python3 pull_diff.py --self-test

argv:
    <project_root>  项目根目录绝对路径（用于定位 .claude/apis/）

env:
    TMPPREFIX  临时文件前缀（必填）

输入：
    glob ${TMPPREFIX}pull-op-*.json          远程单接口切片
    本地新布局：.claude/apis/<folder>/<op-relpath>
    本地旧布局：.claude/apis/<folder>.json   （folder 级聚合，v1.2.x 产物）

输出：
    ${TMPPREFIX}pull-diff.json  按 folder 聚合的详细 diff
    stdout 人类可读摘要（NEW / SAME / DIFF 三种状态 + 接口级 +/~/- + migrate 提示）

规则：
  - 对每个远程 op：
      1) 若本地新布局接口文件存在 → 读 operation 做对比
      2) 否则若旧 folder 聚合文件存在 → 从聚合文件中查该 METHOD+path → 做对比且标记需迁移
      3) 否则 → new
  - 对于本地存在但远程不返的 operation（在已选 folder 范围内）→ removed
  - 按 folder 聚合为 {status, new, updated, removed, unchanged, legacy_file}

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_path  # noqa: E402


# -------- 读远端 op 切片 --------

def read_remote_op(tmp_path: str) -> Optional[dict]:
    """解析单接口切片 → {folder, method, path, operation, raw}。"""
    try:
        data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: 无法解析临时文件 {tmp_path}: {e}", file=sys.stderr)
        return None
    paths = data.get("paths", {})
    if not isinstance(paths, dict) or len(paths) != 1:
        print(f"ERROR: op 切片 paths 结构不合法: {tmp_path}", file=sys.stderr)
        return None
    path = next(iter(paths))
    methods = paths[path]
    if not isinstance(methods, dict) or len(methods) != 1:
        print(f"ERROR: op 切片 methods 结构不合法: {tmp_path}", file=sys.stderr)
        return None
    method = next(iter(methods))
    operation = methods[method]
    folder = operation.get("x-apifox-folder", "") if isinstance(operation, dict) else ""
    return {
        "folder": folder,
        "method": method.upper(),
        "path": path,
        "operation": operation,
        "raw": data,
    }


# -------- 读本地布局 --------

def new_layout_path(project_root: str, folder: str, method: str, path: str) -> str:
    """新布局：.claude/apis/<folder>/<op-relpath>"""
    rel = api_path.op_relpath(method, path)
    return os.path.join(project_root, ".claude", "apis", folder, rel)


def old_layout_path(project_root: str, folder: str) -> str:
    """旧布局：folder 含 `/` → A/B.json；无 `/` → A.json（对齐 v1.2.x pull_save）"""
    apis_dir = os.path.join(project_root, ".claude", "apis")
    parts = folder.rsplit("/", 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + ".json")
    return os.path.join(apis_dir, folder + ".json")


def read_local_new(project_root: str, folder: str, method: str, path: str) -> Optional[dict]:
    """从新布局单接口文件读 operation；不存在返 None。"""
    p = new_layout_path(project_root, folder, method, path)
    if not os.path.exists(p):
        return None
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None
    paths = data.get("paths", {})
    methods = paths.get(path, {})
    if isinstance(methods, dict):
        op = methods.get(method.lower()) or methods.get(method.upper())
        if isinstance(op, dict):
            return op
    return None


def read_local_old(project_root: str, folder: str) -> Optional[dict]:
    """旧 folder 聚合文件 → dict 或 None；返回完整 data，供多接口查询。"""
    p = old_layout_path(project_root, folder)
    if not os.path.exists(p):
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def find_in_old(old_data: dict, method: str, path: str) -> Optional[dict]:
    paths = old_data.get("paths", {}) if isinstance(old_data, dict) else {}
    methods = paths.get(path, {})
    if isinstance(methods, dict):
        op = methods.get(method.lower()) or methods.get(method.upper())
        if isinstance(op, dict):
            return op
    return None


def scan_new_layout_folder(project_root: str, folder: str) -> dict[str, dict]:
    """扫描 .claude/apis/<folder>/ 下所有 op 文件 → {"METHOD path": operation}。"""
    folder_dir = os.path.join(project_root, ".claude", "apis", folder)
    out: dict[str, dict] = {}
    if not os.path.isdir(folder_dir):
        return out
    for root, _dirs, files in os.walk(folder_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(root, fn)
            rel_from_folder = os.path.relpath(full, folder_dir).replace(os.sep, "/")
            if not api_path.is_op_file(rel_from_folder):
                continue
            try:
                method, path = api_path.split_op_relpath(rel_from_folder)
            except ValueError:
                continue
            try:
                data = json.loads(Path(full).read_text(encoding="utf-8"))
            except Exception:
                continue
            paths_block = data.get("paths", {})
            methods = paths_block.get(path, {})
            if not isinstance(methods, dict):
                continue
            op = methods.get(method.lower()) or methods.get(method.upper())
            if isinstance(op, dict):
                out[f"{method} {path}"] = op
    return out


# -------- 核心 diff --------

def run(project_root: str, tmpprefix: str) -> int:
    # 分组远端 op（按 folder）
    remote_by_folder: dict[str, dict[str, dict]] = {}  # folder → {"METHOD path": op}
    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-op-*.json")):
        entry = read_remote_op(tmp_path)
        if entry is None:
            return 1
        key = f"{entry['method']} {entry['path']}"
        remote_by_folder.setdefault(entry["folder"], {})[key] = entry["operation"]

    summary: dict[str, dict] = {}

    for folder, remote_ops in remote_by_folder.items():
        old_data = read_local_old(project_root, folder)
        local_new_ops = scan_new_layout_folder(project_root, folder)
        has_old = old_data is not None
        has_new_layout = len(local_new_ops) > 0

        # 合并本地视角：优先新布局；若新布局没有但旧聚合里有，也算本地存在
        local_view: dict[str, dict] = dict(local_new_ops)
        if has_old:
            old_paths = old_data.get("paths", {}) if isinstance(old_data, dict) else {}
            for p, methods in old_paths.items():
                if not isinstance(methods, dict):
                    continue
                for m, op in methods.items():
                    if not isinstance(op, dict):
                        continue
                    k = f"{m.upper()} {p}"
                    # 若新布局里没有，才从旧文件补
                    if k not in local_view:
                        local_view[k] = op

        added = sorted(remote_ops.keys() - local_view.keys())
        removed = sorted(local_view.keys() - remote_ops.keys())
        common = remote_ops.keys() & local_view.keys()
        updated = sorted([k for k in common if remote_ops[k] != local_view[k]])
        unchanged = sorted([k for k in common if remote_ops[k] == local_view[k]])

        if not has_old and not has_new_layout:
            status = "new-file"
        elif not (added or removed or updated):
            status = "nochange"
        else:
            status = "diff"

        summary[folder] = {
            "status": status,
            "new": added,
            "updated": updated,
            "removed": removed,
            "unchanged": unchanged,
            "legacy_file": has_old,
        }

    with open(f"{tmpprefix}pull-diff.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # stdout 人类可读摘要
    for folder in sorted(summary.keys()):
        info = summary[folder]
        target_dir = os.path.relpath(
            os.path.join(project_root, ".claude", "apis", folder), project_root
        )
        legacy_note = "（将迁移旧文件并拆分为单接口文件）" if info["legacy_file"] else ""
        if info["status"] == "new-file":
            print(f'\n[NEW  ] {folder}  → {target_dir}/（本地不存在，将新建 {len(info["new"])} 个接口文件）')
            for k in info["new"]:
                print(f"    + {k}")
        elif info["status"] == "nochange":
            print(
                f'\n[ SAME] {folder}  → {target_dir}/（无变化，{len(info["unchanged"])} 个接口）'
                f'{legacy_note}'
            )
        else:
            print(f"\n[DIFF ] {folder}  → {target_dir}/{legacy_note}")
            for k in info["new"]:
                print(f"    + {k}")
            for k in info["updated"]:
                print(f"    ~ {k}")
            for k in info["removed"]:
                print(f"    - {k}  （远程已删除，本地将被清理）")
    return 0


# -------- self-test --------

def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        apis = project_root / ".claude" / "apis"
        apis.mkdir(parents=True)

        def write_remote_op(folder: str, method: str, path: str, op: dict) -> None:
            op = dict(op)
            op["x-apifox-folder"] = folder
            data = {
                "paths": {path: {method.lower(): op}},
                "components": {"schemas": {}},
            }
            key = api_path.hash_key(folder, method, path)
            Path(f"{prefix}pull-op-{key}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

        # 场景 A: new-file（本地完全无）
        write_remote_op("新目录", "GET", "/a", {"summary": "a"})

        # 场景 B: 新布局已存在 + 同步
        write_remote_op("新布局同步", "GET", "/b", {"summary": "b", "x-apifox-folder": "新布局同步"})
        b_file = apis / "新布局同步" / "b.GET.json"
        b_file.parent.mkdir(parents=True)
        b_file.write_text(
            json.dumps(
                {"paths": {"/b": {"get": {"summary": "b", "x-apifox-folder": "新布局同步"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 场景 C: 旧布局需要迁移 + diff
        write_remote_op("旧迁移", "GET", "/c_new", {"summary": "new"})
        write_remote_op("旧迁移", "GET", "/c_changed", {"summary": "v2"})
        # 本地旧聚合文件：有 c_changed（不同）+ c_removed（远程已删）
        (apis / "旧迁移.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/c_changed": {"get": {"summary": "v1", "x-apifox-folder": "旧迁移"}},
                        "/c_removed": {"post": {"summary": "gone", "x-apifox-folder": "旧迁移"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 场景 D: 新布局有多余接口（本地 d_removed 远程不返）
        write_remote_op("清理目录", "GET", "/d_keep", {"summary": "keep"})
        d_keep = apis / "清理目录" / "d_keep.GET.json"
        d_keep.parent.mkdir(parents=True)
        d_keep.write_text(
            json.dumps(
                {"paths": {"/d_keep": {"get": {"summary": "keep", "x-apifox-folder": "清理目录"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        d_stale = apis / "清理目录" / "d_stale.POST.json"
        d_stale.write_text(
            json.dumps(
                {"paths": {"/d_stale": {"post": {"summary": "stale", "x-apifox-folder": "清理目录"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 场景 E: 含 / 的 folder 旧布局 → apis/父/子.json
        write_remote_op("父/子", "GET", "/e", {"summary": "e"})
        (apis / "父").mkdir(parents=True)
        (apis / "父" / "子.json").write_text(
            json.dumps(
                {"paths": {"/e": {"get": {"summary": "e", "x-apifox-folder": "父/子"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0
        summary = json.loads(Path(f"{prefix}pull-diff.json").read_text(encoding="utf-8"))

        assert summary["新目录"]["status"] == "new-file", summary["新目录"]
        assert "GET /a" in summary["新目录"]["new"]

        assert summary["新布局同步"]["status"] == "nochange"
        assert summary["新布局同步"]["legacy_file"] is False

        info_c = summary["旧迁移"]
        assert info_c["status"] == "diff"
        assert info_c["legacy_file"] is True
        assert "GET /c_new" in info_c["new"]
        assert "GET /c_changed" in info_c["updated"]
        assert "POST /c_removed" in info_c["removed"]

        info_d = summary["清理目录"]
        assert info_d["status"] == "diff"
        assert info_d["legacy_file"] is False
        assert info_d["new"] == []
        assert info_d["updated"] == []
        assert "POST /d_stale" in info_d["removed"]
        assert "GET /d_keep" in info_d["unchanged"]

        info_e = summary["父/子"]
        assert info_e["status"] == "nochange"
        assert info_e["legacy_file"] is True
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
