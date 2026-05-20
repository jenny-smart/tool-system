"""
共用完整下載工具：避免日排程 / 月排程下載資料時只抓到前 1000 列。

使用情境：
1. API 支援 limit + offset 分頁
2. Google Sheet 原本 range 寫死 A1:Z1000，需要改成 A:Z
"""

from typing import Callable, List, Any, Dict


def fetch_all_rows(fetch_page_func: Callable[..., List[Any]], page_size: int = 1000) -> List[Any]:
    """
    分頁抓完整資料，直到沒有下一頁為止。

    fetch_page_func 需接受：
        limit: 每頁筆數
        offset: 起始位置

    範例：
        rows = fetch_all_rows(
            lambda limit, offset: download_schedule(limit=limit, offset=offset)
        )
    """
    all_rows: List[Any] = []
    offset = 0

    while True:
        rows = fetch_page_func(limit=page_size, offset=offset)

        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        offset += page_size

    return all_rows


def normalize_api_response(response: Any) -> List[Any]:
    """
    如果 API 回傳格式不固定，可用這個函式統一轉成 rows list。
    支援：
    - list
    - {"data": [...]}
    - {"rows": [...]}
    - {"items": [...]}
    """
    if response is None:
        return []

    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        for key in ("data", "rows", "items", "results"):
            value = response.get(key)
            if isinstance(value, list):
                return value

    return []


def fetch_all_rows_from_api(request_func: Callable[..., Any], page_size: int = 1000, **base_params: Dict[str, Any]) -> List[Any]:
    """
    適合 requests.get / API client 這類呼叫。

    範例：
        rows = fetch_all_rows_from_api(
            lambda **params: requests.get(url, params=params).json(),
            page_size=1000,
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
    """
    all_rows: List[Any] = []
    offset = 0

    while True:
        response = request_func(
            **base_params,
            limit=page_size,
            offset=offset,
        )
        rows = normalize_api_response(response)

        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        offset += page_size

    return all_rows


def google_sheet_open_range(sheet_name: str, columns: str = "A:Z") -> str:
    """
    避免使用 A1:Z1000 這種固定 1000 列範圍。

    範例：
        range_name = google_sheet_open_range("專員班表")
        # 回傳：專員班表!A:Z
    """
    return f"{sheet_name}!{columns}"
