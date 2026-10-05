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


@pytest.mark.parametrize('period,previous', [('202611', '202610'), ('202601', '202512')])
def test_new_rows_get_previous_month_formula_and_phone_raw(period, previous):
    book = Book()
    sync(book, [source_row(old_row(date=period[:4] + '/' + period[4:] + '/10'))], name='台北' + period)
    sheet = book.sheets['台北' + period]
    assert sheet.data[1][9] == f"=xlookup(E2,'台北{previous}'!E:E,'台北{previous}'!J:J)"
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
    assert '開始時間' in plan['changes'][0]['detail']
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
    assert '開始時間：9:00 → 8:30' in sheet.data[1][35]
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
    assert sheet.data[1][9] == "=xlookup(E2,'台北202610'!E:E,'台北202610'!J:J)"
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


def test_unique_unbooked_recreated_event_updates_original_date_status_and_id():
    row = old_row(event='old-event', date='2026/11/19', status='待確認')
    row[1] = '每月確認'
    row[31:33] = ['需人工核對', '先前誤判多筆排程']
    source = source_row(row, **{'1': '已確認', '5': '2026/11/05', '8': '未安排', '30': 'new-event'})
    plan = plan_schedule([row], [source], '台北202611', coverage=('2026-11-01', '2026-11-30'))
    assert plan['count'] == 1 and len(plan['updates']) == 1
    change = plan['updates'][0]
    assert change['row'] == 2 and change['old'] is not None
    assert change['values'][:9] == source[:9]
    assert change['values'][9:30] == row[9:30]
    assert change['values'][30] == 'new-event'
    assert change['kind'] == '未成單異動'
    assert '日曆事件ID：old-event → new-event' in change['values'][32]
    assert plan_schedule([change['values']], [source], '台北202611')['updates'] == []


def test_recreated_event_different_date_with_existing_order_stays_for_review():
    row = old_row(event='old-event', order='LC123', date='2026/11/19', status='已安排')
    source = source_row(row, **{'5': '2026/11/05', '30': 'new-event'})
    plan = plan_schedule([row], [source], '台北202611')
    assert plan['count'] == 1
    assert all(change['old'] is not None and change['values'][:30] == row[:30] for change in plan['updates'])
    assert all(change['values'][30] == 'old-event' for change in plan['updates'])
    assert any(change['kind'] == '需人工核對' for change in plan['changes'])
    assert all('有多筆' not in change['detail'] for change in plan['changes'])


def test_recreated_event_does_not_match_only_remaining_visit_of_multiple_visits():
    first = old_row(event='first', date='2026/11/05')
    second = old_row(event='second', date='2026/11/19')
    sources = [source_row(first), source_row(second, **{'5': '2026/11/26', '30': 'recreated'})]
    plan = plan_schedule([first, second], sources, '台北202611')
    assert plan['count'] == 2
    change = next(change for change in plan['updates'] if change['row'] == 3)
    assert change['values'][5] == '2026/11/19' and change['values'][30] == 'second'
    assert change['kind'] == '需人工核對'
    assert all(change['old'] is not None for change in plan['updates'])


@pytest.mark.parametrize('same_phone', [True, False])
def test_recreated_event_requires_same_phone_and_address(same_phone):
    row = old_row(event='old-event', date='2026/11/19')
    source = source_row(row, **{'5': '2026/11/05', '30': 'new-event',
                               '4' if same_phone else '3': '不同地址' if same_phone else '0999999999'})
    plan = plan_schedule([row], [source], '台北202611')
    assert all(change['values'][5] == '2026/11/19' for change in plan['updates'] if change['row'] == 2)


def test_recreated_event_outside_partial_export_is_not_replaced():
    row = old_row(event='old-event', date='2026/11/19')
    source = source_row(row, **{'5': '2026/11/05', '30': 'new-event'})
    plan = plan_schedule([row], [source], '台北202611', coverage=('2026-11-01', '2026-11-10'))
    assert all(change['values'][5] == '2026/11/19' and change['values'][30] == 'old-event'
               for change in plan['updates'] if change['row'] == 2)


def test_unique_recreated_event_does_not_override_other_month_identity():
    row = old_row(event='old-event', date='2026/11/19')
    source = source_row(row, **{'5': '2026/11/05', '30': 'new-event'})
    foreign = {'new-event': [('台北202610', 10, old_row(event='new-event', date='2026/10/29'))]}
    plan = plan_schedule([row], [source], '台北202611', foreign)
    assert any(change['kind'] == '跨月異動' for change in plan['changes'])
    assert all(change['old'] is not None and change['values'][5] == '2026/11/19' for change in plan['updates'])


def test_writer_updates_unique_recreated_event_in_place_and_keeps_k_ag():
    row = old_row(event='old-event', date='2026/11/19', status='待確認')
    sheet = Sheet('台北202611', [row[:30] + ['作業欄AE', '作業欄AF', '作業欄AG'] + row[30:33] + ['舊更新日期']])
    sheet.col_count = 37
    sheet.data[0] = ['原欄位'] * 33 + ['日曆事件ID', '日曆異動', '日曆異動內容', '更新日期']
    source = source_row(row, **{'1': '已確認', '5': '2026/11/05', '8': '未安排', '30': 'new-event'})
    original = deepcopy(sheet.data[1])
    book = Book([sheet])
    result = sync(book, [source])
    assert result['differences'] == {'未成單異動': 1}
    assert len(sheet.data) == 2
    assert sheet.data[1][:9] == source[:9]
    assert sheet.data[1][10:33] == original[10:33]
    assert sheet.data[1][33] == 'new-event'
    report_count = len(book.sheets[result['report']].data)
    sync(book, [source])
    assert len(book.sheets[result['report']].data) == report_count


def test_recreated_event_with_multiple_source_visits_stays_for_review():
    row = old_row(event='old-event', date='2026/11/19')
    sources = [source_row(row, **{'5': '2026/11/05', '30': 'new-first'}),
               source_row(row, **{'5': '2026/11/26', '30': 'new-second'})]
    plan = plan_schedule([row], sources, '台北202611')
    assert plan['count'] == 1
    assert all(change['old'] is not None and change['values'][5] == '2026/11/19' for change in plan['updates'])
    assert '來源 2 筆、既有排程 1 筆' in plan['updates'][0]['values'][32]
