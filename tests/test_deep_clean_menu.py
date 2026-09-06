from pathlib import Path


TOOLAPP = Path(__file__).resolve().parents[1] / "toolapp.py"


def test_service_schedule_exposes_deep_clean_settings_and_update():
    source = TOOLAPP.read_text(encoding="utf-8")

    assert "【大掃除】年度設定" in source
    assert "【大掃除】更新VIP通知清單" in source
    assert '"tools.service_management.deep_clean_notice"' in source
    assert '"--master-spreadsheet-id"' in source
    assert '"--mode", "save-settings"' in source
    assert 'cmd += ["--mode", "update-all"]' in source
    assert "失敗原因：{failure_reason}" in source
    assert "請先執行「【大掃除】年度設定」" in source
