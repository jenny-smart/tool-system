import ast
from contextlib import ExitStack
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
    ns = dict(datetime=datetime, ZoneInfo=ZoneInfo, ExitStack=ExitStack, logout_quietly=MagicMock(),
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
    gspread.authorize().open_by_key().worksheet.return_value = worksheet
    target = MagicMock()
    rows = [['2026/09/14', '2026/09/14 10:00:00', '轉帳', 1234, '', 56789, 'note']]
    ns = dict(load_sheet_target=lambda *_: target, Credentials=MagicMock(),
        load_google_credentials=MagicMock(), SCOPES=[], gspread=gspread,
        _normalize_text=lambda v: v, build_fubon_financial_report_rows=lambda _: rows)
    ns.update(AMOUNT_FORMAT={'a': 1}, DATE_FORMAT={'d': 1}, DATETIME_FORMAT={'t': 1}, FUBON_ATM_SHEET_ID='x', Any=object)
    for helper in ('_fubon_formulas', '_yuanta_formulas', 'report_cell_updates'):
        load_function('sheet_filter.py', helper, ns)
    sync = load_function('sheet_filter.py', 'sync_financial_report', ns)
    table = MagicMock(rows=rows)
    assert sync(table, '台北', 'fubon') == 1
    raw, = [c for c in worksheet.batch_update.call_args_list if c.kwargs['value_input_option'] == 'RAW']
    entered, = [c for c in worksheet.batch_update.call_args_list if c.kwargs['value_input_option'] == 'USER_ENTERED']
    assert raw.args[0] == [{'range': 'D3:H3', 'values': [['轉帳', 1234, '', 56789, 'note']]}]
    assert entered.args[0][0] == {'range': 'B3:C3', 'values': [['2026/09/14', '2026/09/14 10:00:00']]}
    formats = worksheet.batch_format.call_args.args[0]
    assert {f['range'] for f in formats} == {'E3', 'G3', 'B3', 'C3'}  # 空白的 F 欄不加格式


def test_report_formulas_follow_jenny_layout():
    ns = dict(AMOUNT_FORMAT={'a': 1}, DATE_FORMAT={'d': 1}, DATETIME_FORMAT={'t': 1},
              FUBON_ATM_SHEET_ID='1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44', Any=object)
    for helper in ('_fubon_formulas', '_yuanta_formulas', 'report_cell_updates'):
        load_function('sheet_filter.py', helper, ns)
    _raw, entered, _fmt = ns['report_cell_updates']('fubon', 2387, [['2026/10/20', '2026/10/19 22:05:09', '網路自轉', '', 3000, 4407053, '中秋禮品']])
    assert entered[1] == {'range': 'K2387:P2387', 'values': [[
        '=TEXT(LEFT(C2387,10),"yyyy/mm/dd")',
        '=if(E2387<>0,text(K2387,"yyyy.mm")&"-"&left(I2387,10),'
        'vlookup(C2387,importrange("1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44","ATM!B:G"),6,false))',
        '=E2387', '=F2387', '=G2387', '=H2387']]}
    raw, entered, fmt = ns['report_cell_updates']('yuanta', 90, [[54, '2026/10/16 10:41:00', '2026/10/16', 'ATM轉', '台北富邦', '', 748791, 793981, '備註']])
    assert raw[1] == {'range': 'D90:I90', 'values': [['ATM轉', '台北富邦', '', 748791, 793981, '備註']]}
    assert entered[1]['values'][0][:2] == ['=TEXT(LEFT(C90,10),"yyyy/mm/dd")', '=text(L90,"YYYY.MM")&"-"&J90']
    assert entered[1]['values'][0][2:] == ['=F90', '=G90', '=H90', '=I90']
    assert 'F90' not in {f['range'] for f in fmt}
