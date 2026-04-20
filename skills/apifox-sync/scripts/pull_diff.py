#!/usr/bin/env python3
"""对远端 ${TMPPREFIX}pull-op-*.json 切片与本地 .claude/apis/ 做 diff 预览 + 目标结构展示。

用法：
    python3 pull_diff.py <project_root>
    python3 pull_diff.py -h
    python3 pull_diff.py --self-test

argv:
    <project_root>  项目根目录绝对路径（用于定位 .claude/apis/）

env:
    TMPPREFIX  临时文件前缀（必填）

输入：
    glob ${TMPPREFIX}pull-op-*.json                远程单接口切片
    本地新布局（v1.4）：.claude/apis/<folder>/<summary>.json
    本地旧布局（v1.3）：.claude/apis/<folder>/<path段>/<last>.METHOD.json
    本地旧聚合（v1.2）：.claude/apis/<folder>.json

匹配规则：
    v1.4 之后**不再**依赖本地文件名。对本地 folder 目录递归扫描所有 .json，读取每个
    文件的 `paths` 第一个 entry → (METHOD, path)，与远端 (METHOD, path) 对齐。
    旧聚合文件内部也按同样方式展开。

输出：
    ${TMPPREFIX}pull-diff.json  按 folder 聚合的详细 diff
    stdout：人类可读的 NEW / SAME / DIFF 摘要 + 每个 folder 的"目标文件结构"预览

每 folder 的 diff 字段：
    {
      "status": "new-file" | "nochange" | "diff",
      "new":        ["METHOD path  → 目标文件名.json", ...],
      "updated":    ["METHOD path  → 目标文件名.json  (重命名自 旧文件.json)", ...],
      "unchanged":  ["METHOD path  → 目标文件名.json", ...],
      "removed":    ["METHOD path  (本地 旧文件.json)", ...],
      "legacy_file": bool  (v1.2 旧聚合存在)
      "target_layout": ["<folder>/<filename>.json", ...]
    }

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


# -------- 远端切片读取 --------

def read_remote_op(tmp_path: str) -> Optional[dict]:
    """{folder, method, path, summary, operation}；失败 → None。"""
    parsed = api_path.parse_op_file(tmp_path)
    if parsed is None:
        print(f"ERROR: op 切片结构不合法: {tmp_path}", file=sys.stderr)
        return None
    method, path, op = parsed
    folder = op.get("x-apifox-folder", "") if isinstance(op, dict) else ""
    summary = op.get("summary", "") if isinstance(op, dict) else ""
    return {
        "folder": folder if isinstance(folder, str) else "",
        "method": method,
        "path": path,
        "summary": summary if isinstance(summary, str) else "",
        "operation": op,
    }


# -------- 本地布局读取 --------

def old_aggregate_path(project_root: str, folder: str) -> str:
    """v1.2 旧布局：folder 含 `/` → A/B.json；无 `/` → A.json"""
    apis_dir = os.path.join(project_root, ".claude", "apis")
    parts = folder.rsplit("/", 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + ".json")
    return os.path.join(apis_dir, folder + ".json")


def folder_dir(project_root: str, folder: str) -> str:
    return os.path.join(project_root, ".claude", "apis", folder)


def scan_local_ops(project_root: str, folder: str) -> dict[tuple[str, str], dict]:
    """本地视角：(METHOD, path) → {operation, source, filename}。

    source: "new" | "legacy_aggregate"
    filename: 相对 folder_dir 的路径（legacy_aggregate 时为旧聚合文件路径 basename）
    """
    out: dict[tuple[str, str], dict] = {}

    # 1) 新布局扫描（v1.3/v1.4 都能被扫出来，因为都是单接口文件）
    for entry in api_path.scan_folder_ops(folder_dir(project_root, folder)):
        key = (entry["method"], entry["path"])
        out[key] = {
            "operation": entry["operation"],
            "source": "new",
            "filename": entry["filename"],
        }

    # 2) v1.2 旧聚合文件展开（只在新布局没覆盖的 key 上补）
    old_path = old_aggregate_path(project_root, folder)
    if os.path.exists(old_path) and not os.path.isdir(old_path):
        try:
            data = json.loads(Path(old_path).read_text(encoding="utf-8"))
        except Exception:
            data = {}
        paths = data.get("paths", {}) if isinstance(data, dict) else {}
        for p, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for m, op in methods.items():
                if not isinstance(op, dict):
                    continue
                key = (m.upper(), p)
                if key in out:
                    continue
                out[key] = {
                    "operation": op,
                    "source": "legacy_aggregate",
                    "filename": os.path.basename(old_path) + "(内部)",
                }
    return out


# -------- 命名规划 --------

def plan_filenames(remote_entries: list[dict]) -> dict[tuple[str, str], str]:
    """对同 folder 下的远端接口按 summary 命名；冲突时加 METHOD。与 pull_save 逻辑一致。"""
    base: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    for e in remote_entries:
        name = api_path.op_filename(e["summary"], e["method"], e["path"], with_method=False)
        base[(e["method"], e["path"])] = name
        counts[name] = counts.get(name, 0) + 1
    result: dict[tuple[str, str], str] = {}
    for e in remote_entries:
        key = (e["method"], e["path"])
        bn = base[key]
        if counts.get(bn, 0) > 1:
            result[key] = api_path.op_filename(e["summary"], e["method"], e["path"], with_method=True)
        else:
            result[key] = bn
    return result


# -------- 核心 diff --------

def run(project_root: str, tmpprefix: str) -> int:
    # 分组远端
    remote_by_folder: dict[str, list[dict]] = {}
    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-op-*.json")):
        entry = read_remote_op(tmp_path)
        if entry is None:
            return 1
        remote_by_folder.setdefault(entry["folder"], []).append(entry)

    summary: dict[str, dict] = {}

    for folder, remote_entries in remote_by_folder.items():
        local_ops = scan_local_ops(project_root, folder)
        filename_map = plan_filenames(remote_entries)
        remote_by_key: dict[tuple[str, str], dict] = {
            (e["method"], e["path"]): e for e in remote_entries
        }

        has_legacy_aggregate = os.path.exists(old_aggregate_path(project_root, folder)) and not os.path.isdir(
            old_aggregate_path(project_root, folder)
        )
        has_any_local = len(local_ops) > 0

        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        removed: list[str] = []
        target_layout: list[str] = []

        for key, e in sorted(remote_by_key.items()):
            fn = filename_map[key]
            label = f"{key[0]} {key[1]}  → {fn}"
            target_layout.append(f"{folder}/{fn}")
            if key not in local_ops:
                added.append(label)
                continue
            local = local_ops[key]
            # 远端 operation 里带 x-apifox-folder，本地新布局也带；旧聚合也带
            same = local["operation"] == e["operation"]
            if not same:
                rename_note = ""
                if local["source"] == "new" and local["filename"] != fn:
                    rename_note = f"  (重命名自 {local['filename']})"
                elif local["source"] == "legacy_aggregate":
                    rename_note = f"  (拆分自 {local['filename']})"
                updated.append(label + rename_note)
            else:
                rename_note = ""
                if local["source"] == "new" and local["filename"] != fn:
                    rename_note = f"  (重命名自 {local['filename']})"
                elif local["source"] == "legacy_aggregate":
                    rename_note = f"  (拆分自 {local['filename']})"
                if rename_note:
                    # 内容相同但文件名/布局要变 → 视作 updated（磁盘会变）
                    updated.append(label + rename_note)
                else:
                    unchanged.append(label)

        for key, local in sorted(local_ops.items()):
            if key in remote_by_key:
                continue
            m, p = key
            removed.append(f"{m} {p}  (本地 {local['filename']})")

        if not has_any_local and not has_legacy_aggregate:
            status = "new-file"
        elif not (added or updated or removed):
            status = "nochange"
        else:
            status = "diff"

        summary[folder] = {
            "status": status,
            "new": added,
            "updated": updated,
            "removed": removed,
            "unchanged": unchanged,
            "legacy_file": has_legacy_aggregate,
            "target_layout": sorted(target_layout),
        }

    with open(f"{tmpprefix}pull-diff.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # stdout 人类可读
    for folder in sorted(summary.keys()):
        info = summary[folder]
        target_dir_rel = os.path.relpath(
            os.path.join(project_root, ".claude", "apis", folder), project_root
        )
        legacy_note = "（将拆分旧聚合文件）" if info["legacy_file"] else ""
        if info["status"] == "new-file":
            print(
                f'\n[NEW  ] {folder}  → {target_dir_rel}/ '
                f'（本地不存在，将新建 {len(info["new"])} 个接口文件）'
            )
        elif info["status"] == "nochange":
            print(
                f'\n[ SAME] {folder}  → {target_dir_rel}/'
                f'（无变化，{len(info["unchanged"])} 个接口）{legacy_note}'
            )
        else:
            print(f"\n[DIFF ] {folder}  → {target_dir_rel}/{legacy_note}")

        for k in info["new"]:
            print(f"    + {k}")
        for k in info["updated"]:
            print(f"    ~ {k}")
        for k in info["removed"]:
            print(f"    - {k}  （本地将清理）")

        if info["status"] != "nochange" and info["target_layout"]:
            print("    目标结构：")
            for rel in info["target_layout"]:
                print(f"      .claude/apis/{rel}")
    return 0


# -------- self-test --------

def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        apis = project_root / ".claude" / "apis"
        apis.mkdir(parents=True)

        def write_remote_op(folder: str, method: str, path: str, summary_txt: str, extra: Optional[dict] = None) -> None:
            op = {"summary": summary_txt, "x-apifox-folder": folder}
            if extra:
                op.update(extra)
            data = {
                "paths": {path: {method.lower(): op}},
                "components": {"schemas": {}},
            }
            key = api_path.hash_key(folder, method, path)
            Path(f"{prefix}pull-op-{key}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

        def write_local_op(folder: str, method: str, path: str, summary_txt: str, rel_name: str, extra: Optional[dict] = None) -> None:
            op = {"summary": summary_txt, "x-apifox-folder": folder}
            if extra:
                op.update(extra)
            target = apis / folder / rel_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {
                        "paths": {path: {method.lower(): op}},
                        "components": {"schemas": {}},
                    }
                ),
                encoding="utf-8",
            )

        # A: 全新 folder
        write_remote_op("新目录", "GET", "/a", "A接口")

        # B: v1.4 新布局已同步
        write_remote_op("已同步", "GET", "/b", "B接口")
        write_local_op("已同步", "GET", "/b", "B接口", "B接口.json")

        # C: v1.3 旧文件名，内容相同，要被重命名
        write_remote_op("重命名", "POST", "/api/x/y", "改名接口")
        write_local_op("重命名", "POST", "/api/x/y", "改名接口", "api/x/y.POST.json")

        # D: 内容变更
        write_remote_op("内容变", "PUT", "/u/{id}", "更新", extra={"description": "v2"})
        write_local_op("内容变", "PUT", "/u/{id}", "更新", "更新.json", extra={"description": "v1"})

        # E: 本地多余接口（清理）
        write_remote_op("清理", "GET", "/keep", "保留")
        write_local_op("清理", "GET", "/keep", "保留", "保留.json")
        write_local_op("清理", "POST", "/stale", "过时", "过时.json")

        # F: v1.2 旧聚合
        (apis / "v12").mkdir(parents=True, exist_ok=True)
        (apis / "v12.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/old": {"get": {"summary": "旧内容", "x-apifox-folder": "v12"}}
                    }
                }
            ),
            encoding="utf-8",
        )
        write_remote_op("v12", "GET", "/old", "旧内容")

        # G: summary 冲突 → 加 METHOD
        write_remote_op("冲突", "POST", "/u", "用户")
        write_remote_op("冲突", "GET", "/u/{id}", "用户")

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0
        result = json.loads(Path(f"{prefix}pull-diff.json").read_text(encoding="utf-8"))

        assert result["新目录"]["status"] == "new-file"
        assert any("GET /a" in s for s in result["新目录"]["new"])
        assert any("新目录/A接口.json" in p for p in result["新目录"]["target_layout"])

        assert result["已同步"]["status"] == "nochange"

        # 重命名：内容相同但布局要变 → updated 带"重命名自"
        info_c = result["重命名"]
        assert info_c["status"] == "diff"
        assert any("重命名自 api/x/y.POST.json" in s for s in info_c["updated"]), info_c

        # 内容变更：updated
        info_d = result["内容变"]
        assert info_d["status"] == "diff"
        assert any("PUT /u/{id}" in s and "→ 更新.json" in s for s in info_d["updated"])

        # 清理：过时被 removed
        info_e = result["清理"]
        assert info_e["status"] == "diff"
        assert any("POST /stale" in s for s in info_e["removed"])
        assert any("GET /keep" in s for s in info_e["unchanged"])

        # v12：旧聚合 → updated 带"拆分自"
        info_f = result["v12"]
        assert info_f["legacy_file"] is True
        assert info_f["status"] == "diff"
        assert any("拆分自" in s for s in info_f["updated"])

        # 冲突：带 METHOD 的文件名
        info_g = result["冲突"]
        layout = info_g["target_layout"]
        assert any("冲突/用户.POST.json" in p for p in layout), layout
        assert any("冲突/用户.GET.json" in p for p in layout), layout
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
