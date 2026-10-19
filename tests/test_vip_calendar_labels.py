import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


SOURCE = Path(__file__).parents[1] / 'tools/service_management/stored_value.py'


def load_functions():
    names = {'get_status', '_calendar_label_statuses', '_event_schedule_status', '_fetch_calendar_events'}
    tree = ast.parse(SOURCE.read_text())
    ns = {'datetime': datetime, 'timezone': timezone,
          'STATUS_BY_COLOR': {'11': '待確認', '5': '已安排', '10': '暫停', '7': '保留單', '3': '未安排'},
          'log': SimpleNamespace(info=lambda *args: None)}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


@pytest.mark.parametrize('event,expected', [
    ({'eventLabelId': 'taipei-pending', 'colorId': '4'}, '待確認'),
    ({'eventLabelId': 'taichung-pending', 'colorId': '11'}, '待確認'),
    ({'eventLabelId': 'scheduled', 'colorId': '4'}, '已安排'),
    ({'eventLabelId': 'unknown', 'colorId': '11'}, ''),
    ({'eventLabelId': 'unnamed', 'colorId': '11'}, ''),
    ({'colorId': '11'}, '待確認'),
    ({}, ''),
])
def test_custom_labels_override_legacy_color_and_unknown_labels_stay_blank(event, expected):
    functions = load_functions()
    labels = functions['_calendar_label_statuses']({'labelProperties': {'eventLabels': [
        {'id': 'taipei-pending', 'name': '待確認'},
        {'id': 'taichung-pending', 'name': '待確認'},
        {'id': 'scheduled', 'name': '已預約'},
        {'id': 'unnamed'},
    ]}})
    assert functions['_event_schedule_status'](event, labels) == expected


def test_fetch_resolves_calendar_labels_for_every_page():
    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {
        'labelProperties': {'eventLabels': [{'id': 'pending', 'name': '待確認'}]}}
    service.events.return_value.list.return_value.execute.side_effect = [
        {'items': [{'eventLabelId': 'pending', 'colorId': '4'}], 'nextPageToken': 'page2'},
        {'items': [{'colorId': '3'}]},
    ]
    result = load_functions()['_fetch_calendar_events'](
        service, 'calendar-id', datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc), '台北')
    assert [e['_schedule_status'] for e in result] == ['待確認', '未安排']
    assert service.calendars.return_value.get.call_count == 1
    assert service.events.return_value.list.call_args.kwargs['pageToken'] == 'page2'
