# -*- coding: utf-8 -*-
"""指定列批次建單狀態防呆與優化入口綁定。"""
from __future__ import annotations

import re
import pandas as pd
import orders

_INSTALLED = False
_ORIGINAL_LOAD_WORKSHEET = orders.load_worksheet


def _scalar(value):
    if isinstance(value, pd.Series):
        return value.iloc[0] if len(value) else ""
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value


def normalize_status(value) -> str:
    text = str(_scalar(value) or "")
    text = text.replace("\u00a0", " ").replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", text)


def _dedupe_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.columns.is_unique:
        return df
    # 不改 Sheet，只在程式記憶體中保留第一個同名欄，避免 pandas reindex 失敗。
    return df.loc[:, ~df.columns.duplicated(keep="first")].copy()


def _load_worksheet_unique(sheet_name: str):
    ws, df = _ORIGINAL_LOAD_WORKSHEET(sheet_name)
    return ws, _dedupe_dataframe(df)


def _first_series(df: pd.DataFrame, name: str) -> pd.Series:
    selected = df.loc[:, df.columns == name]
    if selected.shape[1] == 0:
        raise RuntimeError(f"工作表缺少必要欄位：{name}")
    return selected.iloc[:, 0]


def _should_process_row(row) -> bool:
    """已有訂單編號＝同步既有訂單；無單號時只有未安排可建單。"""
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    if order_no:
        return True
    return normalize_status(row.get("狀態", "")) == "未安排"


def _should_create_order(row) -> bool:
    """只有未安排＋訂單編號空白才真的建立新訂單。"""
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    return not order_no and normalize_status(row.get("狀態", "")) == "未安排"


def _safe_load_candidates(batch_opt, sheet_name: str) -> pd.DataFrame:
    """人工批次優化：未安排空白單號可建單；已有單號可指定同步既有訂單。"""
    try:
        _, df = _load_worksheet_unique(sheet_name)
    except Exception as exc:
        if type(exc).__name__ == "WorksheetNotFound":
            raise ValueError(f"找不到工作表分頁「{sheet_name}」") from exc
        raise

    work = pd.DataFrame(index=df.index)
    work["__sheet_row__"] = _first_series(df, "__sheet_row__")
    for col in batch_opt.REQUIRED_COLUMNS:
        work[col] = _first_series(df, col).map(batch_opt._text)

    required_ok = (
        work["姓名"].ne("")
        & work["電話"].ne("")
        & work["地址"].ne("")
        & work["日期"].ne("")
        & work["開始時間"].ne("")
        & work["結束時間"].ne("")
    )
    create_ok = work["狀態"].map(normalize_status).eq("未安排") & work["訂單編號"].eq("")
    existing_ok = work["訂單編號"].ne("")
    work = work[required_ok & (create_ok | existing_ok)].copy()

    work.reset_index(drop=True, inplace=True)
    work["日期顯示"] = work["日期"].map(batch_opt._date_text)
    work["時段顯示"] = work.apply(
        lambda r: f"{batch_opt._time_text(r['開始時間'])}-{batch_opt._time_text(r['結束時間'])}", axis=1
    )
    work["群組鍵"] = work.apply(
        lambda r: (batch_opt._text(r["姓名"]), batch_opt._text(r["電話"]), batch_opt._text(r["地址"])), axis=1
    )
    return work


def install_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # 批次核心統一使用去重後 DataFrame，避免任何後續 groupby/reindex 再爆 duplicate labels。
    orders.load_worksheet = _load_worksheet_unique
    orders.should_process_row = _should_process_row
    orders.should_create_order = _should_create_order
    orders.ORDERS_VERSION = "v2026.09.05-1"
    orders.ORDERS_UPDATED_AT = "2026-09-05"

    try:
        import batch_booking_optimized as batch_opt
        from batch_booking_safety import run_process_web_optimized

        batch_opt.load_worksheet = _load_worksheet_unique
        batch_opt._load_candidates = lambda sheet_name: _safe_load_candidates(batch_opt, sheet_name)

        def _optimized_runner_with_lemon_fallback(**kwargs):
            kwargs["allow_auto_lemon_shift"] = True
            return run_process_web_optimized(**kwargs)

        batch_opt.run_process_web = _optimized_runner_with_lemon_fallback
    except Exception:
        pass

    _INSTALLED = True
