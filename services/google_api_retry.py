"""
Google API retry helpers.

用途：
- 自動處理 Google Sheets API / gspread 的 429 Quota exceeded
- 自動 sleep + exponential backoff 後重試
- 在 toolapp.py 啟動時呼叫 install_gspread_retry() 一次即可

放置位置：
services/google_api_retry.py
"""

from __future__ import annotations

import random
import time
from functools import wraps
from typing import Any, Callable

import gspread


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def is_retryable_error(error: Exception) -> bool:
    text = str(error)

    if "429" in text:
        return True
    if "Quota exceeded" in text:
        return True
    if "RESOURCE_EXHAUSTED" in text:
        return True
    if "Rate Limit" in text:
        return True
    if "Read requests per minute" in text:
        return True

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in RETRYABLE_STATUS:
        return True

    # googleapiclient.errors.HttpError 是用 .resp（httplib2 Response）帶
    # 狀態碼，屬性名稱跟 gspread 的 .response.status_code 不一樣。
    resp = getattr(error, "resp", None)
    status = getattr(resp, "status", None)
    if status in RETRYABLE_STATUS:
        return True

    return False


def sleep_backoff(attempt: int, base_seconds: float = 6.0, max_seconds: float = 60.0) -> None:
    wait = min(max_seconds, base_seconds * (2 ** attempt))
    wait = wait + random.uniform(0, 2.5)
    time.sleep(wait)


def retry_call(fn: Callable[..., Any], *args: Any, retries: int = 6, **kwargs: Any) -> Any:
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as error:
            last_error = error
            if not is_retryable_error(error):
                raise
            sleep_backoff(attempt)

    if last_error:
        raise last_error

    return fn(*args, **kwargs)


def install_gspread_retry() -> None:
    """
    Monkey patch gspread HTTPClient.request。
    這樣不用逐一修改 ws.get_all_values(), ws.row_values(), ws.update_cell()。
    """
    http_client_cls = gspread.http_client.HTTPClient

    if getattr(http_client_cls.request, "_retry_installed", False):
        return

    original_request = http_client_cls.request

    @wraps(original_request)
    def request_with_retry(self, *args, **kwargs):
        return retry_call(
            lambda: original_request(self, *args, **kwargs),
            retries=6,
        )

    request_with_retry._retry_installed = True
    http_client_cls.request = request_with_retry


def install_googleapiclient_retry() -> None:
    """
    Monkey patch googleapiclient.http.HttpRequest.execute。

    tools/common/config_loader.get_sheets_service() 建出來的 service 是
    原生 googleapiclient（不是 gspread），local_agent_queue.py 的
    create_task／list_tasks／update_task／append_task_log 全部走這條路。
    install_gspread_retry() 只補到 gspread 那邊，這條路一直沒有保護——
    本機 Agent daemon 在任務執行中每 3 秒回報一次進度、任務結束時回寫
    status／log，只要中途撞到 429，這幾個 update_task() 呼叫沒有重試就
    直接失敗，佇列表裡的狀態就會停在最後一次成功寫入的樣子（例如永遠
    停在剛建立時的「等待本機 Agent」），即使任務其實已經在本機跑完，
    網頁上的執行日誌也看不到後續進度。
    """
    import googleapiclient.http

    request_cls = googleapiclient.http.HttpRequest

    if getattr(request_cls.execute, "_retry_installed", False):
        return

    original_execute = request_cls.execute

    @wraps(original_execute)
    def execute_with_retry(self, *args, **kwargs):
        return retry_call(
            lambda: original_execute(self, *args, **kwargs),
            retries=6,
        )

    execute_with_retry._retry_installed = True
    request_cls.execute = execute_with_retry


def api_pause(seconds: float = 1.2) -> None:
    """
    可在大量檔案迴圈中手動呼叫，降低 API 瞬間壓力。
    """
    time.sleep(seconds + random.uniform(0, 0.8))
