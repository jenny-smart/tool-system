from __future__ import annotations


def render_triplicate() -> None:
    import streamlit as st
    from tools.lemon_backend import BackendClient
    from tools.lemon_backend.stored_value_sheet import get_worksheet
    from tools.local_agent_queue import create_task
    from .config import get_area_options
    from .triplicate import candidates, prepare, validate_source

    options = dict((label, key) for key, label in get_area_options())
    label = st.selectbox("執行區域", list(options), key="triplicate_area")
    st.caption("待處理發票／異動發票／K 欄改三聯。先作廢原票，再填入三聯；開立時仍由你按下一步及儲存。")
    try:
        ws = get_worksheet(label)
        items = candidates(ws.get_all_values())
        if not items:
            st.info("沒有待處理的二聯改三聯資料")
            return
        item = st.selectbox("選擇訂單", items,
                            format_func=lambda r: f"第 {r['source_row']} 列｜{r['order_no']}｜{r['customer']}")
        st.text(item["note"])
        if st.button("讀取後台並預覽", key="triplicate_preview"):
            st.session_state.pop("triplicate_plan", None)
            validate_source(ws, item)
            st.session_state["triplicate_plan"] = (label, prepare(options[label], item, BackendClient(options[label])))
        saved = st.session_state.get("triplicate_plan")
        if not saved or saved[0] != label or saved[1]["source"] != item:
            return
        plan = saved[1]
        data = plan["payload"]
        st.write({"原發票": item["old_invoice"], "付款日期": plan["paid_date"], "作廢原因": "開立錯誤",
                  "買方名稱": data["buyer_name"], "買方統編": data["buyer_identifier"],
                  "發票地址": data["buyer_address"] or "（空白）", "含稅總額": data["totalamount"]})
        st.caption("K 欄其他需求請一併核對。新發票回填 O／AA，X／Y 保留原發票記錄。")
        if st.button("執行二聯改三聯", type="primary"):
            validate_source(ws, item)
            create_task("cetustek.triplicate", {"area": label, "plan": plan},
                        created_by=st.session_state.get("username", "Tool System"))
            st.session_state.pop("triplicate_plan", None)
            st.success("已加入本機 Agent 佇列；請查看執行進度及鯨躍畫面")
    except Exception as exc:
        st.error(f"二聯改三聯：{exc}")
