import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).parent / 'cloud_batch_booking_ui.py'


def load_status(get):
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('_latest_run', '_run_summary')]
    ns = {'_github_get': get, 'WORKFLOW': 'booking.yml', 're': re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def test_status_has_no_run():
    functions = load_status(lambda _: SimpleNamespace(json=lambda: {'workflow_runs': []}))
    assert functions['_latest_run']() is None


@pytest.mark.parametrize('log,expected', [
    ('FINISH attempted=30 success=25 fail=5 remaining=233', (30, 25, 5, 233)),
    ('Traceback: credentials missing', None),
])
def test_summary_distinguishes_partial_completion_from_startup_failure(log, expected):
    def get(path):
        if path.endswith('/jobs'):
            return SimpleNamespace(json=lambda: {'jobs': [{'name': 'booking', 'id': 123}]})
        assert path == 'actions/jobs/123/logs'
        return SimpleNamespace(text=log)
    assert load_status(get)['_run_summary'](456) == expected
