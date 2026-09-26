from pathlib import Path


def _agent_help_source() -> str:
    source = Path("toolapp.py").read_text(encoding="utf-8")
    start = source.index("def render_agent_help")
    end = source.index("# Google Drive OAuth", start)
    return source[start:end]


def test_agent_help_uses_buttons_instead_of_terminal_snippets():
    source = _agent_help_source()
    assert "🔄 重啟 Agent" in source
    assert "📊 檢查狀態" in source
    assert "📋 查看 Agent Log" in source
    assert "⬇️ 更新程式＋重啟 Agent" in source
    assert "./scripts/local_agent_service.sh restart" not in source
    assert "./scripts/local_agent_service.sh logs" not in source


def test_prize_update_requires_loaded_agent_action():
    source = Path("toolapp.py").read_text(encoding="utf-8")
    assert 'if "cetustek.prize_update" not in actions:' in source
    assert "Local Agent 尚未載入「中獎發票更新」新版" in source
