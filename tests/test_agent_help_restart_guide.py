from pathlib import Path


def _agent_help_source() -> str:
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    return source[start:end]


def test_agent_help_uses_background_supervisor_commands():
    source = _agent_help_source()

    assert "./scripts/local_agent_service.sh restart" in source
    assert "./scripts/local_agent_service.sh status" in source
    assert "./scripts/local_agent_service.sh logs" in source
    assert "5 秒後自動重啟" in source


def test_agent_help_does_not_show_obsolete_foreground_start():
    source = _agent_help_source()

    assert "start-local-agent.sh" not in source
    assert "保持該 Terminal 視窗開啟" not in source
    assert "Control + C" in source
    assert "⏹️ 中止目前工作" in source
    assert "_request_local_agent_task_cancel_raw" in source
    assert "離線超過 30 秒才執行" in source
