from pathlib import Path


def test_agent_help_keeps_unconfirmed_tasks_cancellable():
    """2026-10-02 起改用「Agent 是否在線」判斷：沒有心跳不代表工作已停止，
    不在線 Agent 的工作仍顯示為「尚未確認執行端的工作」並保留中止按鈕，不再依心跳時間忽略。"""
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    help_source = source[start:end]
    assert "newest_heartbeat" not in help_source
    assert 'str(task.get("agent_id") or "") not in online_agent_ids' in help_source
    assert "尚未確認執行端的工作" in help_source
