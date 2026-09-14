import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'tools/bank_statement/fubon_supply_purchase.py'


def run_purchase(*, completion_error=False, write_error=False):
    tree = ast.parse(SOURCE.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    service = MagicMock()
    if write_error:
        service.spreadsheets().values().batchUpdate().execute.side_effect = OSError('failed')
    wait = MagicMock(return_value='2026-09-14 10:20:30')
    if completion_error:
        wait.side_effect = RuntimeError('not completed')
    item = dict(sheet_row=2, rows=[2, 5], supplier='supplier', bank_code='012', account_number='123', amount='300')
    ns = dict(Path=Path, datetime=datetime, SUPPLY_PURCHASE_TYPE='清潔用品採購',
              read_report_values=MagicMock(return_value=[]),
              pending_supply_purchases=MagicMock(return_value=[item]),
              resolve_report_location=MagicMock(return_value=('sheet-id', '進貨明細表-台北')),
              get_sheets_service=lambda: service,
              load_account=MagicMock(return_value=SimpleNamespace(bank_account='source')),
              sync_playwright=MagicMock(), connect_existing_chrome=MagicMock(return_value=(None, MagicMock())),
              ensure_login=MagicMock(), fill_supply_purchase=MagicMock(),
              wait_user_completed_transfer=wait, current_fubon_page=MagicMock())
    exec(compile(ast.Module(body=[run], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, service


def test_last_payment_writes_date_to_every_grouped_row():
    ns, service = run_purchase()
    assert ns['run']('台北', '202609', {2}, Path('.'), 'cdp') == 0
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


def test_write_failure_stops_with_paid_warning():
    ns, _ = run_purchase(write_error=True)
    with pytest.raises(RuntimeError, match='付款已完成.*回填失敗'):
        ns['run']('台北', '202609', {2}, Path('.'), 'cdp')
    ns['current_fubon_page'].assert_not_called()
