import argparse
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import pytest

SOURCE = Path(__file__).parents[1] / 'tools/service_management/stored_value.py'


def run(monkeypatch, step=4, fail=None, calendar=True, partial=False):
    node = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    calls = []
    def operation(number):
        def invoke(*args, **kwargs):
            calls.append(number)
            if fail == number: raise RuntimeError('模擬前置步驟失敗')
        return invoke
    ns = dict(argparse=argparse, datetime=datetime, timedelta=timedelta, sys=sys,
              SERVICE_AREA_GROUPS=['全區', '台北', '台中'], TZ_TAIPEI=timezone(timedelta(hours=8)),
              now_tp=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
              uuid=SimpleNamespace(uuid4=lambda: 'test-run'), log=Mock(), fmt=lambda d,f:d.strftime(f),
              _load_target_file_id=lambda:'target', _gc=lambda:object(),
              load_area_config=lambda *a,**k:([{'calendar_id':'cal'}, {'calendar_id':''}] if partial else [{'calendar_id':'cal' if calendar else ''}]),
              _SERVICE_LOG_CONTEXT={}, checkin_both=Mock(),
              step1_fetch_stored_value=operation(1), step2_export_vip_calendar=operation(2),
              step3_build_vip_schedule_sheets=operation(3))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
    monkeypatch.setattr(sys, 'argv', ['stored_value', '--step', str(step), '--start', '2026-11-01', '--end', '2026-11-30'])
    if fail or not calendar or partial:
        with pytest.raises(SystemExit) as error: ns['main']()
        assert error.value.code == 1
    else: ns['main']()
    return calls, ns


def test_three_steps_run_in_order(monkeypatch):
    calls, ns = run(monkeypatch)
    assert calls == [1, 2, 3]
    assert ns['checkin_both'].call_args.args[4] == 'SUCCESS'


@pytest.mark.parametrize('fail', [1, 2])
def test_previous_failure_prevents_schedule_using_stale_data(monkeypatch, fail):
    calls, _ = run(monkeypatch, fail=fail)
    assert 3 not in calls


def test_missing_calendar_prevents_schedule(monkeypatch):
    calls, _ = run(monkeypatch, calendar=False)
    assert calls == [1]


def test_existing_two_step_option_stays_two_steps(monkeypatch):
    assert run(monkeypatch, step=0)[0] == [1, 2]


def test_partial_missing_calendar_prevents_schedule(monkeypatch):
    assert run(monkeypatch, partial=True)[0] == [1, 2]
