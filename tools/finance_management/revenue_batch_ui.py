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


def render(log, username=""):
    last_month = datetime.now(ZoneInfo("Asia/Taipei")).date().replace(day=1) - timedelta(days=1)
    period = st.text_input("期別（YYYYMM-2）", value=last_month.strftime("%Y%m-2"), key="revenue_batch_period")
    areas = st.multiselect("盤點地區（可複選）", batch.AREAS, default=batch.AREAS, key="revenue_batch_areas")
    if st.button("🔍 盤點", type="primary", disabled=not areas, key="revenue_batch_scan"):
        with st.spinner("讀取各地區完成紀錄中…"):
            st.session_state[STATE_KEY] = (period, tuple(areas), batch.scan(period, areas))
        reset_select_all(PREFIX)
    saved = st.session_state.get(STATE_KEY)
    if not saved or saved[:2] != (period, tuple(areas)):
        st.caption("先按「盤點」，未完成的地區×功能會自動勾選。")
        return
    rows = saved[2]
    incomplete = [row for row in rows if not row["done"]]
    if incomplete:
        st.warning(f"共 {len(rows)} 項，尚有 {len(incomplete)} 項未完整完成。")
    else:
        st.success("所選地區的全部功能均已完成。")
    override, editor_key = select_all_controls(PREFIX)
    table = pd.DataFrame([{"執行": checked(not row["done"], override), "地區": row["area"],
                           "功能": row["label"], "狀態": row["status"], "說明": row["note"]} for row in rows])
    edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                            disabled=[c for c in table.columns if c != "執行"])
    selected = [(row, flag) for row, flag in zip(rows, edited["執行"]) if flag]
    signature = (period, tuple(areas), tuple((row["area"], row["step"]) for row, _flag in selected))
    st.caption("勾選後先重新盤點確認；已在這段時間完成的項目會自動排除，不會重複執行。")
    if st.button(f"🔄 重新盤點並確認勾選的 {len(selected)} 項", disabled=not selected,
                 key="revenue_batch_precheck"):
        with st.spinner("執行前重新盤點中…"):
            refreshed = batch.scan(period, areas)
        wanted = set(signature[2])
        pending = [row for row in refreshed if (row["area"], row["step"]) in wanted and not row["done"]]
        newly_done = [row for row in refreshed if (row["area"], row["step"]) in wanted and row["done"]]
        st.session_state[CONFIRM_KEY] = (signature, pending, newly_done)
    confirmation = st.session_state.get(CONFIRM_KEY)
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
    st.caption("確認後依序執行 ⑥→⑦→⑧→⑨→⑪→⑫，再貼入財報；單一步驟失敗仍繼續下一步。")
    if st.button(f"▶️ 確認執行 {len(pending)} 項", key="revenue_batch_run", type="primary"):
        failures = []
        with st.spinner("執行中，請勿關閉頁面…"):
            for area in areas:
                keys = batch.expand_groups([row["step"] for row in pending if row["area"] == area])
                if keys:
                    failures += batch.run(period, [area], keys,
                        log=lambda text: log(text, "error" if text.startswith("❌") else "info"))
        st.session_state.pop(STATE_KEY, None)
        st.session_state.pop(CONFIRM_KEY, None)
        if failures:
            st.error("部分失敗：\n" + "\n".join(failures))
        else:
            st.success("全部完成，請重新盤點確認。")
