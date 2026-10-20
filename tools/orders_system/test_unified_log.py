import types
from unittest.mock import MagicMock, patch

import unified_log


def test_row_layout_matches_other_systems():
    row = unified_log.build_row(function="建立訂單／🚀 建立新客訂單", status="執行")
    assert len(row) == len(unified_log.HEADERS) == 11
    assert row[1:4] == ["訂單系統", "建立訂單／🚀 建立新客訂單", "手動"]
    assert unified_log.sheet_title(2027) == "orders-system執行Log-2027"


def test_button_click_and_error_are_logged_once_per_install():
    fake_st = types.SimpleNamespace(button=lambda label, **kw: label == "執行", error=lambda body, **kw: body,
                                    session_state={})
    with patch.object(unified_log, "write") as write:
        unified_log.install_streamlit_hooks(fake_st, "建立訂單", "Jenny")
        unified_log.install_streamlit_hooks(fake_st, "建立訂單", "Jenny")  # rerun 不重複包裝
        assert fake_st.button("不按") is False
        assert fake_st.button("執行") is True
        fake_st.error("後台登入失敗")
    statuses = [c.kwargs["status"] for c in write.call_args_list]
    assert statuses == ["執行", "失敗"]
    assert write.call_args.kwargs["message"] == "後台登入失敗"
    assert write.call_args.kwargs["function"] == "建立訂單／執行"


def test_write_never_raises():
    client = MagicMock()
    client.open_by_key.side_effect = RuntimeError("no access")
    unified_log.write(client=client, function="測試")
