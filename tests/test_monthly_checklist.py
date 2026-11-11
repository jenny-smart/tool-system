"""月度盤點：狀態判斷、寫回分頁（保留完成時間、手動列不動）、過期提醒。"""
from datetime import date, timedelta

from tools.finance_management import monthly_checklist as mc


def test_item_status():
    today = date.today()
    assert mc.item("a", "A", today, True, "")["status"] == mc.DONE
    assert mc.item("a", "A", today - timedelta(days=1), False, "")["status"] == mc.TODO
    assert mc.item("a", "A", today + timedelta(days=3), False, "")["status"] == mc.NOT_DUE
    assert mc.item("a", "A", today, False, "", running=True)["status"] == mc.RUNNING
    assert mc.item("a", "A", today, False, "", runnable=False)["status"] == mc.MANUAL
    skip = mc.item("a", "A", today, False, "", applicable=False)
    assert skip["status"] == mc.SKIP and not skip["runnable"]
    assert mc.item("a", "A", today, True, "")["runnable"] is False


def test_odd_even_month_items():
    assert mc.accounting_item("202612")["status"] == mc.SKIP  # 雙月沒有會計事務所費用
    assert mc.mof_item("202611", skip_month=True)["status"] == mc.SKIP  # 排程設定不是這個月做


def test_mof_skip_month_follows_schedule_frequency(monkeypatch):
    from tools.scheduling import schedule_settings as ss

    def rule(freq):
        return ss.parse_row([True, "mof_serial", "x", "10:00", freq, "20", "提前", ss.MODE_QUEUE])

    monkeypatch.setattr(ss, "get_rule", lambda *_a, **_k: rule("每月"))
    assert not mc._mof_skip_month("202611") and not mc._mof_skip_month("202612")
    monkeypatch.setattr(ss, "get_rule", lambda *_a, **_k: rule("雙月"))
    assert mc._mof_skip_month("202611") and not mc._mof_skip_month("202612")


def test_merge_rows_keeps_done_time_note_and_manual_rows():
    existing = [mc.HEADER,
                ["202611", "deposit_report", "押金", "2026-11-01", mc.DONE, "ok", "t0", "2026-11-01 04:01", "我的備註"],
                ["202611", "", "手動：寄資料給會計", "2026-11-10", "", "", "", "", ""],
                ["202610", "review_monthly", "review", "2026-10-01", mc.TODO, "x", "t0", "", ""]]
    items = [mc.item("deposit_report", "押金", date(2026, 11, 1), True, "ok"),
             mc.item("review_monthly", "review", date(2026, 11, 1), True, "done"),
             mc.item("fixed_expense_mail", "查信", date(2026, 11, 10), False, "還沒新增")]
    updates, appends = mc.merge_rows(existing, "202611", items, "NOW")
    assert updates == [[2, ["202611", "deposit_report", "押金", "2026-11-01", mc.DONE, "ok", "NOW",
                            "2026-11-01 04:01", "我的備註"]]]
    # 202611 的 review 是新的一列（202610 那列不動）；第一次完成記完成時間；未完成沒有完成時間
    assert appends[0][:2] == ["202611", "review_monthly"] and appends[0][7] == "NOW"
    assert appends[1][:2] == ["202611", "fixed_expense_mail"] and appends[1][7] == ""


def test_overdue_only_past_due_unfinished():
    today = date(2026, 11, 12)
    items = [mc.item("a", "A", date(2026, 11, 10), False, ""), mc.item("b", "B", date(2026, 11, 20), False, ""),
             mc.item("c", "C", date(2026, 11, 1), True, "")]
    items[1]["status"] = mc.NOT_DUE
    items[0]["status"] = mc.TODO
    assert [i["key"] for i in mc.overdue(items, today)] == ["a"]
