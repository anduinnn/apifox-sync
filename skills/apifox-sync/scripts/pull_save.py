#!/usr/bin/env python3
"""按 approved 清单把 ${TMPPREFIX}pull-*.json 落盘到 .claude/apis/。

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
    ${TMPPREFIX}pull-*.json         各 folder 切片（pull-diff.json / pull-approved.json 自身除外）

行为：
    对每个切片：
      - folder 在 approved 列表 → move 到 .claude/apis/<folder>.json
      - 否则 → 删除临时文件，不写盘
    stdout 打印 SAVED: / SKIP: 行

等价替换 pull.md 步骤 6 内嵌 python3 逻辑。path 映射规则：含 `/` 时
`A/B` → `<apis>/A/B.json`；无 `/` 时 `A` → `<apis>/A.json`。

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_codec  # noqa: E402


def run(project_root: str, tmpprefix: str) -> int:
    apis_dir = os.path.join(project_root, ".claude", "apis")

    approved_path = f"{tmpprefix}pull-approved.json"
    try:
        approved = set(json.loads(Path(approved_path).read_text(encoding="utf-8")))
    except Exception:
        approved = set()

    saved: list[tuple[str, str]] = []
    skipped: list[str] = []

    for tmp_path in sorted(glob.glob(f"{tmpprefix}pull-*.json")):
        if tmp_path.endswith("pull-diff.json") or tmp_path.endswith("pull-approved.json"):
            continue
        basename = os.path.basename(tmp_path)
        prefix_base = os.path.basename(tmpprefix)
        encoded = basename
        if encoded.startswith(prefix_base):
            encoded = encoded[len(prefix_base):]
        encoded = encoded.removeprefix("pull-").removesuffix(".json")
        actual_folder = path_codec.decode(encoded)

        if actual_folder not in approved:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            skipped.append(actual_folder)
            continue

        parts = actual_folder.rsplit("/", 1)
        if len(parts) == 2:
            parent_dir = os.path.join(apis_dir, parts[0])
            os.makedirs(parent_dir, exist_ok=True)
            target_path = os.path.join(apis_dir, parts[0], parts[1] + ".json")
        else:
            os.makedirs(apis_dir, exist_ok=True)
            target_path = os.path.join(apis_dir, actual_folder + ".json")

        shutil.move(tmp_path, target_path)
        rel_path = os.path.relpath(target_path, project_root)
        saved.append((actual_folder, rel_path))

    for folder, rel_path in saved:
        print(f'SAVED: "{folder}" → {rel_path}')
    for folder in skipped:
        print(f'SKIP : "{folder}" （用户未勾选，本地保持不变）')
    return 0


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        prefix = str(tmp) + "/apifox-sync-"
        project_root = tmp / "proj"
        project_root.mkdir()

        # 写 3 个切片
        def write_tmp(folder: str, content: dict) -> str:
            enc = path_codec.encode(folder)
            p = f"{prefix}pull-{enc}.json"
            Path(p).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            return p

        p1 = write_tmp("简单", {"paths": {"/a": {}}})
        p2 = write_tmp("父/子", {"paths": {"/b": {}}})
        p3 = write_tmp("历史__组", {"paths": {"/c": {}}})
        p4 = write_tmp("未勾选", {"paths": {"/d": {}}})
        assert all(Path(p).is_file() for p in (p1, p2, p3, p4))

        Path(f"{prefix}pull-approved.json").write_text(
            json.dumps(["简单", "父/子", "历史__组"], ensure_ascii=False),
            encoding="utf-8",
        )

        os.environ["TMPPREFIX"] = prefix
        rc = run(str(project_root), prefix)
        assert rc == 0

        # approved 三个被 move 到 apis 下
        assert (project_root / ".claude" / "apis" / "简单.json").is_file()
        assert (project_root / ".claude" / "apis" / "父" / "子.json").is_file()
        assert (project_root / ".claude" / "apis" / "历史__组.json").is_file()
        # 未勾选被删除
        assert not Path(p4).exists()
        # 原切片文件已 move
        assert not Path(p1).exists()
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
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
