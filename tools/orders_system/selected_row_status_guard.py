# -*- coding: utf-8 -*-
"""指定列批次建單狀態防呆與優化入口綁定。"""
from __future__ import annotations

import re
import pandas as pd
import orders

_INSTALLED = False


def _scalar(value):
    """row.get() 遇到重複欄名時可能回傳 Series；只取第一個同名欄值。"""
    if isinstance(value, pd.Series):
        return value.iloc[0] if len(value) else ""
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value


def normalize_status(value) -> str:
    text = str(_scalar(value) or "")
    text = text.replace("\u00a0", " ").replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", text)


def _first_series(df: pd.DataFrame, name: str) -> pd.Series:
    selected = df.loc[:, df.columns == name]
    if selected.shape[1] == 0:
        raise RuntimeError(f"工作表缺少必要欄位：{name}")
    return selected.iloc[:, 0]


def _is_unarranged_blank_order(row) -> bool:
    status = normalize_status(row.get("狀態", ""))
    order_no = _scalar(row.get("訂單編號", ""))
    return status == "未安排" and orders.is_blank(order_no)


def _safe_load_candidates(batch_opt, sheet_name: str) -> pd.DataFrame:
    """只抽取第一個同名欄位，避免 duplicate labels 造成 pandas reindex 失敗。"""
    try:
        _, df = batch_opt.load_worksheet(sheet_name)
    except Exception as exc:
        if type(exc).__name__ == "WorksheetNotFound":
            raise ValueError(f"找不到工作表分頁「{sheet_name}」") from exc
        raise

    if "__sheet_row__" not in df.columns:
        df = df.copy()
        df["__sheet_row__"] = range(2, len(df) + 2)

    work = pd.DataFrame(index=df.index)
    work["__sheet_row__"] = _first_series(df, "__sheet_row__")
    for col in batch_opt.REQUIRED_COLUMNS:
        work[col] = _first_series(df, col).map(batch_opt._text)

    work = work[
        work["狀態"].map(normalize_status).eq("未安排")
        & work["訂單編號"].eq("")
        & work["姓名"].ne("")
        & work["電話"].ne("")
        & work["地址"].ne("")
        & work["日期"].ne("")
        & work["開始時間"].ne("")
        & work["結束時間"].ne("")
    ].copy()
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

    orders.should_process_row = _is_unarranged_blank_order
    orders.should_create_order = _is_unarranged_blank_order

    try:
        import batch_booking_optimized as batch_opt
        from batch_booking_safety import run_process_web_optimized

        batch_opt._load_candidates = lambda sheet_name: _safe_load_candidates(batch_opt, sheet_name)

        # 直接綁定人工「批次建單優化」入口，不再依賴 import/stack patch。
        # 查無班表時允許核心補檸檬人；已有配班專員時核心不會覆蓋。
        def _optimized_runner_with_lemon_fallback(**kwargs):
            kwargs["allow_auto_lemon_shift"] = True
            return run_process_web_optimized(**kwargs)

        batch_opt.run_process_web = _optimized_runner_with_lemon_fallback
    except Exception:
        pass

    _INSTALLED = True
