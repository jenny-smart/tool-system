from unittest.mock import MagicMock, patch
import pytest
from gspread.exceptions import APIError
from gspread.http_client import HTTPClient
import sheets_quota as quota
import cloud_batch_booking as cloud
from cloud_booking_progress import Progress


def api_error(code):
    response = MagicMock(status_code=code)
    response.json.return_value = {'error': {'code': code, 'message': 'quota' if code == 429 else 'forbidden'}}
    return APIError(response)


def test_quota_retry_waits_and_repeats_only_sheets_request():
    client = object.__new__(quota.QuotaHTTPClient)
    with patch.object(HTTPClient, 'request', side_effect=[api_error(429), 'ok']) as request, \
         patch.object(quota, 'pace'), patch.object(quota.time, 'sleep') as sleep:
        assert client.request('post', 'sheets/update') == 'ok'
    assert request.call_count == 2
    sleep.assert_called_once_with(2)


def test_nonquota_error_is_not_retried():
    client = object.__new__(quota.QuotaHTTPClient)
    with patch.object(HTTPClient, 'request', side_effect=api_error(403)) as request, patch.object(quota, 'pace'):
        with pytest.raises(APIError):
            client.request('post', 'sheets/update')
    assert request.call_count == 1


def test_quota_retry_is_bounded():
    client = object.__new__(quota.QuotaHTTPClient)
    with patch.object(HTTPClient, 'request', side_effect=api_error(429)) as request, \
         patch.object(quota, 'pace'), patch.object(quota.time, 'sleep') as sleep:
        with pytest.raises(APIError):
            client.request('get', 'sheets/read')
    assert request.call_count == 7
    assert [call.args[0] for call in sleep.call_args_list] == [2, 4, 8, 16, 32, 32]


def test_pacing_is_shared_between_client_instances():
    with patch.dict(quota._last, {}, clear=True), \
         patch.object(quota.time, 'monotonic', side_effect=[0, 0, 0, 2]), \
         patch.object(quota.time, 'sleep') as sleep:
        quota.pace('post')
        quota.pace('post')
    sleep.assert_called_once_with(2)


def test_partial_abort_preserves_confirmed_results_and_stops_batch():
    def fail_after_one(**kwargs):
        kwargs['progress_callback'](1, 1, 0, '3')
        raise api_error(429)
    with patch.object(cloud, 'load_pending', return_value=[(2, '台北', ''), (3, '台北', '')]) as load, \
         patch.object(cloud, 'ACCOUNTS', {'台北': {'email': 'test', 'password': 'test'}}), \
         patch.object(cloud, 'run_process_web_hybrid', side_effect=fail_after_one), \
         patch.object(cloud, 'Progress') as progress:
        assert cloud.run('sheet', pause_seconds=0) == 2
    assert load.call_count == 1
    assert progress.return_value.publish.call_args.args == ('中止，未回報列待核對', 1, 1, 0, 1, 2)


def test_progress_write_is_throttled_but_final_state_is_immediate():
    progress = Progress('sheet')
    progress.key = '123:1'
    progress.sheet = MagicMock()
    progress.row = 2
    progress.last_published = 100
    with patch('cloud_booking_progress.time.monotonic', return_value=101):
        progress.publish('成單中', 1, 1, 0, 1, 2)
        progress.sheet.update.assert_not_called()
        progress.publish('完成', 2, 2, 0, 0, 2)
    progress.sheet.update.assert_called_once()
