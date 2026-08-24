"""崩溃记录：本地优先，不向任何第三方发送。

CLI 未捕获异常时此前只打一个裸 traceback 就退出，什么都不留——用户来报 bug
时能提供的往往只有"它崩了"，版本、provider、走到哪一步全部丢失。

为什么不直接接 Sentry：aria-code 是 local-first 的终端工具，堆栈里可能带着
文件路径、项目名、提示词片段。默认把这些发到第三方服务器跟产品定位相悖。
"""

from __future__ import annotations

import json

from aria_code.runtime.crash_report import recent_crashes, redact, write_crash_report


def test_obvious_key_shapes_are_redacted():
    for secret in (
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "ghp_abcdefghijklmnopqrstuvwxyz1234",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
    ):
        assert secret not in redact(f"failed with key={secret} at line 3")


def test_report_captures_context_without_leaking_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ARIA_FAKE_TOKEN", "sk-abcdefghijklmnopqrstuvwxyz123456")
    monkeypatch.setenv("ARIA_BUDGET_MAX_USD", "2.0")
    monkeypatch.setenv("UNRELATED_PROJECT_SECRET", "should-not-be-collected")

    try:
        raise ValueError("boom sk-abcdefghijklmnopqrstuvwxyz123456")
    except ValueError as exc:
        path = write_crash_report(exc, context={"provider": "openai", "round": 3})

    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["error_type"] == "ValueError"
    assert "sk-abcdefg" not in data["error"], "错误消息里的 key 未打码"
    assert "sk-abcdefg" not in data["traceback"], "traceback 里的 key 未打码"

    # 疑似密钥的环境变量只记长度，不记值
    assert data["env"]["ARIA_FAKE_TOKEN"].startswith("<set:")
    # 非密钥的 aria 变量照常记录，便于复现
    assert data["env"]["ARIA_BUDGET_MAX_USD"] == "2.0"
    # 不相关的环境变量根本不收集——收进来只是扩大暴露面
    assert "UNRELATED_PROJECT_SECRET" not in data["env"]

    assert data["context"]["provider"] == "openai"


def test_writer_never_raises_even_when_target_is_unwritable(tmp_path, monkeypatch):
    """崩溃记录器自己再崩一次，只会把原始错误从屏幕上冲掉，让排查更难。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "runtime.crash_report._crash_dir",
        lambda: (_ for _ in ()).throw(OSError("disk on fire")),
    )
    try:
        raise RuntimeError("original failure")
    except RuntimeError as exc:
        assert write_crash_report(exc) is None      # 返回 None，不抛


def test_only_recent_reports_are_kept(tmp_path, monkeypatch):
    """崩溃记录是排查素材，不是需要永久保存的资产；无上限增长的日志目录
    本身就会变成一个问题。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    for i in range(25):
        try:
            raise ValueError(f"failure {i}")
        except ValueError as exc:
            write_crash_report(exc)

    kept = list((tmp_path / ".aria-code" / "crashes").glob("crash-*.json"))
    assert len(kept) <= 20, f"保留了 {len(kept)} 份，应当被裁剪到 20"
    assert recent_crashes(limit=3), "recent_crashes 应能读出摘要"


def test_keyboard_interrupt_is_not_recorded(tmp_path, monkeypatch):
    """用户主动中断不是故障，记下来只会污染真实崩溃的记录。"""
    import sys
    from runtime.crash_report import install_excepthook

    monkeypatch.setenv("HOME", str(tmp_path))
    original = sys.excepthook
    try:
        install_excepthook()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        crash_dir = tmp_path / ".aria-code" / "crashes"
        assert not crash_dir.exists() or not list(crash_dir.glob("crash-*.json"))
    finally:
        sys.excepthook = original
