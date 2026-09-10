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
    assert "💾 儲存年度設定" in source
    assert "加價未定時可先填 0" in source
    assert "PART 1 VIP 平日加價" in source
    assert "PART 1 非VIP 平日加價" in source
    assert "PART 2 VIP 週六＋週日加價" in source
    assert "PART 2 非VIP 週六＋週日加價" in source
    assert '"--phase1-nonvip-weekday-rate"' in source
    assert '"--phase2-nonvip-weekend-rate"' in source
