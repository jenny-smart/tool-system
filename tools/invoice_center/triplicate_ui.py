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
    action_label = st.radio("原發票處理方式", ["作廢原發票", "開立折讓單（全額）"], key="triplicate_original_action")
    action = "cancel" if action_label == "作廢原發票" else "allowance"
    st.caption("先完成原票作廢或全額折讓，再填入新三聯發票；新票仍由你按下一步及儲存。")
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
            st.session_state["triplicate_plan"] = (label, prepare(options[label], item, BackendClient(options[label]), original_action=action))
        saved = st.session_state.get("triplicate_plan")
        if not saved or saved[0] != label or saved[1]["source"] != item or saved[1].get("original_action", "cancel") != action:
            return
        plan = saved[1]
        data = plan["payload"]
        st.write({"原發票": item["old_invoice"], "付款日期": plan["paid_date"], "原票處理": action_label,
                  **({"作廢原因": "開立錯誤"} if action == "cancel" else {"折讓範圍": "原票全額", "折讓金額": plan["total"]}),
                  "買方名稱": data["buyer_name"], "買方統編": data["buyer_identifier"],
                  "發票地址": data["buyer_address"] or "（空白）", "含稅總額": data["totalamount"]})
        st.caption("K 欄其他需求請一併核對。新發票回填 O／AA，X／Y 保留原發票記錄，折讓單號記錄於 AB。")
        if st.button("執行二聯改三聯", type="primary"):
            validate_source(ws, item)
            create_task("cetustek.triplicate", {"area": label, "plan": plan},
                        created_by=st.session_state.get("username", "Tool System"))
            st.session_state.pop("triplicate_plan", None)
            st.success("已加入本機 Agent 佇列；請查看執行進度及鯨躍畫面")
    except Exception as exc:
        st.error(f"二聯改三聯：{exc}")
