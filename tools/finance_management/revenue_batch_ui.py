"""【整合執行】財報營收：兩個獨立任務的盤點、確認與補跑。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
from tools.finance_management import revenue_batch as batch
from tools.finance_management.batch_ui_common import checked, reset_select_all, select_all_controls

PREFIX = "revenue_batch"
STATE_KEY = f"{PREFIX}_scan_result"
CONFIRM_KEY = f"{PREFIX}_confirm"


def render(log, username="", group="cash_flow"):
    prefix = f"{PREFIX}_{group}"
    state_key = f"{STATE_KEY}_{group}"
    confirm_key = f"{CONFIRM_KEY}_{group}"
    group_steps = next(steps for key, _label, steps in batch.GROUPS if key == group)
    group_label = next(label for key, label, _steps in batch.GROUPS if key == group)
    last_month = datetime.now(ZoneInfo("Asia/Taipei")).date().replace(day=1) - timedelta(days=1)
    period = st.text_input("期別（YYYYMM-2）", value=last_month.strftime("%Y%m-2"), key=f"revenue_batch_period_{group}")
    area_options = batch.RECONCILE_AREAS if group == "cash_flow" else batch.AREAS
    areas = st.multiselect("盤點地區（可複選）", area_options, default=area_options,
                           key=f"revenue_batch_areas_{group}")
    st.caption(f"任務：{group_label}。盤點會逐項列出，可單獨勾選補跑。")
    if st.button("🔍 盤點", type="primary", disabled=not areas, key=f"revenue_batch_scan_{group}"):
        with st.spinner("讀取各地區完成紀錄中…"):
            details = batch._scan_steps(period, areas)
            st.session_state[state_key] = (period, tuple(areas), [r for r in details if r["step"] in group_steps])
        reset_select_all(prefix)
    saved = st.session_state.get(state_key)
    if not saved or saved[:2] != (period, tuple(areas)):
        st.caption("先按「盤點」，未完成的地區×功能會自動勾選。")
        return
    rows = saved[2]
    incomplete = [row for row in rows if not row["done"]]
    if incomplete:
        st.warning(f"共 {len(rows)} 項，尚有 {len(incomplete)} 項未完整完成。")
    else:
        st.success("所選地區的全部功能均已完成。")
    override, editor_key = select_all_controls(prefix)
    table = pd.DataFrame([{"執行": checked(not row["done"], override), "地區": row["area"],
                           "功能": row["label"], "狀態": row["status"], "說明": row["note"]} for row in rows])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != "執行"])
    selected = [(row, flag) for row, flag in zip(rows, edited["執行"]) if flag]
    signature = (period, tuple(areas), tuple((row["area"], row["step"]) for row, _flag in selected))
    st.caption("勾選後先重新盤點確認；已在這段時間完成的項目會自動排除，不會重複執行。")
    if st.button(f"🔄 重新盤點並確認勾選的 {len(selected)} 項", disabled=not selected,
                 key=f"revenue_batch_precheck_{group}"):
        with st.spinner("執行前重新盤點中…"):
            refreshed = [r for r in batch._scan_steps(period, areas) if r["step"] in group_steps]
        wanted = set(signature[2])
        pending = [row for row in refreshed if (row["area"], row["step"]) in wanted and not row["done"]]
        newly_done = [row for row in refreshed if (row["area"], row["step"]) in wanted and row["done"]]
        st.session_state[confirm_key] = (signature, pending, newly_done)
    confirmation = st.session_state.get(confirm_key)
    if not confirmation or confirmation[0] != signature:
        return
    pending, newly_done = confirmation[1], confirmation[2]
    if newly_done:
        st.info("重新盤點後已完成、將不重複執行：" + "、".join(
            f"{row['area']}／{row['label']}" for row in newly_done))
    if not pending:
        st.success("重新盤點後，勾選項目都已完成，沒有需要執行的功能。")
        return
    st.warning("請再次確認要執行：\n" + "\n".join(f"- {row['area']}／{row['label']}" for row in pending))
    st.caption("確認後依編號順序執行；單一步驟失敗仍繼續下一步。")
    if st.button(f"▶️ 確認執行 {len(pending)} 項", key=f"revenue_batch_run_{group}", type="primary"):
        failures = []
        with st.spinner("執行中，請勿關閉頁面…"):
            for area in areas:
                keys = [row["step"] for row in pending if row["area"] == area]
                if keys:
                    failures += batch.run(period, [area], keys,
                        log=lambda text: log(text, "error" if text.startswith("❌") else "info"))
        st.session_state.pop(state_key, None)
        st.session_state.pop(confirm_key, None)
        if failures:
            st.error("部分失敗：\n" + "\n".join(failures))
        else:
            st.success("全部完成，請重新盤點確認。")
