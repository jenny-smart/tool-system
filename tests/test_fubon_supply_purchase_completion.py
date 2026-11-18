import ast
from contextlib import ExitStack
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'tools/bank_statement/fubon_supply_purchase.py'


class SavedAccountNotFound(RuntimeError):
    pass


def load_destination_helper():
    tree = ast.parse(SOURCE.read_text())
    future = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == '__future__')
    helper = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == '_choose_supply_purchase_destination'
    )
    ns = dict(
        Page=object,
        SavedAccountNotFound=SavedAccountNotFound,
        choose_saved_destination=MagicMock(),
        choose_manual_destination=MagicMock(),
        fill_manual_destination=MagicMock(),
    )
    exec(compile(ast.Module(body=[future, helper], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def test_supply_purchase_prefers_saved_account_with_same_supplier_name():
    ns = load_destination_helper()
    page = MagicMock()
    item = dict(supplier='LINE酵素', bank_code='807', account_number='11601800014602')

    ns['_choose_supply_purchase_destination'](page, item)

    ns['choose_saved_destination'].assert_called_once_with(page, 'LINE酵素', account_number='11601800014602')
    ns['choose_manual_destination'].assert_not_called()
    ns['fill_manual_destination'].assert_not_called()


def test_supply_purchase_falls_back_to_report_account_when_name_and_account_are_missing():
    ns = load_destination_helper()
    page = MagicMock()
    item = dict(supplier='新廠商', bank_code='013', account_number='214502127659')
    ns['choose_saved_destination'].side_effect = SavedAccountNotFound('missing')

    ns['_choose_supply_purchase_destination'](page, item)

    ns['choose_saved_destination'].assert_called_once_with(page, '新廠商', account_number='214502127659')
    ns['choose_manual_destination'].assert_called_once_with(page)
    page.wait_for_timeout.assert_called_once_with(800)
    ns['fill_manual_destination'].assert_called_once_with(page, '013', '214502127659')


def run_purchase(*, completion_error=False, write_error=False):
    tree = ast.parse(SOURCE.read_text())
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('prepare', 'pay', 'run')]
    service = MagicMock()
    if write_error:
        service.spreadsheets().values().batchUpdate().execute.side_effect = OSError('failed')
    wait = MagicMock(return_value='2026-09-14 10:20:30')
    if completion_error:
        wait.side_effect = RuntimeError('not completed')
    item = dict(sheet_row=2, rows=[2, 5], supplier='supplier', bank_code='012', account_number='123', amount='300')
    ns = dict(report_query_start=lambda account, end: end, Path=Path, datetime=datetime, ZoneInfo=ZoneInfo, Page=object, ExitStack=ExitStack, logout_quietly=MagicMock(), run_download=MagicMock(), SUPPLY_PURCHASE_TYPE='清潔用品採購',
              read_report_values=MagicMock(return_value=[]),
              pending_supply_purchases=MagicMock(return_value=[item]),
              split_groups=lambda values, month, groups, split_rows: groups,
              resolve_report_location=MagicMock(return_value=('sheet-id', '進貨明細表-台北')),
              get_sheets_service=lambda: service,
              load_account=MagicMock(return_value=SimpleNamespace(bank_account='source')),
              sync_playwright=MagicMock(), connect_existing_chrome=MagicMock(return_value=(None, MagicMock())),
              ensure_login=MagicMock(), fill_supply_purchase=MagicMock(),
              wait_user_completed_transfer=wait, current_fubon_page=MagicMock())
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, service


def test_last_payment_writes_date_to_every_grouped_row():
    ns, service = run_purchase()
    assert ns['run']('台北', '202609', {2}, Path('.'), 'cdp') == 0
    ns['run_download'].assert_called_once()
    ns['wait_user_completed_transfer'].assert_called_once()
    assert ns['wait_user_completed_transfer'].call_args.kwargs == dict(
        require_completed_at=True, expected_amount='300', expected_account='123')
    body = service.spreadsheets().values().batchUpdate.call_args.kwargs['body']
    assert body['data'] == [
        {'range': "'進貨明細表-台北'!Q2", 'values': [['2026/09/14']]},
        {'range': "'進貨明細表-台北'!Q5", 'values': [['2026/09/14']]},
    ]


def test_unconfirmed_payment_does_not_write_date():
    ns, service = run_purchase(completion_error=True)
    with pytest.raises(RuntimeError, match='not completed'):
        ns['run']('台北', '202609', {2}, Path('.'), 'cdp')
    service.spreadsheets().values().batchUpdate.assert_not_called()
    ns['run_download'].assert_not_called()


def test_write_failure_stops_with_paid_warning():
    ns, _ = run_purchase(write_error=True)
    with pytest.raises(RuntimeError, match='付款已完成.*回填失敗'):
        ns['run']('台北', '202609', {2}, Path('.'), 'cdp')
    ns['current_fubon_page'].assert_not_called()
    ns['run_download'].assert_not_called()


def test_select_card_falls_back_to_same_account_and_closes_popup_when_nothing_matches():
    """常用帳號清單先找名字、再找帳號；都沒有時要關掉帳號清單視窗（Escape 關不掉）再自行輸入。"""
    import ast as _ast
    from pathlib import Path as _Path

    source = _Path("tools/bank_statement/fubon_transfer_common.py").read_text(encoding="utf-8")
    tree = _ast.parse(source)
    select = next(n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name == "_select_account_card")
    body = _ast.get_source_segment(source, select)
    assert "targets = [t for t in (target_text, *alt_texts) if t]" in body
    assert "close_account_popup(page)" in body
    choose = next(n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name == "choose_saved_destination")
    assert "alt_texts=alt" in _ast.get_source_segment(source, choose)


def test_prepare_pays_each_row_and_merges_only_marked_same_account_rows():
    from tools.bank_statement import fubon_supply_purchase_filter as flt

    tree = ast.parse(SOURCE.read_text())
    future = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == '__future__')
    prepare = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare')

    def row(amount, supplier, bank, account):
        r = [""] * 19
        r[1], r[5], r[10], r[13], r[14] = "2026.11", supplier, amount, bank, account
        return r

    values = [["h"], row("780", "福吉", "808(玉山銀行)", "0129"), row("1680", "福吉", "808(玉山銀行)", "0129"),
              row("4800", "檸檬嚴選", "806(元大銀行)", "2034")]
    ns = dict(read_report_values=lambda *_: values, SUPPLY_PURCHASE_TYPE='清潔用品採購',
              pending_supply_purchases=flt.pending_supply_purchases, merge_by_account=flt.merge_by_account,
              split_groups=flt.split_groups, resolve_report_location=lambda *_: ('sid', 'title'),
              get_sheets_service=lambda: None)
    exec(compile(ast.Module(body=[future, prepare], type_ignores=[]), str(SOURCE), 'exec'), ns)

    picked = ns['prepare']('台北', '202611', {2, 3, 4}, merge_rows=set())['selected']
    assert [(m['rows'], m['amount']) for m in picked] == [([2], '780'), ([3], '1680'), ([4], '4800')]
    picked = ns['prepare']('台北', '202611', {2, 3, 4}, merge_rows={2, 3})['selected']
    assert [(m['rows'], m['amount']) for m in picked] == [([2, 3], '2460'), ([4], '4800')]
    # 單獨頁面（沒給 merge_rows）沿用依帳號合併後的筆
    picked = ns['prepare']('台北', '202611', {2, 4})['selected']
    assert [(m['rows'], m['amount']) for m in picked] == [([2, 3], '2460'), ([4], '4800')]
