import pandas as pd

import batch_booking_safety as safety
from booking_eligibility import can_create_order, can_process_row, create_block_reason


BASE = {'姓名': '王小明', '電話': '0912345678', '地址': '台北市大安區仁愛路一段1號',
        '日期': '2027/01/20', '開始時間': '09:00', '結束時間': '12:00', '服務人時': '定期清潔',
        '狀態': '未安排', '訂單編號': '', '預約條件': ''}


def test_shared_rule_requires_blank_order_unarranged_and_no_hold():
    assert can_create_order(BASE)
    assert can_create_order({**BASE, '預約條件': '可預約：2026大掃除 V 欄已確認'})
    assert not can_create_order({**BASE, '預約條件': '先不約：2026大掃除 V 欄為 FALSE，尚未確認'})
    assert not can_create_order({**BASE, '狀態': '待確認'})
    assert not can_create_order({**BASE, '訂單編號': 'LC1'})
    assert can_create_order({k: v for k, v in BASE.items() if k != '預約條件'})


def test_rows_with_order_still_processed_for_mail_and_calendar():
    assert can_process_row({**BASE, '訂單編號': 'LC1', '預約條件': '先不約：x'})
    assert not can_process_row({**BASE, '預約條件': '先不約：x'})


def test_reason_is_shown_for_skipped_rows():
    assert create_block_reason({**BASE, '狀態': '暫停'}) == '狀態為「暫停」，非未安排'
    assert create_block_reason({**BASE, '預約條件': '先不約：排程 A 欄標記「改」'}).startswith('先不約')


def test_all_batch_runners_share_recover_filter(monkeypatch):
    rows = [{**BASE, '__sheet_row__': 2},
            {**BASE, '__sheet_row__': 3, '預約條件': '先不約：排程 A 欄標記「改」，留待最後成單'},
            {**BASE, '__sheet_row__': 4, '狀態': '待確認'}]
    monkeypatch.setattr(safety, '_configure_runtime', lambda env: None)
    monkeypatch.setattr(safety._orders, 'load_worksheet', lambda name: (object(), pd.DataFrame(rows)))
    monkeypatch.setattr(safety._orders, 'get_region_by_address', lambda address, accounts: '台北')
    monkeypatch.setattr(safety._orders, 'login', lambda session, email, password: True)
    monkeypatch.setattr(safety, '_row_key', lambda row: ('0912345678', 'addr', '2027-01-20', 'p', 'p'))
    monkeypatch.setattr(safety, '_fetch_phone_blocks', lambda session, phone: [])
    logs = []
    _, remaining, recovered, blocked, _ = safety._recover_before_create(
        'dev', '台北', 'e', 'p', '台北202701', 2, 4, None, ['建單'], logs.append)
    assert remaining == [2] and recovered == [] and blocked == []
    assert any('第 3 列不建單：先不約' in line for line in logs)
    assert any('第 4 列不建單：狀態為「待確認」' in line for line in logs)


def test_held_rows_match_filters_used_by_all_three_batches(monkeypatch):
    from types import SimpleNamespace
    import selected_row_status_guard as guard
    hold = '先不約：2026大掃除 V 欄為 FALSE，尚未確認'
    rows = [{**BASE, '__sheet_row__': 2, '預約條件': hold, '原因': '無班表', '結果': '失敗'},
            {**BASE, '__sheet_row__': 3, '預約條件': hold, '原因': '找不到訂單編號', '結果': ''},
            {**BASE, '__sheet_row__': 4, '預約條件': hold, '狀態': '暫停', '結果': ''},
            {**BASE, '__sheet_row__': 5, '預約條件': hold, '訂單編號': 'LC1', '結果': ''},
            {**BASE, '__sheet_row__': 6, '結果': ''}]
    monkeypatch.setattr(guard, '_load_worksheet_unique', lambda name: (None, pd.DataFrame(rows)))
    batch = SimpleNamespace(_text=lambda v: str(v or '').strip(), get_region_by_address=lambda address, accounts: '台北')
    assert guard._held_rows(batch, 's') == [3]
    assert guard._held_rows(batch, 's', allow_failed=True) == [2, 3]
    assert guard._held_rows(batch, 's', 'no_schedule', allow_failed=True) == [2]
    # O 欄沒有單號就算；此表 O 欄空白，可用「（O欄空白）」勾選
    assert guard._held_rows(batch, 's', 'missing_order', allow_failed=True) == [2, 3]
    assert guard._held_rows(batch, 's', 'missing_order', allow_failed=True, o_texts=['（O欄空白）']) == [2, 3]
    assert guard._held_rows(batch, 's', 'missing_order', allow_failed=True, o_texts=['系統未產生新訂單編號']) == []
    assert guard._held_rows(batch, 's', region='台中', allow_failed=True) == []
    assert guard._held_for_request(batch, 's', '台北', rows=[3, 6]) == [3]
    assert guard._held_for_request(batch, 's', '台北', modes=['no_schedule']) == [2]


def test_o_text_options_and_filter_use_current_o_column(monkeypatch):
    from types import SimpleNamespace
    import selected_row_status_guard as guard
    columns = ['服務人時', '備註', '姓名', '電話', '地址', '日期', '開始時間', '結束時間', '狀態', '購買項目', '簡訊', '客人備註',
               '訂單編號', '結果', '原因', '預約條件']
    def row(no, o, **extra):
        values = {**BASE, '原因': o, '結果': '', **extra}
        return [values.get(c, '') for c in columns] + [no]
    rows = [row(2, '系統未產生新訂單編號'), row(3, '無訂單編號'), row(4, ''), row(5, '系統未產生新訂單編號'),
            row(6, 'LC123 回填'), row(7, '無訂單編號', 狀態='暫停')]
    df = pd.DataFrame(rows, columns=columns + ['__sheet_row__'])
    monkeypatch.setattr(guard, '_load_worksheet_unique', lambda name: (None, df))
    batch = SimpleNamespace(_text=lambda v: str(v or '').strip(), get_region_by_address=lambda address, accounts: '台北',
                            REQUIRED_COLUMNS=['姓名', '電話', '地址', '日期', '開始時間', '結束時間', '狀態', '訂單編號'],
                            _date_text=str, _time_text=str)
    assert guard._o_text_options(batch, 's') == [('（O欄空白）', 1), ('系統未產生新訂單編號', 2), ('無訂單編號', 1)]
    assert guard._auto_filter_rows(batch, 's', 'missing_order', allow_failed=True) == [2, 3, 4, 5]
    assert guard._auto_filter_rows(batch, 's', 'missing_order', allow_failed=True, o_texts=['無訂單編號']) == [3]
    assert guard._auto_filter_rows(batch, 's', 'missing_order', allow_failed=True,
                                   o_texts=['（O欄空白）', '系統未產生新訂單編號']) == [2, 4, 5]


def test_o_text_widget_lists_counts_and_returns_selection(monkeypatch):
    from unittest.mock import MagicMock
    import selected_row_status_guard as guard
    st = MagicMock()
    st.session_state = {}
    st.button.return_value = False
    st.multiselect.return_value = ['無訂單編號']
    monkeypatch.setattr(guard, '_o_text_options', lambda *a, **k: [('（O欄空白）', 1), ('無訂單編號', 2)])
    assert guard.render_o_text_filter(st, None, '台北202701', '台北', 'k') == ['無訂單編號']
    label, options = st.multiselect.call_args.args
    assert options == ['（O欄空白）', '無訂單編號'] and '都不選' in label
    assert st.multiselect.call_args.kwargs['format_func']('無訂單編號') == '無訂單編號（2 列）'
    assert guard.render_o_text_filter(st, None, '', '台北', 'k') == []
