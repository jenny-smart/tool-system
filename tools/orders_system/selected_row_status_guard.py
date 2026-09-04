# -*- coding: utf-8 -*-
"""批次建單共用防呆：欄位去重、候選條件、回填欄位定位、優化入口綁定。"""
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
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    return bool(order_no) or normalize_status(row.get("狀態", "")) == "未安排"


def _should_create_order(row) -> bool:
    order_no = str(_scalar(row.get("訂單編號", "")) or "").strip()
    return not order_no and normalize_status(row.get("狀態", "")) == "未安排"


def _safe_load_candidates(batch_opt, sheet_name: str) -> pd.DataFrame:
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
    for col in ("原因", "沒班表日期"):
        work[col] = _first_series(df, col).map(batch_opt._text) if col in df.columns else ""

    required_ok = (
        work["姓名"].ne("") & work["電話"].ne("") & work["地址"].ne("")
        & work["日期"].ne("") & work["開始時間"].ne("") & work["結束時間"].ne("")
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


def _auto_filter_rows(batch_opt, sheet_name: str, mode: str) -> list[int]:
    work = _safe_load_candidates(batch_opt, sheet_name)
    # 自動篩選只建立新單，不把已有訂單編號列納入。
    work = work[
        work["狀態"].map(normalize_status).eq("未安排")
        & work["訂單編號"].eq("")
    ].copy()
    reason = work.get("原因", pd.Series("", index=work.index)).map(batch_opt._text)
    no_schedule_date = work.get("沒班表日期", pd.Series("", index=work.index)).map(batch_opt._text)
    if mode == "no_schedule":
        mask = reason.str.contains("無班表|沒班表", regex=True, na=False) | no_schedule_date.ne("")
    elif mode == "missing_order":
        mask = reason.str.contains("找不到訂單編號", regex=False, na=False)
    else:
        mask = pd.Series(False, index=work.index)
    return sorted(work.loc[mask, "__sheet_row__"].astype(int).tolist())


def _update_sheet_rows_first_header(ws, row_results):
    headers = orders.ensure_columns_in_sheet(ws)
    header_index = {}
    for i, header in enumerate(headers, 1):
        if header and header not in header_index:
            header_index[header] = i
    updates = []
    for row_num, info in (row_results or {}).items():
        xyz = orders.finalize_xyz(
            {"服務人員": info.get("服務人員", ""), "服務狀態": info.get("服務狀態", ""), "車馬費": info.get("車馬費", "")},
            fallback_fare=info.get("車馬費", "0"),
        )
        info["服務人員"], info["服務狀態"], info["車馬費"] = xyz["服務人員"], xyz["服務狀態"], xyz["車馬費"]
        for key, value in info.items():
            if key not in header_index:
                continue
            if key == "狀態" and str(value).strip() not in ("已安排", "待確認"):
                continue
            updates.append({"range": orders.gspread.utils.rowcol_to_a1(int(row_num), header_index[key]), "values": [["" if value is None else str(value)]]})
    if updates:
        ws.batch_update(updates)
        orders.set_customer_notice_clip_style(ws, headers=headers, row_numbers=row_results.keys())


def _install_optimized_filter_ui(batch_opt):
    """在既有批次優化畫面上方補回兩個自動篩選，不重寫原畫面。"""
    original_render = batch_opt.render
    if getattr(original_render, "_lemon_filter_wrapped", False):
        return

    def render_with_filters(backend_email: str, backend_password: str, env: str):
        st = batch_opt.st
        st.markdown("#### 快速自動篩選")
        st.caption("查無班表時自動補檸檬人：已啟用。只補當日無任何班別的檸檬人，不改動其他客人已配班專員。")
        sheet_name = str(st.session_state.get("batch_opt_sheet_name", "") or "").strip()
        c1, c2 = st.columns(2)
        if c1.button("自動篩選：未安排＋訂單編號空白＋無班表", disabled=not sheet_name, key="batch_opt_filter_no_schedule"):
            try:
                rows = _auto_filter_rows(batch_opt, sheet_name, "no_schedule")
                st.session_state["batch_opt_row_spec"] = ",".join(map(str, rows))
                st.success(f"已帶入 {len(rows)} 列" if rows else "查無符合『無班表』條件的列")
            except Exception as exc:
                st.error(f"自動篩選失敗：{exc}")
        if c2.button("自動篩選：未安排＋訂單編號空白＋O欄找不到訂單編號", disabled=not sheet_name, key="batch_opt_filter_missing_order"):
            try:
                rows = _auto_filter_rows(batch_opt, sheet_name, "missing_order")
                st.session_state["batch_opt_row_spec"] = ",".join(map(str, rows))
                st.success(f"已帶入 {len(rows)} 列" if rows else "查無 O欄『找不到訂單編號』的列")
            except Exception as exc:
                st.error(f"自動篩選失敗：{exc}")
        original_render(backend_email, backend_password, env)

    render_with_filters._lemon_filter_wrapped = True
    batch_opt.render = render_with_filters


def install_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    orders.load_worksheet = _load_worksheet_unique
    orders.update_sheet_rows = _update_sheet_rows_first_header
    orders.should_process_row = _should_process_row
    orders.should_create_order = _should_create_order
    orders.ORDERS_VERSION = "v2026.09.05-2"
    orders.ORDERS_UPDATED_AT = "2026-09-05"

    try:
        import batch_booking_optimized as batch_opt
        import batch_booking_safety as batch_safety
        batch_opt.load_worksheet = _load_worksheet_unique
        batch_opt._load_candidates = lambda sheet_name: _safe_load_candidates(batch_opt, sheet_name)
        batch_safety._BASE_UPDATE_SHEET_ROWS = _update_sheet_rows_first_header
        batch_safety._orders.update_sheet_rows = _update_sheet_rows_first_header

        def _optimized_runner_with_lemon_fallback(**kwargs):
            kwargs["allow_auto_lemon_shift"] = True
            return batch_safety.run_process_web_optimized(**kwargs)

        batch_opt.run_process_web = _optimized_runner_with_lemon_fallback
        _install_optimized_filter_ui(batch_opt)
    except Exception:
        pass
    _INSTALLED = True
