import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from tools.invoice_center import invoice_archive as archive
from tools.invoice_center.ei_export_all import detect_download_format, archive_as_zip, _check_prize_option
from tools.invoice_center.prize_customers import customer_from_html


def workbook(rows, sheet='發票中獎資料'):
    buffer = io.BytesIO()
    pd.DataFrame(rows, columns=archive.PRIZE_HEADERS).to_excel(buffer, sheet_name=sheet, index=False)
    return buffer.getvalue()


ROW = ['115/07/29', 'DM51791397', 3600, '四獎', 4000, '', '載具', '', '', '']


@pytest.mark.parametrize('wrapped', [False, True])
def test_excel_inside_zip_and_excel_renamed_zip(tmp_path, wrapped):
    path = tmp_path / 'prize.zip'
    if wrapped:
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('清冊.xlsx', workbook([ROW]))
    else:
        path.write_bytes(workbook([ROW]))
    assert archive.prize_rows(path) == [ROW]


def test_empty_sheet_is_valid_but_missing_sheet_is_error(tmp_path):
    path = tmp_path / 'empty.xlsx'
    path.write_bytes(workbook([]))
    assert archive.prize_rows(path) == []
    path.write_bytes(workbook([], '錯誤工作表'))
    with pytest.raises(RuntimeError, match='工作表'):
        archive.prize_rows(path)


def test_xlsx_download_is_wrapped_in_real_archive(tmp_path):
    path = tmp_path / 'prize.xlsx'
    path.write_bytes(workbook([ROW]))
    assert detect_download_format(path) == 'xlsx'
    result = archive_as_zip(path, tmp_path / 'prize.zip')
    with zipfile.ZipFile(result) as z:
        assert z.namelist() == ['prize.xlsx']
    assert archive.prize_rows(result) == [ROW]


def html(items):
    return 'purchaseList: ' + json.dumps({'data': items})


def test_customer_exact_invoice_and_line():
    item = {'invoice_no': ROW[1], 'name': '測試客戶', 'address': '測試地址', 'member': {'line': 'https://line.me/test'}}
    assert customer_from_html(html([dict(item, invoice_no='DM00000000'), item]), ROW[1]) == ('測試客戶', '測試地址', 'https://line.me/test')
    with pytest.raises(RuntimeError, match='2 組'):
        customer_from_html(html([item, dict(item, name='另一位')]), ROW[1])
    with pytest.raises(RuntimeError, match='0 組'):
        customer_from_html(html([]), ROW[1])


def test_reimport_updates_same_row_and_links_name(monkeypatch, tmp_path):
    path = tmp_path / 'p.xlsx'; path.write_bytes(workbook([ROW]))
    monkeypatch.setattr(archive, '_already_imported', lambda *a: True)
    monkeypatch.setattr(archive, '_ensure_sheet', lambda *a: 7)
    from tools.invoice_center import prize_customers
    monkeypatch.setattr(prize_customers, 'lookup_customers', lambda *a: {ROW[1]: ('=客戶', '地址', 'https://line.me/test')})
    sheets = MagicMock()
    values = sheets.spreadsheets().values()
    values.get().execute.return_value = {'values': [['姓名', '地址', *archive.PRIZE_HEADERS], ['', '', *ROW]]}
    assert archive.import_prize(sheets, 'id', '20260708', path, area='台北') == 1
    body = values.batchUpdate.call_args.kwargs['body']
    assert body['valueInputOption'] == 'RAW'
    assert [d['range'] for d in body['data']] == ["'中獎發票'!C2:L2", "'中獎發票'!A2:B2"]
    link = sheets.spreadsheets().batchUpdate.call_args.kwargs['body']['requests'][0]['repeatCell']
    assert link['range']['startRowIndex'] == 1
    assert link['cell']['userEnteredFormat']['textFormat']['link']['uri'] == 'https://line.me/test'


def test_period_folder_creates_hyphen_name(monkeypatch):
    drive = MagicMock(); drive.files().list().execute.return_value = {'files': []}
    calls = []
    monkeypatch.setattr(archive, 'get_or_create_folder', lambda *a: calls.append(a) or 'folder')
    assert archive.get_or_create_period_folder(drive, 'parent', '09-10') == 'folder'
    assert calls[0][-1] == '09-10'


@pytest.mark.parametrize('with_labels', [False, True])
def test_radio_selects_all_and_excel_in_same_table_row(with_labels):
    from playwright.sync_api import sync_playwright
    def option(name, value, text, checked=False):
        field = f'<input type="radio" name="{name}" value="{value}" {"checked" if checked else ""}>{text}'
        return f'<label>{field}</label>' if with_labels else field
    source = '<table><tr><td>' + ''.join([
        option('status', 'all', '全部'), option('status', 'unprinted', '未列印', True),
        option('status', 'printed', '已列印'),
        '</td></tr><tr><td>', option('format', 'excel', 'Excel'), option('format', 'csv', 'CSV', True),
    ]) + '</td></tr></table>'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(); page.set_content(source)
        _check_prize_option(page, '全部'); _check_prize_option(page, 'Excel')
        assert page.locator('input[name=status]:checked').input_value() == 'all'
        assert page.locator('input[name=format]:checked').input_value() == 'excel'
        browser.close()
