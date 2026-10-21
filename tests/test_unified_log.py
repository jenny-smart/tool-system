from unittest.mock import MagicMock

from tools.common import unified_log


def test_sheet_title_and_row_layout():
    assert unified_log.sheet_title("salary-system", 2027) == "salary-system執行Log-2027"
    row = unified_log.build_row(system="財務管理", function="富邦請款", area="台北", status="completed",
                                message="ok", operator="Jenny")
    assert len(row) == len(unified_log.HEADERS)
    assert row[1:5] == ["財務管理", "富邦請款", "", "台北"] and row[8] == "成功" and row[10] == "Jenny"
    assert unified_log.normalize_result("failed") == "失敗"
    assert unified_log.normalize_result("略過") == "略過"


def test_creates_missing_year_sheet_then_appends(monkeypatch):
    unified_log._ensured.clear()
    service = MagicMock()
    service.spreadsheets().get().execute.return_value = {"sheets": [{"properties": {"title": "其他"}}]}
    unified_log.write(service=service, spreadsheet_id="sid", function="測試", status="成功")
    add = service.spreadsheets().batchUpdate.call_args.kwargs["body"]["requests"][0]["addSheet"]
    assert add["properties"]["title"] == unified_log.sheet_title()
    append = service.spreadsheets().values().append.call_args.kwargs
    assert append["range"] == f"'{unified_log.sheet_title()}'!A:K"


def test_write_never_raises():
    service = MagicMock()
    service.spreadsheets().get().execute.side_effect = RuntimeError("no access")
    unified_log._ensured.clear()
    unified_log.write(service=service, spreadsheet_id="sid", function="測試")  # 不拋例外


def test_agent_failure_message_includes_error_line(monkeypatch):
    from tools import local_agent

    calls = []
    monkeypatch.setattr(unified_log, "write", lambda **kw: calls.append(kw))
    task = {"action": "fubon.download", "task_id": "t1", "created_by": "排程系統",
            "params_json": '{"area": "台北", "start_date": "2026-10-20"}'}
    log = "START\nTraceback\nRuntimeError: 富邦登入逾時\n[2026] FAILED x"
    local_agent._unified_task_log(task, "failed", "執行失敗（exit 1）", log)
    assert calls[0]["area"] == "台北" and calls[0]["period"] == "2026-10-20"
    assert "富邦登入逾時" in calls[0]["message"] and calls[0]["operator"] == "排程系統"
    assert calls[0]["function"] == "【富邦銀行】富邦明細下載" and calls[0]["source_file"] == "fubon.download"


def test_every_registered_agent_action_has_chinese_label():
    import re
    from pathlib import Path
    from tools import local_agent

    source = Path(local_agent.__file__).read_text(encoding="utf-8")
    actions = re.findall(r'^register_action\("([^"]+)"', source, re.M)
    assert actions and not [a for a in actions if a not in local_agent.ACTION_LABELS]


def _fake_st():
    import types

    return types.SimpleNamespace(session_state={}, button=lambda label, **kw: label == "▶️ 執行",
                                 success=lambda body, **kw: body, error=lambda body, **kw: body)


def test_web_click_logs_start_then_first_success(monkeypatch):
    calls = []
    monkeypatch.setattr(unified_log, "write", lambda **kw: calls.append(kw))
    st = _fake_st()
    unified_log.install_streamlit_hooks(st, system="財務管理", function="【財報】各區固定費用匯款", operator="Jenny")
    unified_log.install_streamlit_hooks(st, system="財務管理", function="【財報】各區固定費用匯款", operator="Jenny")
    unified_log.set_context(st, area="台中")
    assert st.button("取消") is False
    assert st.button("▶️ 執行") is True
    unified_log.report(st, "success", "完成 3 筆")
    unified_log.report(st, "success", "第二則成功訊息不重複記")
    assert [c["status"] for c in calls] == ["執行", "成功"]
    assert calls[0]["function"] == "【財報】各區固定費用匯款／▶️ 執行" and calls[0]["area"] == "台中"
    assert calls[1]["message"] == "完成 3 筆" and calls[1]["operator"] == "Jenny"


def test_errors_capped_and_ignored_without_click(monkeypatch):
    calls = []
    monkeypatch.setattr(unified_log, "write", lambda **kw: calls.append(kw))
    st = _fake_st()
    unified_log.install_streamlit_hooks(st, system="日排程系統", function="日排程")
    st.error("沒按按鈕前的錯誤不記")
    st.button("▶️ 執行")
    for i in range(5):
        st.error(f"錯誤 {i}")
    assert [c["status"] for c in calls] == ["執行", "失敗", "失敗", "失敗"]


def test_agent_labels_match_web_menu_names():
    import re
    from pathlib import Path
    from tools import local_agent

    menu_names = set(re.findall(r'"name": "([^"]+)", "handler"',
                                (Path(local_agent.__file__).parents[1] / "toolapp.py").read_text(encoding="utf-8")))
    # 由網頁選單排入的任務，Log 名稱要跟選單一致（配號任務以前誤標成「字軌區段查詢」）
    for action in ("cetustek.serial_section_query", "cetustek.allowance", "fubon.payment_request",
                   "newebpay.refund_pending", "mofei.download", "yuanta.salary_status"):
        assert local_agent.ACTION_LABELS[action] in menu_names, action
