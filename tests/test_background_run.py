import sys
import time

from tools.common import background_run


def _wait(run, seconds=10):
    deadline = time.monotonic() + seconds
    while not run.done and time.monotonic() < deadline:
        time.sleep(0.05)
    return run


def test_collects_stdout_and_stderr_and_returncode():
    code = "import sys; print('a', flush=True); print('err', file=sys.stderr, flush=True); print('b', flush=True); sys.exit(2)"
    run = _wait(background_run.get(background_run.start([sys.executable, "-c", code])))
    assert run.done and run.returncode == 2 and not run.timed_out
    assert ("stdout", "a") in run.lines and ("stderr", "err") in run.lines
    completed = run.completed()
    assert completed.stdout == "a\nb" and completed.stderr == "err" and completed.returncode == 2


def test_snapshot_reads_only_new_lines():
    code = "print('1'); print('2'); print('3')"
    run = _wait(background_run.get(background_run.start([sys.executable, "-c", code])))
    assert [t for _, t in background_run.snapshot(run, 1)] == ["2", "3"]


def test_run_keeps_going_while_nobody_reads_and_timeout_kills():
    code = "import time; print('start', flush=True); time.sleep(30)"
    run = background_run.get(background_run.start([sys.executable, "-c", code], timeout=0.5))
    _wait(run)
    assert run.timed_out and run.done and ("stdout", "start") in run.lines


def test_unknown_run_id_returns_none():
    assert background_run.get("missing") is None
