import ast
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'tools/bank_statement'


def load_function(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('kind', ['payment_request', 'deposit_refund'])
@pytest.mark.parametrize('completed', [True, False])
def test_download_only_after_all_payments(kind, completed):
    events = []
    item = dict(sheet_row=2, name='name', amount='100', memo='memo')
    service = MagicMock()
    service.spreadsheets().values().update().execute.side_effect = lambda: events.append('write')
    def wait(*args, **kwargs):
        if not completed:
            raise RuntimeError('not completed')
        events.append('paid')
        return '2026-09-14 10:20:30'
    ns = dict(datetime=datetime, ZoneInfo=ZoneInfo,
        PAYMENT_REQUEST_TYPE='request', DEPOSIT_REFUND_TYPE='deposit',
        read_report_values=MagicMock(), pending_payment_requests=lambda _: [item, item],
        pending_deposit_refunds=lambda _: [item, item], load_account=MagicMock(),
        resolve_report_location=lambda *_: ('sid', 'title'), get_sheets_service=lambda: service,
        sync_playwright=MagicMock(), connect_existing_chrome=lambda *_: (None, MagicMock()),
        ensure_login=MagicMock(), fill_payment_request=MagicMock(), fill_deposit_refund=MagicMock(),
        wait_user_completed_transfer=wait, current_fubon_page=MagicMock(),
        read_destination_name=MagicMock(), verify_writeback_access=MagicMock(),
        write_payment_date=lambda *_: events.append('write'),
        run_download=MagicMock(side_effect=lambda *_: events.append('download')))
    run = load_function('fubon_' + kind + '.py', 'run', ns)
    if completed:
        assert run('台北', {2}, Path('.'), 'cdp') == 0
        assert events == ['paid', 'write', 'paid', 'write', 'download']
        args = ns['run_download'].call_args.args
        assert args[-2:] == (datetime.now(ZoneInfo('Asia/Taipei')).date(),) * 2
    else:
        with pytest.raises(RuntimeError, match='not completed'):
            run('台北', {2}, Path('.'), 'cdp')
        assert events == []


def test_fubon_report_formats_only_written_amount_cells():
    worksheet = MagicMock()
    worksheet.get.return_value = [['header'], ['', '2026/09/13']]
    gspread = MagicMock()
    gspread.authorize().open_by_key().get_worksheet_by_id.return_value = worksheet
    target = MagicMock()
    rows = [['2026/09/14', '2026/09/14 10:00:00', '轉帳', 1234, '', 56789, 'note']]
    ns = dict(load_sheet_target=lambda *_: target, Credentials=MagicMock(),
        load_google_credentials=MagicMock(), SCOPES=[], gspread=gspread,
        _normalize_text=lambda v: v, build_fubon_financial_report_rows=lambda _: rows)
    sync = load_function('sheet_filter.py', 'sync_financial_report', ns)
    table = MagicMock(rows=rows)
    assert sync(table, '台北', 'fubon') == 1
    worksheet.update.assert_called_once_with(range_name='B3:H3', values=rows, value_input_option='RAW')
    worksheet.format.assert_called_once_with('E3:G3', {
        'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'},
        'horizontalAlignment': 'RIGHT'})
