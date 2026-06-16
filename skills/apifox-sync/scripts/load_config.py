#!/usr/bin/env python3
"""加载 Apifox 配置：env 优先，回落 `.claude/apifox.json`。

用法：
    python3 load_config.py <project_root>
    python3 load_config.py -h
    python3 load_config.py --self-test

argv:
    <project_root>  项目根目录绝对路径（用于定位 .claude/apifox.json）

env（优先级高于配置文件）:
    APIFOX_API_TOKEN  Apifox Bearer Token（仅读，脚本不会回显）
    APIFOX_PROJECT_ID Apifox 项目 ID

stdout 输出协议（严格两行）:
    HAS_TOKEN=yes|no
    PID=<project_id>

安全约束：
- 严禁 print Token 或 Token 片段；脚本末尾自检 stdout 中不包含 `afxp_`。
- Token 仅用于 HAS_TOKEN 标志位计算，不写入任何输出。

退出码：
    0 成功
    1 参数缺失或配置错误
    2 参数用法错误
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def load_config(project_root: str) -> tuple[str, str, bool]:
    """返回 (token, project_id, debug)。env 优先，回落配置文件。Token 不外流。"""
    cfg: dict = {}
    cfg_path = Path(project_root) / ".claude" / "apifox.json"
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    token = os.environ.get("APIFOX_API_TOKEN") or cfg.get("apiToken", "") or ""
    pid = os.environ.get("APIFOX_PROJECT_ID") or cfg.get("projectId", "") or ""
    debug = cfg.get("debug", False) is True
    return str(token), str(pid), debug


def emit(token: str, pid: str, debug: bool = False, project_root: str = "") -> str:
    """生成 stdout 内容，不含 Token 本身。"""
    has = "yes" if token else "no"
    lines = [f"HAS_TOKEN={has}", f"PID={pid}"]
    if debug:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from debug_log import generate_session_id
        cmd = "unknown"
        sid = generate_session_id(cmd)
        log_dir = Path(project_root) / ".claude" / "debug-logs" if project_root else Path(".claude/debug-logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{sid}.jsonl"
        lines.append("APIFOX_DEBUG=1")
        lines.append(f"APIFOX_DEBUG_LOG={log_path}")
        lines.append(f"APIFOX_SESSION_ID={sid}")
    else:
        lines.append("APIFOX_DEBUG=0")
    return "\n".join(lines) + "\n"


def _assert_no_token_leak(output: str) -> None:
    """自检：stdout 中不能出现 Apifox Token 前缀。"""
    if "afxp_" in output:
        print("SELFTEST_FAIL: Token prefix leaked in stdout", file=sys.stderr)
        sys.exit(1)


def self_test() -> int:
    """内置 fixture 自测，覆盖 2 种典型场景。"""
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-"))
    try:
        # fixture 1: 配置文件 + 无 env → HAS_TOKEN=yes，PID 来自文件
        root1 = tmp / "case1"
        (root1 / ".claude").mkdir(parents=True)
        (root1 / ".claude" / "apifox.json").write_text(
            json.dumps({"apiToken": "afxp_fake_for_test_only", "projectId": "12345"}),
            encoding="utf-8",
        )
        # 确保 env 不干扰
        saved = {
            k: os.environ.pop(k, None)
            for k in ("APIFOX_API_TOKEN", "APIFOX_PROJECT_ID")
        }
        try:
            t, p, d = load_config(str(root1))
            out = emit(t, p)
            assert "HAS_TOKEN=yes" in out and "PID=12345" in out, f"case1 stdout unexpected: {out!r}"
            assert d is False, "case1 debug should be False"
            _assert_no_token_leak(out)

            # fixture 2: 无配置文件 + env 覆盖
            root2 = tmp / "case2"
            root2.mkdir()
            os.environ["APIFOX_API_TOKEN"] = "afxp_env_fake"
            os.environ["APIFOX_PROJECT_ID"] = "99999"
            t, p, d = load_config(str(root2))
            out = emit(t, p)
            assert "HAS_TOKEN=yes" in out and "PID=99999" in out, f"case2 stdout unexpected: {out!r}"
            _assert_no_token_leak(out)

            # fixture 3: 无配置文件 + 无 env → HAS_TOKEN=no，PID 空
            os.environ.pop("APIFOX_API_TOKEN", None)
            os.environ.pop("APIFOX_PROJECT_ID", None)
            t, p, d = load_config(str(root2))
            out = emit(t, p)
            assert "HAS_TOKEN=no" in out and "PID=" in out, f"case3 stdout unexpected: {out!r}"
            _assert_no_token_leak(out)

            # fixture 4: 配置文件中 Token 空字符串
            root3 = tmp / "case3"
            (root3 / ".claude").mkdir(parents=True)
            (root3 / ".claude" / "apifox.json").write_text(
                json.dumps({"apiToken": "", "projectId": "88"}),
                encoding="utf-8",
            )
            t, p, d = load_config(str(root3))
            out = emit(t, p)
            assert "HAS_TOKEN=no" in out and "PID=88" in out, f"case4 stdout unexpected: {out!r}"
            _assert_no_token_leak(out)

            # fixture 5: debug=true 配置
            root4 = tmp / "case4"
            (root4 / ".claude").mkdir(parents=True)
            (root4 / ".claude" / "apifox.json").write_text(
                json.dumps({"apiToken": "afxp_test", "projectId": "55", "debug": True}),
                encoding="utf-8",
            )
            t, p, d = load_config(str(root4))
            assert d is True, "case5 debug should be True"
            out = emit(t, p, d, str(root4))
            assert "APIFOX_DEBUG=1" in out, f"case5 should have APIFOX_DEBUG=1: {out!r}"
            assert "APIFOX_DEBUG_LOG=" in out, f"case5 should have APIFOX_DEBUG_LOG: {out!r}"
            assert "APIFOX_SESSION_ID=" in out, f"case5 should have APIFOX_SESSION_ID: {out!r}"
            assert (root4 / ".claude" / "debug-logs").is_dir(), "case5 debug-logs dir should exist"
            _assert_no_token_leak(out)
        finally:
            # 恢复 env
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
    finally:
        # 清理临时目录
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
        print("Usage: load_config.py <project_root> | -h | --self-test", file=sys.stderr)
        return 2
    project_root = argv[1]
    token, pid, debug = load_config(project_root)
    out = emit(token, pid, debug, project_root)
    # 严禁 print Token。输出中不得含 Token 字符串。
    _assert_no_token_leak(out)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
