#!/usr/bin/env python3
"""按 approved folder 清单把 ${TMPPREFIX}pull-op-*.json 落盘到 .claude/apis/<folder>/。

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

行为（对每个 approved folder）：
    1) 删除旧 folder 聚合文件 .claude/apis/<folder>.json（若存在，迁移到新结构）
    2) 该 folder 远程返回的接口 → move op 临时文件到
       .claude/apis/<folder>/<op_relpath>（自动 mkdir 父目录）
    3) 该 folder 本地新布局里远程未返回的接口文件 → 删除（远程已删除）
    4) 非 approved folder 的 op 临时文件 → 直接删除
    stdout 打印 SAVED: / REMOVED: / MIGRATED: / SKIP: 行

path 映射：
    op_relpath 由 api_path.op_relpath(METHOD, path) 生成，保留 `/` 作为子目录。
    最终：.claude/apis/<folder>/<op_relpath>

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


def read_op(tmp_path: str) -> Optional[dict]:
    """读取 op 切片 → {folder, method, path, tmp}。"""
    try:
        data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    paths = data.get("paths", {})
    if not isinstance(paths, dict) or len(paths) != 1:
        return None
    path = next(iter(paths))
    methods = paths[path]
    if not isinstance(methods, dict) or len(methods) != 1:
        return None
    method = next(iter(methods))
    operation = methods[method]
    folder = operation.get("x-apifox-folder", "") if isinstance(operation, dict) else ""
    return {"folder": folder, "method": method.upper(), "path": path, "tmp": tmp_path}


def old_layout_path(project_root: str, folder: str) -> str:
    apis_dir = os.path.join(project_root, ".claude", "apis")
    parts = folder.rsplit("/", 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + ".json")
    return os.path.join(apis_dir, folder + ".json")


def folder_dir(project_root: str, folder: str) -> str:
    return os.path.join(project_root, ".claude", "apis", folder)


def scan_existing_op_files(project_root: str, folder: str) -> list[tuple[str, str, str]]:
    """扫描本地 folder 目录下所有 op 文件 → [(method, path, abs_path), ...]。"""
    root = folder_dir(project_root, folder)
    out: list[tuple[str, str, str]] = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not api_path.is_op_file(rel):
                continue
            try:
                method, path = api_path.split_op_relpath(rel)
            except ValueError:
                continue
            out.append((method, path, full))
    return out


def cleanup_empty_dirs(leaf_dir: str, stop_at: str) -> None:
    """从 leaf_dir 向上删除空目录，直到 stop_at（不含）。"""
    stop_abs = os.path.abspath(stop_at)
    cur = os.path.abspath(leaf_dir)
    while cur != stop_abs and cur.startswith(stop_abs + os.sep):
        try:
            if not os.listdir(cur):
                os.rmdir(cur)
            else:
                break
        except OSError:
            break
        cur = os.path.dirname(cur)


def run(project_root: str, tmpprefix: str) -> int:
    apis_dir = os.path.join(project_root, ".claude", "apis")

    approved_path = f"{tmpprefix}pull-approved.json"
    try:
        approved = set(json.loads(Path(approved_path).read_text(encoding="utf-8")))
    except Exception:
        approved = set()

    # 按 folder 归并临时 op 文件
    folder_ops: dict[str, list[dict]] = {}
    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-op-*.json")):
        entry = read_op(tmp_path)
        if entry is None:
            # 解析失败，直接删除临时文件，继续
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
            # 未勾选 → 直接删除所有临时文件
            for entry in ops:
                try:
                    os.remove(entry["tmp"])
                except OSError:
                    pass
            skipped_folders.append(folder)
            continue

        # 1) 迁移旧聚合文件
        old_file = old_layout_path(project_root, folder)
        if os.path.exists(old_file) and not os.path.isdir(old_file):
            try:
                os.remove(old_file)
                migrated_old.append(folder)
            except OSError as e:
                print(f"WARN: 无法删除旧聚合文件 {old_file}: {e}", file=sys.stderr)

        # 2) move 每个 op 文件到新布局
        target_folder_dir = folder_dir(project_root, folder)
        remote_keys = set()
        for entry in ops:
            method = entry["method"]
            path = entry["path"]
            remote_keys.add((method, path))
            rel = api_path.op_relpath(method, path)
            target = os.path.join(target_folder_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            # 若目标已存在，shutil.move 在 Unix 下会覆盖；在 Windows 下需先删除
            if os.path.exists(target):
                try:
                    os.remove(target)
                except OSError:
                    pass
            shutil.move(entry["tmp"], target)
            saved.append((folder, os.path.relpath(target, project_root)))

        # 3) 清理远程已删除的本地接口文件
        for method, path, abs_path in scan_existing_op_files(project_root, folder):
            if (method, path) not in remote_keys:
                try:
                    os.remove(abs_path)
                    removed_stale.append((folder, os.path.relpath(abs_path, project_root)))
                    # 同时清理空父目录（仅限 folder 内部，不上穿越）
                    cleanup_empty_dirs(os.path.dirname(abs_path), target_folder_dir)
                except OSError as e:
                    print(f"WARN: 无法删除过期文件 {abs_path}: {e}", file=sys.stderr)

    # 未 approve 且无 op 文件的 folder（例如"全部覆盖"但用户之前未勾选）：无需额外处理

    for folder, rel in saved:
        print(f'SAVED   : "{folder}" → {rel}')
    for folder, rel in removed_stale:
        print(f'REMOVED : "{folder}" → {rel} （远程已删除）')
    for folder in migrated_old:
        print(f'MIGRATED: "{folder}" （旧聚合文件已删除，已拆分为单接口文件）')
    for folder in skipped_folders:
        print(f'SKIP    : "{folder}" （用户未勾选，本地保持不变）')
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        apis = project_root / ".claude" / "apis"
        apis.mkdir(parents=True)

        def write_op(folder: str, method: str, path: str, body: Optional[dict] = None) -> str:
            body = body or {"summary": f"{method} {path}"}
            body["x-apifox-folder"] = folder
            data = {
                "paths": {path: {method.lower(): body}},
                "components": {"schemas": {}},
            }
            key = api_path.hash_key(folder, method, path)
            p = f"{prefix}pull-op-{key}.json"
            Path(p).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return p

        # 场景 A: 普通 folder 全新接口
        p_a = write_op("简单", "GET", "/a")
        p_a2 = write_op("简单", "POST", "/a/{id}")

        # 场景 B: 带旧聚合文件（需要迁移）
        write_op("迁移", "GET", "/m_new")
        write_op("迁移", "GET", "/m_kept")
        (apis / "迁移.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/m_kept": {"get": {"summary": "kept", "x-apifox-folder": "迁移"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 场景 C: 本地有过期接口文件需要清理
        (apis / "清理" / "x").mkdir(parents=True)
        stale = apis / "清理" / "x" / "stale.GET.json"
        stale.write_text(
            json.dumps(
                {"paths": {"/x/stale": {"get": {"summary": "gone", "x-apifox-folder": "清理"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        write_op("清理", "GET", "/x/keep")

        # 场景 D: 含 `/` 的 folder（旧 apis/父/子.json → 新 apis/父/子/*）
        (apis / "父").mkdir(parents=True, exist_ok=True)
        (apis / "父" / "子.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "/p": {"get": {"summary": "p", "x-apifox-folder": "父/子"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        write_op("父/子", "GET", "/p")

        # 场景 E: 未勾选 folder，临时文件应被删
        p_unchecked = write_op("未勾选", "GET", "/u")

        Path(f"{prefix}pull-approved.json").write_text(
            json.dumps(["简单", "迁移", "清理", "父/子"], ensure_ascii=False),
            encoding="utf-8",
        )

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0

        # A
        assert (apis / "简单" / "a.GET.json").is_file()
        assert (apis / "简单" / "a" / "{id}.POST.json").is_file()

        # B: 旧 folder 文件被删，新文件落盘
        assert not (apis / "迁移.json").exists()
        assert (apis / "迁移" / "m_new.GET.json").is_file()
        assert (apis / "迁移" / "m_kept.GET.json").is_file()

        # C: 过期文件被清理，保留的新增
        assert not stale.exists()
        # keep 的 path 是 /x/keep，落盘为 清理/x/keep.GET.json；父目录不空故保留
        assert (apis / "清理" / "x" / "keep.GET.json").is_file(), list((apis / "清理").rglob("*"))
        assert (apis / "清理" / "x").is_dir()

        # 再验证：若所有接口都被清空，目录会递归清理
        # 模拟 stale 的父目录空后被 cleanup（单独构造一个独立场景）
        lonely_dir = apis / "独立清理" / "deep" / "nested"
        lonely_dir.mkdir(parents=True)
        lonely_file = lonely_dir / "gone.POST.json"
        lonely_file.write_text(
            json.dumps(
                {"paths": {"/deep/nested/gone": {"post": {"summary": "g", "x-apifox-folder": "独立清理"}}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 重新运行 run()：approved 中加入"独立清理"但远程无任何 op 返回 → 该目录下的接口应被清理
        # 先写空 approved 增加"独立清理"
        Path(f"{prefix}pull-approved.json").write_text(
            json.dumps(["独立清理"], ensure_ascii=False), encoding="utf-8"
        )
        # 清理残留 op 临时文件（上一轮已 move），此时没有任何 op 文件
        # 但我们需要手动塞一个 "独立清理" 触发 scan——方案：写一个 placeholder op（不在 approved 的同 folder 下会被直接删）
        # 直接给 scan 机会：写一个"独立清理"下的无关接口远端切片，保证 folder_ops 里有该 folder 条目
        _placeholder = write_op("独立清理", "GET", "/only")
        rc2 = run(str(project_root), prefix)
        assert rc2 == 0
        # gone 被清理，lonely 目录链被清理
        assert not lonely_file.exists()
        assert not (apis / "独立清理" / "deep").exists(), list((apis / "独立清理").rglob("*"))
        # 只剩 only.GET.json
        assert (apis / "独立清理" / "only.GET.json").is_file()

        # D: 旧聚合 apis/父/子.json 被删，新布局落到 apis/父/子/p.GET.json
        assert not (apis / "父" / "子.json").exists()
        assert (apis / "父" / "子" / "p.GET.json").is_file()

        # E: 未勾选临时文件被删
        assert not Path(p_unchecked).exists()
        assert not Path(p_a).exists()
        assert not Path(p_a2).exists()
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
        print("Usage: pull_save.py <project_root> | -h | --self-test", file=sys.stderr)
        return 2
    tmpprefix = os.environ.get("TMPPREFIX")
    if not tmpprefix:
        print("ERROR: env TMPPREFIX is required", file=sys.stderr)
        return 1
    return run(argv[1], tmpprefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
