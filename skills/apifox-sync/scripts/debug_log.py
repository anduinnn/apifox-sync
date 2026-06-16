#!/usr/bin/env python3
"""debug 日志共用模块：结构化 JSONL 日志写入、Token 脱敏、执行摘要。

用法：
    python3 debug_log.py --summary <log_path>
    python3 debug_log.py --format-entry --session-id <id> --step <step> --status <status> [--duration-ms <ms>] [--input-summary <s>] [--output-summary <s>] [--error-detail <s>] [--http-status <code>] [--command <cmd>]
    python3 debug_log.py --self-test

模块导入：
    from debug_log import debug_log, generate_session_id, print_summary

env:
    APIFOX_DEBUG_LOG   日志文件路径（debug_log() 检测此变量，无则静默返回）
    APIFOX_SESSION_ID  当前 session ID
    APIFOX_API_TOKEN   用于 Token 脱敏（仅读取用于替换，不写入日志）

退出码：0 成功 / 1 运行时错误 / 2 参数用法错误
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    "session_id", "timestamp", "step", "status", "duration_ms",
    "input_summary", "output_summary", "error_detail", "http_status", "command",
)


def _sanitize_token(text: str | None) -> str | None:
    if text is None:
        return None
    token = os.environ.get("APIFOX_API_TOKEN", "")
    if token and len(token) > 8 and token in text:
        text = text.replace(token, "***")
    return text


def generate_session_id(command: str = "unknown") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = secrets.token_hex(4)
    return f"{ts}-{command}-{short}"


def debug_log(
    step: str,
    status: str,
    duration_ms: int,
    *,
    input_summary: str | None = None,
    output_summary: str | None = None,
    error_detail: str | None = None,
    http_status: int | None = None,
    command: str | None = None,
) -> None:
    log_path = os.environ.get("APIFOX_DEBUG_LOG")
    if not log_path:
        return
    entry = {
        "session_id": os.environ.get("APIFOX_SESSION_ID", "unknown"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
        "step": step,
        "status": status,
        "duration_ms": duration_ms,
        "input_summary": _sanitize_token(input_summary),
        "output_summary": _sanitize_token(output_summary),
        "error_detail": _sanitize_token(error_detail),
        "http_status": http_status,
        "command": _sanitize_token(command),
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def format_entry(
    session_id: str,
    step: str,
    status: str,
    duration_ms: int = 0,
    input_summary: str | None = None,
    output_summary: str | None = None,
    error_detail: str | None = None,
    http_status: int | None = None,
    command: str | None = None,
) -> str:
    entry = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
        "step": step,
        "status": status,
        "duration_ms": duration_ms,
        "input_summary": _sanitize_token(input_summary),
        "output_summary": _sanitize_token(output_summary),
        "error_detail": _sanitize_token(error_detail),
        "http_status": int(http_status) if http_status is not None else None,
        "command": _sanitize_token(command),
    }
    return json.dumps(entry, ensure_ascii=False)


def print_summary(log_path: str) -> str:
    lines = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return f"[摘要] 无法读取日志文件: {log_path}"

    if not lines:
        return "[摘要] 日志文件为空"

    first = lines[0]
    session_id = first.get("session_id", "unknown")
    cmd = session_id.split("-")[2] if session_id.count("-") >= 2 else "unknown"
    total_ms = sum(e.get("duration_ms", 0) or 0 for e in lines)
    failed_steps = [e for e in lines if e.get("status") == "error"]
    overall = "失败" if failed_steps else "成功"

    parts = []
    ts_display = first.get("timestamp", "")[:19].replace("T", " ")
    parts.append(f"[{ts_display}] {cmd} 执行摘要")
    if failed_steps:
        parts.append(f"状态: 失败 (步骤 {failed_steps[0].get('step', '?')})")
    else:
        parts.append("状态: 成功")
    parts.append(f"总耗时: {total_ms / 1000:.1f}s")
    parts.append("步骤:")
    for e in lines:
        st = e.get("status", "?")
        step = e.get("step", "?")
        dur = e.get("duration_ms", 0) or 0
        mark = "✓" if st == "success" else ("✗" if st == "error" else "-")
        detail = ""
        if st == "error" and e.get("error_detail"):
            detail = f" - {e['error_detail'][:80]}"
        parts.append(f"  {mark} {step} ({dur / 1000:.1f}s){detail}")
    parts.append(f"日志文件: {log_path}")
    return "\n".join(parts)


def _cli_format_entry(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="debug_log.py --format-entry")
    p.add_argument("--session-id", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--duration-ms", type=int, default=0)
    p.add_argument("--input-summary", default=None)
    p.add_argument("--output-summary", default=None)
    p.add_argument("--error-detail", default=None)
    p.add_argument("--http-status", type=int, default=None)
    p.add_argument("--command", default=None)
    args = p.parse_args(argv)
    line = format_entry(
        session_id=args.session_id, step=args.step, status=args.status,
        duration_ms=args.duration_ms, input_summary=args.input_summary,
        output_summary=args.output_summary, error_detail=args.error_detail,
        http_status=args.http_status, command=args.command,
    )
    print(line)
    return 0


def self_test() -> int:
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="apifox-sync-selftest-debuglog-"))
    try:
        # case 1: debug_log 写入 + 10 字段完整性
        log = tmp / "test.jsonl"
        os.environ["APIFOX_DEBUG_LOG"] = str(log)
        os.environ["APIFOX_SESSION_ID"] = "20260616-103000-pull-a1b2c3d4"
        debug_log("pull.export_api", "success", 1200, input_summary="3 folders", output_summary="OK")
        debug_log("pull.pull_diff", "error", 500, error_detail="FileNotFoundError: diff.json")
        assert log.exists(), "日志文件应已创建"
        with open(log, "r", encoding="utf-8") as f:
            entries = [json.loads(l) for l in f]
        assert len(entries) == 2, f"应有 2 条，实际 {len(entries)}"
        for e in entries:
            for field in FIELDS:
                assert field in e, f"缺少字段: {field}"
            assert len(e) == 10, f"应恒定 10 字段，实际 {len(e)}"

        # case 2: Token 脱敏
        os.environ["APIFOX_API_TOKEN"] = "afxp_secret_token_value_12345"
        debug_log("push.import", "success", 300, command="curl -s afxp_secret_token_value_12345 https://api.apifox.com")
        with open(log, "r", encoding="utf-8") as f:
            last = json.loads(f.readlines()[-1])
        assert "afxp_" not in last["command"], f"Token 泄露: {last['command']}"
        assert "***" in last["command"], "Token 应被替换为 ***"

        # case 3: APIFOX_DEBUG_LOG 未设置时静默
        del os.environ["APIFOX_DEBUG_LOG"]
        line_count_before = sum(1 for _ in open(log))
        debug_log("should.skip", "success", 0)
        line_count_after = sum(1 for _ in open(log))
        assert line_count_before == line_count_after, "无 DEBUG_LOG 时不应写入"

        # case 4: generate_session_id 格式
        sid = generate_session_id("pull")
        parts = sid.split("-")
        assert len(parts) == 4, f"session_id 格式错误: {sid}"
        assert parts[2] == "pull", f"command 段错误: {parts[2]}"
        assert len(parts[3]) == 8, f"hex 段应 8 字符: {parts[3]}"

        # case 5: format_entry 输出合法 JSON + 10 字段
        line = format_entry("test-id", "pull.extract", "success", duration_ms=100)
        parsed = json.loads(line)
        assert len(parsed) == 10, f"format_entry 应 10 字段，实际 {len(parsed)}"
        for field in FIELDS:
            assert field in parsed, f"format_entry 缺少: {field}"

        # case 6: print_summary 生成摘要
        os.environ["APIFOX_DEBUG_LOG"] = str(log)
        summary = print_summary(str(log))
        assert "执行摘要" in summary, "摘要应含标题"
        assert "✓" in summary, "成功步骤应有 ✓"
        assert "✗" in summary, "失败步骤应有 ✗"

        # case 7: format_entry Token 脱敏
        line = format_entry("test-id", "push.curl", "success", command="curl afxp_secret_token_value_12345 url")
        parsed = json.loads(line)
        assert "afxp_" not in parsed["command"], "format_entry 也应脱敏 Token"

    finally:
        os.environ.pop("APIFOX_DEBUG_LOG", None)
        os.environ.pop("APIFOX_SESSION_ID", None)
        os.environ.pop("APIFOX_API_TOKEN", None)
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST_OK")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) >= 2 and argv[1] == "--summary":
        if len(argv) < 3:
            print("Usage: debug_log.py --summary <log_path>", file=sys.stderr)
            return 2
        summary = print_summary(argv[2])
        print(summary)
        return 0
    if len(argv) >= 2 and argv[1] == "--format-entry":
        return _cli_format_entry(argv[2:])
    if len(argv) >= 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    print("Usage: debug_log.py --self-test | --summary <path> | --format-entry ... | -h", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
