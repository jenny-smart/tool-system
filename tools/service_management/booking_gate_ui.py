"""客服排程系統執行畫面：排程預約條件區塊（建立VIP排程工作表／全跑）。

是否套用與確認來源都在畫面上決定；期間跟著匯出起迄日。設定存在主控試算表
「排程預約條件」（每個地區一列），下次開啟自動帶入。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

FIELDS_MAIN = [
    ("確認檔案ID", "確認檔案（試算表 ID 或網址）"),
    ("確認工作表", "確認工作表"),
]
FIELDS_REPLY = [("確認欄", "回覆確認欄"), ("確認值", "確認值"), ("備註欄", "備註欄")]
FIELDS_STAGE = [("排程排除欄", "改加：排程欄"), ("排除關鍵字", "改加關鍵字"),
                ("已確認欄", "已確認：排程欄"), ("已確認關鍵字", "已確認關鍵字")]
FIELDS_MATCH = [("姓名欄", "姓名欄"), ("電話欄", "電話欄"), ("地址欄", "地址欄"), ("確認表排除欄", "確認表改加欄（選填）")]


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def deep_clean_overlap(start: date, end: date):
    """回傳與匯出範圍重疊的年度大掃除設定；沒有重疊回傳 None。"""
    from tools.service_management.stored_value import TZ_TAIPEI, _load_deep_clean_pricing_settings
    settings = _load_deep_clean_pricing_settings(
        datetime(start.year, start.month, start.day, tzinfo=TZ_TAIPEI),
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=TZ_TAIPEI),
    )
    for setting in settings:
        if _as_date(setting.phase1_start) <= end and _as_date(setting.phase2_end) >= start:
            return setting
    return None


def _cached(st, key, loader):
    if key not in st.session_state:
        st.session_state[key] = loader()
    return st.session_state[key]


def render(st, areas: list[str], start: date | None, end: date | None) -> dict | None:
    """畫出區塊；回傳 {"enabled": bool, "settings": {地區: 設定}}，日期未選時回傳 None。"""
    if not start or not end:
        return None
    from tools.service_management import booking_gate
    from tools.service_management.stored_value import _gc

    st.markdown('<div class="field-label">🧾 排程預約條件（分階段預約）</div>', unsafe_allow_html=True)
    overlap_key = f"booking_gate_overlap_{start}_{end}"
    try:
        overlap = _cached(st, overlap_key, lambda: deep_clean_overlap(start, end))
        overlap_error = ""
    except Exception as exc:
        overlap, overlap_error = None, str(exc)
    if overlap is not None:
        st.caption(f"此匯出範圍與 {overlap.season_year} 年節大掃除期間"
                   f"（{_as_date(overlap.phase1_start)}～{_as_date(overlap.phase2_end)}）重疊，預設套用。")
    elif overlap_error:
        st.caption(f"無法讀取大掃除年度設定（{overlap_error}），預設不套用，可自行勾選。")
    else:
        st.caption("此匯出範圍不在年節大掃除期間，預設不套用。")
    enabled = st.checkbox(
        "套用預約條件：寫入回覆確認、確認備註、改加標記、已確認、預約條件五欄",
        value=overlap is not None, key=f"booking_gate_on_{start}_{end}",
    )
    if not enabled:
        st.caption("不套用：排程不寫入這五欄，已寫入的會清空；批次成單照平常規則。")
        return {"enabled": False, "settings": {}}

    st.caption(f"期間：{start} ～ {end}（跟著上方匯出起迄日）。預約條件依序判斷：改加 → 已確認 → 可約 → 未回覆。")
    if st.button("重新讀取已儲存的設定", key="booking_gate_reload"):
        for area in areas:
            st.session_state.pop(f"booking_gate_settings_{area}", None)
    result = {}
    for area in areas:
        try:
            saved = _cached(st, f"booking_gate_settings_{area}",
                            lambda area=area: booking_gate.load_settings(_gc(), area))
        except Exception as exc:
            st.error(f"{area}：讀取排程預約條件設定失敗：{exc}")
            saved = {"地區": area, **booking_gate.DEFAULT_SETTINGS, **booking_gate.AREA_DEFAULTS.get(area, {})}
        if len(areas) > 1:
            st.markdown(f"**{area}**")
        values = {}

        def field(container, name, label):
            values[name] = container.text_input(label, value=saved.get(name, ""), key=f"booking_gate_{area}_{name}",
                                                help=booking_gate.HEADER_NOTES.get(name))

        for (name, label), col in zip(FIELDS_MAIN, st.columns(2)):
            field(col, name, label)
        for (name, label), col in zip(FIELDS_REPLY, st.columns(3)):
            field(col, name, label)
        for (name, label), col in zip(FIELDS_STAGE, st.columns(4)):
            field(col, name, label)
        with st.expander("進階：比對客人欄位", expanded=False):
            for (name, label), col in zip(FIELDS_MATCH, st.columns(4)):
                field(col, name, label)
        result[area] = values
    st.caption("執行時會儲存以上設定，下次自動帶入。")
    return {"enabled": True, "settings": result}


def save(state: dict | None) -> list[str]:
    """執行前儲存各地區設定；回傳錯誤訊息（空＝成功）。"""
    if not state or not state.get("enabled"):
        return []
    import gspread
    from tools.service_management import booking_gate
    from tools.service_management.stored_value import _gc
    errors = []
    gc = _gc()
    for area, values in state["settings"].items():
        try:
            booking_gate.save_settings(gc, area, values, gspread.WorksheetNotFound)
        except Exception as exc:
            errors.append(f"{area}：{exc}")
    return errors
