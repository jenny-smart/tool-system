from copy import deepcopy
import re

import pytest

from tools.service_management.vip_schedule_diff import plan_schedule, sync_schedule


class MissingSheet(Exception):
    pass


def old_row(event='event1', order='', date='2026/11/10', start='9:00', service='1人', status='未安排'):
    row = [service, '', '王小明', '0912345678', '台北市大安區', date, start, '12:00', status] + [''] * 24
    row[9] = '購買項目原值'
    row[12] = order
    row[13:16] = ['失敗', '原原因', '原沒班表日']
    row[17:30] = ['原確認信', '原日曆结果'] + ['原成單資料'] * 11
    row[30] = event
    return row


def source_row(row, **changes):
    row = deepcopy(row)
    for column, value in changes.items(): row[int(column)] = value
    return row[:9] + [''] * 9 + [row[30]]


def test_booked_event_date_people_and_hours_changes_keep_all_order_fields():
    original = old_row(order='LC123')
    source = source_row(original, **{'0': '2人', '5': '2026/11/12', '7': '13:00'})
    plan = plan_schedule([original], [source], '台北202611')
    change = plan['updates'][0]
    assert change['row'] == 2
    assert change['values'][:9] == source[:9]
    assert change['values'][9:30] == original[9:30]
    assert change['kind'] == '已成單異動'
    assert '1人 → 2人' in change['values'][32]
    assert '2026/11/10 → 2026/11/12' in change['values'][32]
    assert plan['count'] == 1


def test_unbooked_event_updates_calendar_fields_but_keeps_failed_result_and_extras():
    original = old_row()
    source = source_row(original, **{'0': '2人', '5': '2026/11/12'})
    values = plan_schedule([original], [source], '台北202611')['updates'][0]['values']
    assert values[0] == '2人' and values[5] == '2026/11/12'
    assert values[9:30] == original[9:30]
    assert values[13] == '失敗'


def test_legacy_unique_customer_can_be_matched_without_event_id():
    original = old_row(event='', order='LC123')
    source = source_row(old_row(), **{'5': '2026/11/12'})
    plan = plan_schedule([original], [source], '台北202611')
    assert plan['count'] == 1
    assert plan['updates'][0]['values'][12] == 'LC123'
    assert plan['updates'][0]['values'][30] == 'event1'


def test_ambiguous_legacy_visits_are_not_added_as_new_bookings():
    rows = [old_row(event='', order='LC123'), old_row(event='', date='2026/11/17', start='14:00')]
    sources = [source_row(old_row(event='new1', date='2026/11/12', start='15:00')),
               source_row(old_row(event='new2', date='2026/11/20', start='16:00'))]
    plan = plan_schedule(rows, sources, '台北202611')
    assert plan['count'] == 2
    assert all(u['old'] is not None for u in plan['updates'])
    assert next(u for u in plan['updates'] if u['row'] == 2)['values'][:30] == rows[0][:30]
    assert next(u for u in plan['updates'] if u['row'] == 3)['values'][8] == '待確認'
    assert any(c['kind'] == '需人工核對' for c in plan['changes'])


def test_missing_rows_are_retained_and_unbooked_rows_stop_auto_booking():
    booked, pending = old_row(order='LC123'), old_row(event='event2')
    plan = plan_schedule([booked, pending], [], '台北202611')
    assert plan['count'] == 2
    assert plan['updates'][0]['values'][:30] == booked[:30]
    assert plan['updates'][1]['values'][8] == '待確認'
    assert all(c['kind'] == '本次日曆未見' for c in plan['changes'])


def test_formatting_differences_do_not_create_false_changes():
    original = old_row()
    source = source_row(original, **{'3': '912345678', '5': '2026-11-10', '6': '09:00'})
    plan = plan_schedule([original], [source], '台北202611')
    assert plan['updates'] == [] and plan['changes'] == []


def test_cross_month_existing_event_is_not_added():
    booked = old_row(order='LC123')
    source = source_row(booked, **{'5': '2026/12/01'})
    plan = plan_schedule([], [source], '台北202612', {'event1': [('台北202611', 2, booked)]})
    assert not plan['updates']
    assert plan['changes'][0]['kind'] == '跨月異動'
    assert plan['changes'][0]['before'][12] == 'LC123'


class Sheet:
    def __init__(self, title, rows=()):
        self.title = title
        self.data = [[''] * 30 + ['日曆事件ID', '日曆異動', '日曆異動內容']] + deepcopy(list(rows))
        self.row_count, self.col_count = 200, 33
        self.batches, self.formats = [], []

    def get(self, range_name, **kwargs):
        if range_name.startswith('A1:'): return deepcopy(self.data[:1])
        if self.title.startswith('排程差異_'): return deepcopy(self.data[1:])
        return deepcopy(self.data[1:])

    def update(self, values, range_name, **kwargs):
        col = re.match(r'([A-Z]+)', range_name).group(1)
        index = 0
        for char in col: index = index * 26 + ord(char) - 64
        while len(self.data[0]) < index - 1 + len(values[0]): self.data[0].append('')
        self.data[0][index - 1:index - 1 + len(values[0])] = deepcopy(values[0])

    def batch_update(self, updates, **kwargs):
        self.batches.append((deepcopy(updates), kwargs))
        for update in updates:
            col, row = re.match(r'([A-Z]+)(\d+)', update['range']).groups()
            index = 0
            for char in col: index = index * 26 + ord(char) - 64
            while len(self.data) < int(row): self.data.append([''] * 33)
            values = update['values'][0]
            while len(self.data[int(row) - 1]) < index - 1 + len(values): self.data[int(row) - 1].append('')
            self.data[int(row) - 1][index - 1:index - 1 + len(values)] = values

    def append_rows(self, values, **kwargs): self.data.extend(deepcopy(values))
    def batch_format(self, values): self.formats.extend(deepcopy(values))
    def freeze(self, **kwargs): pass
    def resize(self, rows, cols): self.row_count, self.col_count = rows, cols


class Book:
    def __init__(self, sheets=()): self.sheets = {s.title: s for s in sheets}
    def worksheet(self, title):
        if title not in self.sheets: raise MissingSheet()
        return self.sheets[title]
    def worksheets(self): return list(self.sheets.values())
    def add_worksheet(self, title, **kwargs):
        self.sheets[title] = Sheet(title)
        return self.sheets[title]


def sync(book, sources, compare_only=False, name='台北202611'):
    return sync_schedule(book, name, sources, ['標題'] * 30, MissingSheet, compare_only=compare_only)


def test_compare_only_writes_report_without_changing_schedule_or_colors():
    row = old_row(order='LC123')
    sheet = Sheet('台北202611', [row])
    before = deepcopy(sheet.data)
    book = Book([sheet])
    result = sync(book, [source_row(row, **{'0': '2人'})], compare_only=True)
    assert sheet.data == before and not sheet.formats and not sheet.batches
    assert result['differences'] == {'已成單異動': 1}
    assert book.sheets[result['report']].data[1][8] == 'LC123'


def test_preview_missing_schedule_does_not_create_schedule():
    book = Book()
    sync(book, [source_row(old_row())], compare_only=True)
    assert '台北202611' not in book.sheets


def test_writer_updates_booked_calendar_cells_preserving_k_ag_and_row_position():
    row = old_row(order='LC123')
    sheet = Sheet('台北202611', [row])
    book = Book([sheet])
    source = source_row(row, **{'0': '2人'})
    sync(book, [source])
    assert sheet.data[1][10:30] == row[10:30]
    assert all(u['range'] in ('A2:J2', 'D2', 'AH2', 'AI2', 'AJ2', 'AK2') for batch, _ in sheet.batches for u in batch)
    assert sheet.formats[0]['range'] == 'A2:AK2'
    report = book.sheets['排程差異_台北202611']
    before = len(report.data)
    sync(book, [source])
    assert len(report.data) == before
    assert sheet.data[1][10:30] == row[10:30]


def _lookup(row, sheet):
    return (f"=IFNA(xlookup(E{row},'{sheet}'!E:E,'{sheet}'!J:J),"
            f"xlookup(D{row},'{sheet}'!D:D,'{sheet}'!J:J))")


@pytest.mark.parametrize('period,previous', [('202611', '202610'), ('202601', '202512')])
def test_new_rows_get_previous_month_formula_and_phone_raw(period, previous):
    book = Book()
    sync(book, [source_row(old_row(date=period[:4] + '/' + period[4:] + '/10'))], name='台北' + period)
    sheet = book.sheets['台北' + period]
    assert sheet.data[1][9] == _lookup(2, f'台北{previous}')
    assert sheet.batches[-1][1]['value_input_option'] == 'RAW'
    assert sheet.data[1][3] == '0912345678'


def test_changes_during_comparison_abort_before_any_schedule_write():
    row = old_row()
    sheet = Sheet('台北202611', [row])
    original_get = sheet.get
    reads = 0
    def get(range_name, **kwargs):
        nonlocal reads
        if range_name == 'A2:AG':
            reads += 1
            if reads == 2: sheet.data[1][12] = 'LC_NEW'
        return original_get(range_name, **kwargs)
    sheet.get = get
    with pytest.raises(RuntimeError, match='比對期間已有更新'):
        sync(Book([sheet]), [source_row(row, **{'0': '2人'})])
    assert not sheet.batches and not sheet.formats
    assert sheet.data[1][12] == 'LC_NEW'


def test_partial_export_does_not_block_rows_outside_export_range():
    original = old_row(date='2026/11/25')
    plan = plan_schedule([original], [], '台北202611', coverage=('2026-11-01', '2026-11-10'))
    assert plan['updates'] == [] and plan['changes'] == []


def test_duplicate_export_event_ids_abort_instead_of_creating_duplicate_bookings():
    with pytest.raises(RuntimeError, match='重複事件ID'):
        plan_schedule([], [source_row(old_row()), source_row(old_row())], '台北202611')


def test_legacy_30_column_schedule_is_extended_without_overwriting_order():
    row = old_row(event='', order='LC123')
    sheet = Sheet('台北202611', [row[:30]])
    sheet.col_count = 30
    sheet.data[0] = sheet.data[0][:30]
    original_get = sheet.get
    def get(range_name, **kwargs):
        assert range_name not in ('A2:AG', 'A1:AG1')
        return original_get(range_name, **kwargs)
    sheet.get = get
    sync(Book([sheet]), [source_row(old_row(), **{'0': '2人'})])
    assert sheet.col_count == 37
    assert sheet.data[1][10:30] == row[10:30]


def test_custom_ae_ag_are_preserved_and_tracking_columns_reused():
    row = old_row(order='LC123')
    custom = ['自訂值', '=SUM(A2:A9)', '保留資料']
    sheet = Sheet('台北202611', [row[:30] + custom])
    sheet.data[0][30:33] = ['自訂一', '自訂二', '自訂三']
    book = Book([sheet])
    source = source_row(row, **{'0': '2人'})
    sync(book, [source])
    assert sheet.data[1][10:30] == row[10:30]
    assert sheet.data[1][30:33] == custom
    assert sheet.data[0][33:36] == ['日曆事件ID', '日曆異動', '日曆異動內容']
    assert sheet.data[1][33] == 'event1'
    assert sheet.col_count == 37
    sync(book, [source])
    assert sheet.col_count == 37
    assert sheet.data[1][30:33] == custom


def test_unnamed_columns_with_data_are_preserved():
    row = old_row(event='')[:30] + [''] * 10 + ['不可覆蓋']
    sheet = Sheet('台北202611', [row])
    sheet.data[0] = [''] * 41
    sheet.col_count = 41
    sync(Book([sheet]), [source_row(old_row(), **{'0': '2人'})])
    assert sheet.data[1][40] == '不可覆蓋'
    assert sheet.data[0][41:44] == ['日曆事件ID', '日曆異動', '日曆異動內容']
    assert sheet.data[1][10:30] == row[10:30]


def test_preview_with_custom_columns_does_not_modify_schedule():
    sheet = Sheet('台北202611', [old_row()[:30] + ['甲', '乙', '丙']])
    sheet.data[0][30:33] = ['自訂一', '自訂二', '自訂三']
    before = deepcopy(sheet.data)
    sync(Book([sheet]), [source_row(old_row())], compare_only=True)
    assert sheet.data == before and not sheet.batches and not sheet.formats


def test_relocated_foreign_event_prevents_cross_month_duplicate():
    foreign = Sheet('台北202611', [old_row(order='LC123')[:30] + ['自訂值', 'event1', '', '']])
    foreign.col_count = 34
    foreign.data[0] = [''] * 30 + ['自訂欄', '日曆事件ID', '日曆異動', '日曆異動內容']
    book = Book([foreign])
    result = sync(book, [source_row(old_row(date='2026/12/10'))], name='台北202612')
    assert result['differences'] == {'跨月異動': 1}
    assert len(book.sheets['台北202612'].data) == 1


def test_existing_business_headers_and_unbooked_k_ag_are_preserved():
    row = old_row(event='')[:30] + ['甲', '乙', '丙']
    sheet = Sheet('台北202611', [row])
    sheet.data[0] = [''] * 10 + ['客製欄'] + [''] * 22
    original_header = deepcopy(sheet.data[0])
    sync(Book([sheet]), [source_row(old_row(), **{'0': '2人'})])
    assert sheet.data[0][:33] == original_header
    assert sheet.data[1][0] == '2人'
    assert sheet.data[1][10:33] == row[10:33]


def test_recreated_events_match_same_customer_date_without_false_review():
    rows = [old_row(date=f'2026/11/{day}', order=f'LC{day}', event=f'old{day}') for day in (5, 12, 19, 26)]
    for row in rows: row[8] = '已安排'
    sources = [source_row(row, **{'8': '未安排', '30': f'new{i}'}) for i, row in enumerate(rows)]
    plan = plan_schedule(rows, sources, '台北202611')
    assert not plan['changes']
    assert all(u['values'][:30] == rows[u['row'] - 2][:30] for u in plan['updates'])
    assert plan['count'] == 4


def test_recreated_event_changed_hours_still_reports_real_difference():
    row = old_row(order='LC123', event='old')
    row[8] = '已安排'
    plan = plan_schedule([row], [source_row(row, **{'6': '8:30', '8': '未安排', '30': 'new'})], '台北202611')
    assert plan['changes'][0]['kind'] == '已成單異動'
    assert '時段：09:00–12:00 → 08:30–12:00' in plan['changes'][0]['detail']
    assert '狀態' not in plan['changes'][0]['detail']
    assert plan['updates'][0]['values'][6] == '8:30'
    assert plan['updates'][0]['values'][8] == '已安排'
    assert plan['updates'][0]['values'][10:30] == row[10:30]


def test_inline_change_details_and_date_do_not_refresh_on_identical_rerun():
    row = old_row(order='LC123')
    sheet = Sheet('台北202611', [row])
    book = Book([sheet])
    source = source_row(row, **{'6': '8:30'})
    sync(book, [source])
    assert sheet.data[0][36] == '更新日期'
    assert '時段：09:00–12:00 → 08:30–12:00' in sheet.data[1][35]
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', sheet.data[1][36])
    sheet.batches.clear()
    sync(book, [source])
    assert not sheet.batches


def test_resolved_false_review_is_cleared_on_original_row():
    row = old_row(order='LC123', event='old')
    row[31:33] = ['需人工核對', '先前誤判']
    sheet = Sheet('台北202611', [row])
    sync(Book([sheet]), [source_row(row, **{'30': 'new'})])
    assert sheet.data[1][34:36] == ['', '']
    assert sheet.data[1][10:30] == row[10:30]


@pytest.mark.parametrize('booked', [True, False])
def test_additional_paused_event_does_not_flag_existing_customer_rows(booked):
    rows = [old_row(date=f'2026/11/{day}', order=f'LC{day}' if booked else '', event=f'active{day}') for day in (5, 12, 19, 26)]
    if booked:
        for row in rows: row[8] = '已安排'
    sources = [source_row(row, **{'8': '未安排'}) for row in rows]
    sources.append(source_row(rows[-1], **{'6': '8:30', '7': '12:30', '8': '暫停', '30': 'paused26'}))
    plan = plan_schedule(rows, sources, '台北202611')
    assert [item['kind'] for item in plan['changes']] == ['新增']
    added = next(u for u in plan['updates'] if u['old'] is None)
    assert added['values'][8] == '暫停'
    assert added['values'][30] == 'paused26'
    assert plan['count'] == 5


def test_booked_address_change_refreshes_j_formula_and_preserves_all_k_ag():
    row = old_row(order='LC123', status='已安排')
    extra = ['原沒班表', '原餘額不足', '原改色原因']
    sheet = Sheet('台北202611', [row[:30] + extra])
    sheet.data[0][30:33] = ['沒班表日期', '餘額不足未送', '日曆改色原因']
    original = deepcopy(sheet.data[1][10:33])
    source = source_row(row, **{'4': '台北市新地址', '5': '2026/11/12', '8': '未安排'})
    book = Book([sheet])
    sync(book, [source])
    assert sheet.data[1][4] == '台北市新地址'
    assert sheet.data[1][5] == '2026/11/12'
    assert sheet.data[1][8] == '已安排'
    assert sheet.data[1][9] == _lookup(2, '台北202610')
    assert sheet.data[1][10:33] == original
    assert '台北市大安區 → 台北市新地址' in sheet.data[1][35]
    assert sheet.data[1][36]
    sheet.batches.clear()
    sync(book, [source])
    assert not sheet.batches


def test_website_has_no_compare_only_checkbox_or_dispatch_argument():
    from pathlib import Path
    source = (Path(__file__).parents[1] / 'toolapp.py').read_text()
    assert 'vip_schedule_compare_only' not in source
    assert '只比對差異，不更新排程工作表' not in source
    assert 'cmd += ["--compare-only"]' not in source


def test_same_day_customer_address_highlights_all_visits_including_paused():
    active = old_row(order='LC123', status='已安排')
    paused = old_row(event='paused', start='14:00', status='暫停')
    other_day = old_row(event='other', date='2026/11/11')
    sheet = Sheet('台北202611', [active, paused, other_day])
    before = deepcopy(sheet.data)
    sync(Book([sheet]), [source_row(row) for row in (active, paused, other_day)])
    purple = {'red': .90, 'green': .85, 'blue': 1}
    marked = [item['range'] for item in sheet.formats if item['format']['backgroundColor'] == purple]
    assert marked == ['A2:AK2', 'A3:AK3']
    assert [row[:30] for row in sheet.data[1:]] == [row[:30] for row in before[1:]]


def test_new_same_day_events_are_highlighted_after_append():
    first = old_row()
    second = old_row(event='second', start='14:00')
    book = Book()
    sync(book, [source_row(first), source_row(second)])
    sheet = book.sheets['台北202611']
    purple = {'red': .90, 'green': .85, 'blue': 1}
    assert [item['range'] for item in sheet.formats if item['format']['backgroundColor'] == purple] == ['A2:AK2', 'A3:AK3']


def test_same_day_matching_normalizes_date_name_and_address():
    from tools.service_management.vip_schedule_diff import _same_day_rows
    first = old_row()
    second = old_row(date='2026-11-10', event='second')
    second[2] = ' 王小明 '
    second[4] = '台北市 大安區'
    assert _same_day_rows([first, second], []) == [2, 3]


@pytest.mark.parametrize('q,marked', [('1,200', True), (100, True), (0, False), ('-50', False), ('', False), ('無', False)])
def test_no_new_order_with_positive_vip_q_is_highlighted(q, marked):
    row = old_row()
    row[14] = '系統未產生新訂單編號'
    sheet = Sheet('台北202611', [row])
    source = source_row(row)
    source[16] = q
    before = deepcopy(row[:30])
    sync(Book([sheet]), [source])
    red = {'red': 1, 'green': .80, 'blue': .80}
    assert any(item['format']['backgroundColor'] == red for item in sheet.formats) == marked
    assert any(item['range'] == 'AK2' for batch, _ in sheet.batches for item in batch) == marked
    assert sheet.data[1][:30] == before


def test_q_displayed_once_applies_to_other_visits_for_same_customer():
    from tools.service_management.vip_schedule_diff import _balance_attention_rows
    first, second = old_row(), old_row(event='second', date='2026/11/17')
    second[14] = '系統未產生新訂單編號'
    sources = [source_row(first), source_row(second)]
    sources[0][16] = '500'
    assert _balance_attention_rows([first, second], [], sources) == [3]
    second[12] = 'LC123'
    assert _balance_attention_rows([first, second], [], sources) == []


def test_q_highlight_does_not_match_different_customer_or_missing_source():
    from tools.service_management.vip_schedule_diff import _balance_attention_rows
    row = old_row()
    row[14] = '系統未產生新訂單編號'
    other = old_row(event='other')
    other[2], other[3] = '另一客戶', '0987654321'
    source = source_row(other)
    source[16] = '500'
    assert _balance_attention_rows([row], [], [source]) == []


def test_balance_recovers_from_negative_updates_ak_without_calendar_changes():
    row = old_row()
    row[14] = '系統未產生新訂單編號'
    previous_time = '2026-10-01 09:00:00'
    physical = row[:30] + [''] * 3 + row[30:33] + [previous_time]
    sheet = Sheet('台北202611', [physical])
    sheet.col_count = 37
    sheet.data[0] = [''] * 33 + ['日曆事件ID', '日曆異動', '日曆異動內容', '更新日期']
    book = Book([sheet])
    source = source_row(row)
    source[16] = '-500'
    sync(book, [source])
    assert sheet.data[1][36] == previous_time
    source[16] = '500'
    sync(book, [source])
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', sheet.data[1][36])
    assert sheet.data[1][36] != previous_time
    assert sheet.data[1][:30] == row[:30]
    assert any(item['range'] == 'AK2' for batch, _ in sheet.batches for item in batch)
    assert any(item['format']['backgroundColor'] == {'red': 1, 'green': .80, 'blue': .80}
               for item in sheet.formats)


def test_balance_recovery_preview_does_not_write_timestamp_or_color():
    row = old_row()
    row[14] = '系統未產生新訂單編號'
    sheet = Sheet('台北202611', [row])
    source = source_row(row)
    source[16] = '500'
    before = deepcopy(sheet.data)
    sync(Book([sheet]), [source], compare_only=True)
    assert sheet.data == before
    assert not sheet.batches and not sheet.formats


def test_first_build_of_new_schedule_is_baseline_without_new_highlight():
    sources = [source_row(old_row(event=f'e{day}', date=f'2026/11/{day}')) for day in (10, 17)]
    plan = plan_schedule([], sources, '台北202611')
    assert [u['kind'] for u in plan['updates']] == ['', '']
    assert all(u['values'][31:33] == ['', ''] for u in plan['updates'])
    assert plan['changes'] == [] and plan['count'] == 2


def test_recreated_sheet_has_no_new_colors_and_later_additions_are_marked():
    first = old_row(event='e10', date='2026/11/10')
    book = Book([])
    sync(book, [source_row(first)])
    sheet = book.sheets['台北202611']
    assert sheet.formats == []
    change = sheet.data[0].index('日曆異動', 33)
    assert sheet.data[1][2] == '王小明' and (sheet.data[1] + [''] * 40)[change] == ''
    later = old_row(event='e17', date='2026/11/17')
    sync(book, [source_row(first), source_row(later)])
    assert sheet.data[2][change] == '新增'
    assert any(item['range'].startswith('A3:') for item in sheet.formats)


def _monthly(event, date, start='14:00', end='18:00', note='每月確認', status='待確認', order=''):
    row = old_row(event=event, date=date, start=start, status=status, order=order, service='2人4')
    row[1], row[7] = note, end
    return row


def test_monthly_confirm_rows_moved_to_new_dates_update_each_row_in_place():
    rows = [_monthly('old1', '2027/01/07'), _monthly('old2', '2027/02/04')]
    sources = [source_row(_monthly('new1', '2027/01/29', note='已確認', status='未安排')),
               source_row(_monthly('new2', '2027/02/26'))]
    plan = plan_schedule(rows, sources, '台北202701-202702')
    by_row = {u['row']: u['values'] for u in plan['updates']}
    assert by_row[2][1] == '已確認' and by_row[2][5] == '2027/01/29' and by_row[2][8] == '未安排'
    assert by_row[2][31] == '未成單異動'
    assert by_row[2][32] == '備註：每月確認 → 已確認；日期：2027/01/07 → 2027/01/29；狀態：待確認 → 未安排'
    assert by_row[3][5] == '2027/02/26' and by_row[3][32] == '日期：2027/02/04 → 2027/02/26'
    assert all('日曆候選' not in item['detail'] for item in plan['changes'])
    assert [item['kind'] for item in plan['changes']] == ['未成單異動', '未成單異動']


def test_three_unbooked_rows_pair_in_date_order_and_extra_event_is_added():
    rows = [_monthly(f'o{n}', d, '8:30', '12:30') for n, d in enumerate(('2026/12/15', '2027/01/19', '2027/02/16'))]
    sources = [source_row(_monthly(f'n{n}', d, note='已確認', status='未安排'))
               for n, d in enumerate(('2026/12/28', '2027/01/25', '2027/02/01', '2027/02/22'))]
    plan = plan_schedule(rows, sources, '台北202612-202702')
    dates = {u['row']: u['values'][5] for u in plan['updates']}
    assert [dates[2], dates[3], dates[4]] == ['2026/12/28', '2027/01/25', '2027/02/01']
    assert dates[5] == '2027/02/22'
    assert [c['kind'] for c in plan['changes']].count('新增') == 1


def test_customer_with_a_booked_row_still_needs_manual_review():
    rows = [_monthly('old1', '2027/01/07', order='LC1', status='已安排'), _monthly('old2', '2027/02/04')]
    sources = [source_row(_monthly('new1', '2027/01/29')), source_row(_monthly('new2', '2027/02/26'))]
    plan = plan_schedule(rows, sources, '台北202701-202702')
    assert any(c['kind'] == '需人工核對' for c in plan['changes'])
    assert next(u for u in plan['updates'] if u['row'] == 2)['values'][5] == '2027/01/07'


def test_slot_change_is_written_as_one_time_range():
    original = _monthly('o1', '2026/12/15', '8:30', '12:30')
    plan = plan_schedule([original], [source_row(_monthly('o1', '2026/12/15', '14:00', '18:00'))], '台北202612')
    assert plan['updates'][0]['values'][32] == '時段：08:30–12:30 → 14:00–18:00'
    only_end = plan_schedule([original], [source_row(_monthly('o1', '2026/12/15', '8:30', '13:00'))], '台北202612')
    assert only_end['updates'][0]['values'][32] == '時段：08:30–12:30 → 08:30–13:00'


def test_monthly_confirm_pairing_also_updates_booking_gate_columns():
    from tools.service_management.booking_gate import build_gate, parse_rule
    rule = parse_rule({"地區": "台北", "確認檔案ID": "F", "確認工作表": "2026大掃除", "確認欄": "V", "備註欄": "W",
                       "排程排除欄": "A", "排除關鍵字": "改,加", "已確認欄": "B", "已確認關鍵字": "已確認"},
                      "2026-12-01", "2027-02-28")
    confirm = [""] * 23
    confirm[2], confirm[3], confirm[5], confirm[21] = '王小明', '0912345678', '台北市大安區', 'FALSE'
    rows = [_monthly('old1', '2027/01/07'), _monthly('old2', '2027/02/04')]
    sheet = Sheet('台北202701-202702', rows)
    sources = [source_row(_monthly('new1', '2027/01/29', note='已確認', status='未安排')),
               source_row(_monthly('new2', '2027/02/26'))]
    sync_schedule(Book([sheet]), '台北202701-202702', sources, ['標題'] * 30, MissingSheet,
                  period_filter='', gate=build_gate(rule, [confirm]))
    header = sheet.data[0]
    confirmed, stage = header.index('已確認'), header.index('預約條件')
    assert sheet.data[1][1] == '已確認' and sheet.data[1][5] == '2027/01/29' and sheet.data[1][8] == '未安排'
    assert sheet.data[1][confirmed] == '已確認' and sheet.data[1][stage] == '已確認'
    second = sheet.data[2] + [''] * len(header)
    # The run period covers 2027/02/26 (customers may move after the holiday).
    assert second[5] == '2027/02/26' and second[confirmed] == '' and second[stage] == '未回覆'


def _with_topup(row, flag):
    source = source_row(row)
    return source + [''] * (21 - len(source)) + [flag]


def test_schedule_copies_topup_flag_from_export_by_event_id():
    first, second = old_row(event='e1', date='2026/12/07'), old_row(event='e2', date='2026/12/21')
    booked = old_row(event='e3', date='2026/12/28', order='LC1', status='已安排')
    sheet = Sheet('台北202612', [first, second, booked])
    sync(Book([sheet]), [_with_topup(first, ''), _with_topup(second, '需儲值'), _with_topup(booked, '需儲值')],
         name='台北202612')
    column = sheet.data[0].index('需儲值')
    cells = [(row + [''] * (column + 1))[column] for row in sheet.data[1:4]]
    assert cells == ['', '需儲值', '']


def test_schedule_adds_no_topup_column_when_nobody_needs_topup():
    row = old_row(event='e1')
    sheet = Sheet('台北202611', [row])
    sync(Book([sheet]), [_with_topup(row, '')])
    assert '需儲值' not in sheet.data[0]


def test_combined_sheet_rows_look_up_month_before_range():
    existing = old_row(event='e1', date='2027/01/04')
    existing[9] = "=xlookup(E2,'台北202612'!E:E,'台北202612'!J:J)"
    other = old_row(event='e3', date='2027/02/15')
    other[9] = "=xlookup(E3,'台北202611'!E:E,'台北202611'!J:J)"
    sheet = Sheet('台北202612-202702', [existing, other])
    previous = Sheet('台北202611')
    new = old_row(event='e2', date='2027/02/01')
    sync_schedule(Book([sheet, previous]), '台北202612-202702',
                  [source_row(existing), source_row(other), source_row(new)], ['標題'] * 30, MissingSheet,
                  period_filter='')
    assert sheet.data[1][9] == _lookup(2, '台北202611')
    assert sheet.data[2][9] == _lookup(3, '台北202611')
    assert sheet.data[3][9] == _lookup(4, '台北202611')


def test_single_month_sheet_keeps_previous_month_lookup():
    sheet = Sheet('台北202701', [])
    sync(Book([sheet]), [source_row(old_row(event='e1', date='2027/01/04'))], name='台北202701')
    assert sheet.data[1][9] == _lookup(2, '台北202612')


class SortableSheet(Sheet):
    id = 99

    def update(self, values, range_name, **kwargs):
        col, row = re.match(r'([A-Z]+)(\d+)', range_name).groups()
        index = 0
        for char in col: index = index * 26 + ord(char) - 64
        for offset, line in enumerate(values):
            target_row = int(row) - 1 + offset
            while len(self.data) <= target_row: self.data.append([])
            cells = self.data[target_row]
            while len(cells) < index - 1 + len(line): cells.append('')
            cells[index - 1:index - 1 + len(line)] = line


class SortableBook(Book):
    def batch_update(self, body):
        for request in body['requests']:
            sheet = next(s for s in self.sheets.values() if getattr(s, 'id', None) == 99)
            if 'sortRange' in request:
                spec = request['sortRange']
                helper = spec['sortSpecs'][0]['dimensionIndex']
                start, end = spec['range']['startRowIndex'], spec['range']['endRowIndex']
                block = [row + [''] * (helper + 1 - len(row)) for row in sheet.data[start:end]]
                sheet.data[start:end] = sorted(block, key=lambda row: row[helper])
            if 'deleteDimension' in request:
                index = request['deleteDimension']['range']['startIndex']
                sheet.data = [row[:index] + row[index + 1:] for row in sheet.data]


def test_sort_order_uses_base_name_then_address_then_date():
    from tools.service_management.vip_schedule_diff import _sort_order

    def row(name, address, date):
        values = [''] * 33
        values[2], values[4], values[5], values[6] = name, address, date, '09:00'
        return values
    rows = {2: row('王小明', '大安', '2026/12/05'), 3: row('劉家熙-6樓', '內湖6樓', '2026/12/01'),
            4: row('劉家熙-4樓', '內湖4樓', '2026/12/08'), 5: [''] * 33, 6: row('劉家熙-4樓', '內湖4樓', '2026/12/02')}
    # 王（4 劃）→ 劉（15 劃）；劉家熙兩個地址各自依日期。
    assert _sort_order(rows) == {2: 2, 6: 3, 4: 4, 3: 5, 5: 6}


def test_sync_sorts_whole_rows_and_report_uses_sorted_row_numbers():
    wang = old_row(event='w', date='2026/12/05')
    li1 = old_row(event='l1', date='2026/12/14')
    li1[2], li1[4], li1[26] = '李曉微', '台北市大安區師大路', '客服備註-李1214'
    li2 = old_row(event='l2', date='2027/02/15')
    li2[2], li2[4] = '李曉微', '台北市大安區師大路'
    added = old_row(event='l3', date='2027/02/01')
    added[0], added[2], added[4] = '加2人3', '李曉微', '台北市大安區師大路'
    sheet = SortableSheet('台北202612-202702', [wang, li1, li2])
    book = SortableBook([sheet])
    sync_schedule(book, '台北202612-202702', [source_row(wang), source_row(li1), source_row(li2), source_row(added)],
                  ['標題'] * 30, MissingSheet, period_filter='', sort_rows=True)
    assert [(r[2], r[5]) for r in sheet.data[1:5]] == [
        ('王小明', '2026/12/05'), ('李曉微', '2026/12/14'), ('李曉微', '2027/02/01'), ('李曉微', '2027/02/15')]
    assert sheet.data[2][26] == '客服備註-李1214'
    assert all(len(row) < 40 for row in sheet.data)  # helper column removed
    report = book.sheets['排程差異_台北202612-202702'].data
    assert [line[2] for line in report[1:] if line[3] == '新增'] == [4]


def test_rows_no_longer_in_calendar_move_below_the_calendar_rows():
    gone = old_row(event='gone', date='2026/12/03', order='LC7', status='已安排')
    gone[2] = '王小明'
    kept = old_row(event='kept', date='2026/12/10')
    other = old_row(event='other', date='2026/12/05')
    other[2], other[4] = '李曉微', '台北市大安區師大路'
    sheet = SortableSheet('台北202612', [gone, kept, other])
    book = SortableBook([sheet])
    sync_schedule(book, '台北202612', [source_row(kept), source_row(other)], ['標題'] * 30, MissingSheet,
                  sort_rows=True)
    assert [(r[2], r[5]) for r in sheet.data[1:4]] == [
        ('王小明', '2026/12/10'), ('李曉微', '2026/12/05'), ('王小明', '2026/12/03')]
    assert sheet.data[3][12] == 'LC7'
    report = book.sheets['排程差異_台北202612'].data
    assert [line[2] for line in report[1:] if line[3] == '本次日曆未見'] == [4]
