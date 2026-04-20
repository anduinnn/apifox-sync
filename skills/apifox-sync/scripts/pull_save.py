#!/usr/bin/env python3
"""按 approved folder 清单把 ${TMPPREFIX}pull-op-*.json 落盘到 .claude/apis/<folder>/<summary>.json。

用法：
    python3 pull_save.py <project_root>
    python3 pull_save.py -h
    python3 pull_save.py --self-test

argv:
    <project_root>  项目根目录绝对路径

env:
    TMPPREFIX  临时文件前缀（必填）

输入：
    ${TMPPREFIX}pull-approved.json  字符串数组（actual_folder 清单）
    ${TMPPREFIX}pull-op-*.json      各接口切片

命名规则（v1.4）：
    <apis_dir>/<folder>/<filename>.json
      filename = sanitize(summary)
      若 summary 为空 → fallback 到 path 最后一段（清洗后）；根路径 → "_root"
      若同一 folder 内两条接口产生相同 filename → 所有冲突的接口改用
        filename = "<stem>.<METHOD>"（保证冲突全部被拆散，人读也清楚）

本地 ↔ 远程匹配：
    读取本地 .json 的内部 `paths` 第一个 entry，按 (METHOD, path) 与远程匹配。
    这样无论旧布局（v1.2 folder 聚合 / v1.3 path 展开成目录）还是新布局，都能识别。

行为（对每个 approved folder）：
    1) 旧 folder 聚合文件 .claude/apis/<folder>.json 存在 → 拆分其内部接口落到新布局；
       然后删除旧聚合文件
    2) 远程接口：按上述命名规则写入 .claude/apis/<folder>/<filename>.json；
       若本地存在同 (METHOD, path) 但文件名不同的 op 文件 → 删除旧文件再写入新文件
    3) 本地 folder 目录下 (METHOD, path) 在远程不存在的 op 文件 → 删除
    4) 递归清理 folder 目录下的空子目录
    5) 非 approved folder 的临时 op 切片 → 直接删除

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_path  # noqa: E402


# -------- 临时切片读取 --------

def read_tmp_op(tmp_path: str) -> Optional[dict]:
    """读取 ${TMPPREFIX}pull-op-*.json → {folder, method, path, summary, raw, tmp}。"""
    parsed = api_path.parse_op_file(tmp_path)
    if parsed is None:
        return None
    method, path, op = parsed
    folder = op.get("x-apifox-folder", "") if isinstance(op, dict) else ""
    summary = op.get("summary", "") if isinstance(op, dict) else ""
    try:
        raw = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "folder": folder if isinstance(folder, str) else "",
        "method": method,
        "path": path,
        "summary": summary if isinstance(summary, str) else "",
        "raw": raw,
        "tmp": tmp_path,
    }


# -------- 文件名分配（summary 冲突处理） --------

def assign_filenames(entries: list[dict]) -> dict[tuple[str, str], str]:
    """对同一 folder 下的接口分配 filename；(METHOD, path) → filename。

    策略：
    1) 先按"不带 METHOD"算每条的 candidate filename
    2) 统计 candidate 出现次数；>=2 的所有参与者统一改用"带 METHOD"
    3) 若加 METHOD 后仍冲突（极端：summary 相同 + method 相同 + path 不同？
       这不可能因为 (METHOD,path) 唯一），仍保留带 METHOD 版本；兜底加 hash
    """
    base_candidates: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    for e in entries:
        name = api_path.op_filename(e["summary"], e["method"], e["path"], with_method=False)
        base_candidates[(e["method"], e["path"])] = name
        counts[name] = counts.get(name, 0) + 1

    result: dict[tuple[str, str], str] = {}
    for e in entries:
        key = (e["method"], e["path"])
        base = base_candidates[key]
        if counts.get(base, 0) > 1:
            # 冲突 → 加 METHOD 后缀
            result[key] = api_path.op_filename(e["summary"], e["method"], e["path"], with_method=True)
        else:
            result[key] = base

    # 二次冲突兜底（理论上不会触发）：同 folder 两条 (METHOD, path) 不同但 summary+METHOD 相同
    seen: dict[str, tuple[str, str]] = {}
    for key, name in list(result.items()):
        if name in seen and seen[name] != key:
            # 用 hash 区分
            h = api_path.hash_key("", key[0], key[1])[:8]
            stem = name[: -len(".json")]
            result[key] = f"{stem}.{h}.json"
        else:
            seen[name] = key
    return result


# -------- 旧布局定位 --------

def old_aggregate_path(project_root: str, folder: str) -> str:
    """v1.2 旧布局：folder 含 `/` → A/B.json；无 `/` → A.json"""
    apis_dir = os.path.join(project_root, ".claude", "apis")
    parts = folder.rsplit("/", 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + ".json")
    return os.path.join(apis_dir, folder + ".json")


def folder_dir(project_root: str, folder: str) -> str:
    return os.path.join(project_root, ".claude", "apis", folder)


def migrate_old_aggregate(project_root: str, folder: str) -> list[str]:
    """把 v1.2 旧 folder 聚合文件内部的接口拆成单接口文件后删除旧文件。

    返回拆出的临时文件绝对路径列表（供主流程纳入扫描）。实现：直接把每条 operation
    写成一个符合 api_path.parse_op_file 结构的 .json，文件名用 "__legacy_<hash>.json"
    占位（主流程随后会按正常流程重命名）；但为了最小化改动，这里直接写到目标
    folder_dir 下的临时占位名里，并返回其路径。
    """
    path = old_aggregate_path(project_root, folder)
    if not os.path.exists(path) or os.path.isdir(path):
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: 旧聚合文件解析失败，跳过迁移 {path}: {e}", file=sys.stderr)
        return []
    target_dir = folder_dir(project_root, folder)
    os.makedirs(target_dir, exist_ok=True)
    spawned: list[str] = []
    paths = data.get("paths", {}) if isinstance(data, dict) else {}
    all_schemas = data.get("components", {}).get("schemas", {}) if isinstance(data, dict) else {}
    for p, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for m, op in methods.items():
            if not isinstance(op, dict):
                continue
            h = api_path.hash_key(folder, m, p)
            placeholder = os.path.join(target_dir, f"__legacy_{h}.json")
            slice_data = {
                "paths": {p: {m: op}},
                "components": {"schemas": all_schemas},
            }
            try:
                with open(placeholder, "w", encoding="utf-8") as f:
                    json.dump(slice_data, f, ensure_ascii=False, indent=2)
                spawned.append(placeholder)
            except OSError as e:
                print(f"WARN: 旧聚合内接口落盘失败 {placeholder}: {e}", file=sys.stderr)
    try:
        os.remove(path)
    except OSError as e:
        print(f"WARN: 无法删除旧聚合文件 {path}: {e}", file=sys.stderr)
    return spawned


# -------- 空目录清理 --------

def cleanup_empty_dirs(leaf_dir: str, stop_at: str) -> None:
    """从 leaf_dir 向上删除空目录，停在 stop_at（不删 stop_at 自身）。"""
    stop_abs = os.path.abspath(stop_at)
    cur = os.path.abspath(leaf_dir)
    # 仅处理 stop_at 严格下方的子目录
    while cur != stop_abs and cur.startswith(stop_abs + os.sep):
        try:
            if not os.listdir(cur):
                os.rmdir(cur)
            else:
                break
        except OSError:
            break
        cur = os.path.dirname(cur)


# -------- 核心 --------

def run(project_root: str, tmpprefix: str) -> int:
    approved_path = f"{tmpprefix}pull-approved.json"
    try:
        approved = set(json.loads(Path(approved_path).read_text(encoding="utf-8")))
    except Exception:
        approved = set()

    # 1) 收集远端 op 切片
    folder_ops: dict[str, list[dict]] = {}
    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-op-*.json")):
        entry = read_tmp_op(tmp_path)
        if entry is None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            continue
        folder_ops.setdefault(entry["folder"], []).append(entry)

    saved: list[tuple[str, str]] = []
    removed_stale: list[tuple[str, str]] = []
    migrated_old: list[str] = []
    skipped_folders: list[str] = []

    for folder, ops in folder_ops.items():
        if folder not in approved:
            # 未勾选 → 直接删除所有临时切片；不触碰本地
            for entry in ops:
                try:
                    os.remove(entry["tmp"])
                except OSError:
                    pass
            skipped_folders.append(folder)
            continue

        target_dir = folder_dir(project_root, folder)

        # 2) 迁移旧聚合文件（若存在）
        if os.path.exists(old_aggregate_path(project_root, folder)):
            migrate_old_aggregate(project_root, folder)
            migrated_old.append(folder)

        # 3) 扫描本地（在迁移后扫描，占位文件也会被纳入）
        local_ops = api_path.scan_folder_ops(target_dir)
        local_by_key: dict[tuple[str, str], str] = {
            (o["method"], o["path"]): o["abs_path"] for o in local_ops
        }

        # 4) 分配目标 filename（处理 summary 冲突）
        filename_map = assign_filenames(ops)
        remote_keys = {(e["method"], e["path"]) for e in ops}

        os.makedirs(target_dir, exist_ok=True)

        # 5) 写远端接口；若本地已有同 (METHOD, path) 的文件但路径不同 → 先删
        for entry in ops:
            key = (entry["method"], entry["path"])
            filename = filename_map[key]
            target = os.path.join(target_dir, filename)

            local_existing = local_by_key.get(key)
            if local_existing and os.path.abspath(local_existing) != os.path.abspath(target):
                # 旧布局/旧文件名 → 删除旧文件
                try:
                    os.remove(local_existing)
                    cleanup_empty_dirs(os.path.dirname(local_existing), target_dir)
                except OSError as e:
                    print(f"WARN: 无法删除旧文件 {local_existing}: {e}", file=sys.stderr)

            if os.path.exists(target) and not os.path.isdir(target):
                try:
                    os.remove(target)
                except OSError:
                    pass
            shutil.move(entry["tmp"], target)
            saved.append((folder, os.path.relpath(target, project_root)))

        # 6) 清理远端已不存在的本地接口
        for (m, p), abs_path in local_by_key.items():
            if (m, p) in remote_keys:
                continue
            if not os.path.exists(abs_path):
                continue  # 已在上一步删
            try:
                os.remove(abs_path)
                removed_stale.append((folder, os.path.relpath(abs_path, project_root)))
                cleanup_empty_dirs(os.path.dirname(abs_path), target_dir)
            except OSError as e:
                print(f"WARN: 无法删除过期文件 {abs_path}: {e}", file=sys.stderr)

    for folder, rel in saved:
        print(f'SAVED   : "{folder}" → {rel}')
    for folder, rel in removed_stale:
        print(f'REMOVED : "{folder}" → {rel} （远程已删除）')
    for folder in migrated_old:
        print(f'MIGRATED: "{folder}" （旧聚合文件已拆分并删除）')
    for folder in skipped_folders:
        print(f'SKIP    : "{folder}" （用户未勾选，本地保持不变）')
    return 0


# -------- self-test --------

def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        apis = project_root / ".claude" / "apis"
        apis.mkdir(parents=True)

        def write_tmp_op(folder: str, method: str, path: str, summary: str) -> str:
            op = {
                "summary": summary,
                "x-apifox-folder": folder,
            }
            data = {
                "paths": {path: {method.lower(): op}},
                "components": {"schemas": {}},
            }
            key = api_path.hash_key(folder, method, path)
            p = f"{prefix}pull-op-{key}.json"
            Path(p).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return p

        # 场景 A: summary 命名（包含含 `/` 的 folder 层级）
        write_tmp_op("用户服务/v1/用户管理", "POST", "/api/users", "创建用户")
        write_tmp_op("用户服务/v1/用户管理", "DELETE", "/api/users/{id}", "删除用户")

        # 场景 B: 空 summary → fallback 到 path 末段
        write_tmp_op("用户服务/v1/用户管理", "GET", "/api/users/{id}", "")

        # 场景 C: summary 冲突 → 全部加 METHOD 后缀
        write_tmp_op("批量操作", "POST", "/api/users", "用户")
        write_tmp_op("批量操作", "GET", "/api/users/{id}", "用户")

        # 场景 D: v1.3 旧布局（path 展开成子目录）→ 被识别为同 (METHOD, path) 删旧写新
        old_v13 = apis / "v13遗留" / "api" / "legacy.GET.json"
        old_v13.parent.mkdir(parents=True)
        old_v13.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/legacy": {
                            "get": {
                                "summary": "遗留接口",
                                "x-apifox-folder": "v13遗留",
                            }
                        }
                    },
                    "components": {"schemas": {}},
                }
            ),
            encoding="utf-8",
        )
        write_tmp_op("v13遗留", "GET", "/api/legacy", "遗留接口")

        # 场景 E: v1.2 旧聚合文件 → 迁移
        (apis / "v12聚合.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/m_kept": {
                            "get": {"summary": "保留", "x-apifox-folder": "v12聚合"}
                        },
                        "/m_removed": {
                            "post": {"summary": "应被清理", "x-apifox-folder": "v12聚合"}
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        write_tmp_op("v12聚合", "GET", "/m_kept", "保留")
        write_tmp_op("v12聚合", "GET", "/m_new", "新增")

        # 场景 F: 本地存在但远端不返的接口 → 删除
        (apis / "清理").mkdir()
        stale = apis / "清理" / "陈旧.GET.json"
        stale.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/stale": {
                            "get": {"summary": "陈旧", "x-apifox-folder": "清理"}
                        }
                    },
                    "components": {"schemas": {}},
                }
            ),
            encoding="utf-8",
        )
        write_tmp_op("清理", "GET", "/api/keep", "保留的接口")

        # 场景 G: 未勾选 folder 的临时文件应被删除
        p_unchecked = write_tmp_op("未勾选", "GET", "/u", "未勾选接口")

        Path(f"{prefix}pull-approved.json").write_text(
            json.dumps(
                ["用户服务/v1/用户管理", "批量操作", "v13遗留", "v12聚合", "清理"],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0

        # A: summary 命名（含 `/` folder 层级）
        assert (apis / "用户服务" / "v1" / "用户管理" / "创建用户.json").is_file()
        assert (apis / "用户服务" / "v1" / "用户管理" / "删除用户.json").is_file()
        # B: 空 summary fallback 到 path 末段
        assert (apis / "用户服务" / "v1" / "用户管理" / "{id}.json").is_file()
        # C: 冲突 → 带 METHOD
        assert (apis / "批量操作" / "用户.POST.json").is_file()
        assert (apis / "批量操作" / "用户.GET.json").is_file()
        # D: 旧 v1.3 布局被清理 + 新命名写入
        assert not old_v13.exists()
        assert not (apis / "v13遗留" / "api").exists()
        assert (apis / "v13遗留" / "遗留接口.json").is_file()
        # E: 旧聚合迁移 + 按 summary 命名；m_removed 应被清理（迁移到新布局后远端不返）
        assert not (apis / "v12聚合.json").exists()
        assert (apis / "v12聚合" / "保留.json").is_file()
        assert (apis / "v12聚合" / "新增.json").is_file()
        assert not any(f.name.startswith("__legacy_") for f in (apis / "v12聚合").iterdir())
        # 确认 m_removed（"应被清理"）被删除
        for f in (apis / "v12聚合").iterdir():
            data = json.loads(f.read_text(encoding="utf-8"))
            paths = list(data["paths"].keys())
            assert "/m_removed" not in paths
        # F: 陈旧接口被清理 + 保留的写入
        assert not stale.exists()
        assert (apis / "清理" / "保留的接口.json").is_file()
        # G: 未勾选临时文件被删
        assert not Path(p_unchecked).exists()
        # 临时 op 文件全部被消费
        assert not glob.glob(f"{prefix}pull-op-*.json")

        print("SELFTEST_OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 2:
        print("Usage: pull_save.py <project_root> | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(argv[1], tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
