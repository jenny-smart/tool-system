from __future__ import annotations

import json


ACTIVE_TASK_STATUSES = {"pending", "queued", "running", "cancel_requested"}


def _active_triplicate_task(tasks, area_label: str, order_no: str):
    for task in tasks:
        if task.get("action") != "cetustek.triplicate" or task.get("status") not in ACTIVE_TASK_STATUSES:
            continue
        try:
            params = json.loads(task.get("params_json") or "{}")
        except (TypeError, ValueError):
            continue
        source = ((params.get("plan") or {}).get("source") or {})
        if str(params.get("area") or "") == area_label and str(source.get("order_no") or "") == order_no:
            return task
    return None


def render_triplicate() -> None:
    import streamlit as st
    from tools.lemon_backend import BackendClient
    from tools.lemon_backend.stored_value_sheet import get_worksheet
    from tools.local_agent_queue import create_task, list_tasks
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
        try:
            active_task = _active_triplicate_task(list_tasks(limit=200), label, item["order_no"])
        except Exception as exc:
            active_task = None
            st.warning(f"無法確認重複任務：{exc}")
        if active_task:
            st.warning(f"此訂單已有{active_task.get('status')}任務，完成或中止前不能再次執行。")
        st.write({"原發票": item["old_invoice"], "付款日期": plan["paid_date"], "原票處理": action_label,
                  **({"作廢原因": "開立錯誤"} if action == "cancel" else {"折讓範圍": "原票全額", "折讓金額": plan["total"]}),
                  "買方名稱": data["buyer_name"], "買方統編": data["buyer_identifier"],
                  "發票地址": data["buyer_address"] or "（空白）", "含稅總額": data["totalamount"]})
        st.caption("K 欄其他需求請一併核對。新發票回填 O／AA，B 改為已處理發票，K 加註原票與新票號碼；X／Y 保留，折讓單號記錄於 AB。")
        resume_no = ""
        if action == "allowance":
            values = ws.get(f"AB{item['source_row']}")
            detected_no = str(values[0][0]).strip().upper() if values and values[0] else ""
            if detected_no:
                st.info(f"已偵測折讓單號：{detected_no}；將直接接續開立發票，不再折讓。")
            else:
                resume_no = st.text_input("已開立但尚未回填的折讓單號（選填）",
                                          key=f"triplicate_resume_{label}_{item['old_invoice']}").strip().upper()
                st.caption("若 AB 欄已有折讓單號會自動偵測；只有尚未回填時才需手動輸入。")
        retry_unissued = st.checkbox(
            "上次已填入，但我沒有按儲存：確認無新發票後重新填入",
            key=f"triplicate_retry_{label}_{item['old_invoice']}",
        )
        st.caption("程式會先查詢新發票；若已開立只補回填，若確定未開立才重填，不重複作廢／折讓原票。")
        button_label = (
            "確認未儲存並重新填入" if retry_unissued else
            "回填折讓單號並接續新發票" if resume_no else
            "執行二聯改三聯"
        )
        if st.button(button_label, type="primary", disabled=bool(active_task)):
            validate_source(ws, item)
            duplicate = _active_triplicate_task(list_tasks(limit=200), label, item["order_no"])
            if duplicate:
                st.warning("此訂單已有等待中或執行中的任務，未重複送出。")
                return
            dispatch_plan = {
                **plan,
                **({"resume_allowance_no": resume_no} if resume_no else {}),
                **({"retry_unissued": True} if retry_unissued else {}),
            }
            create_task("cetustek.triplicate", {"area": label, "plan": dispatch_plan},
                        created_by=st.session_state.get("username", "Tool System"))
            st.session_state.pop("triplicate_plan", None)
            st.success("已加入本機 Agent 佇列；請查看執行進度及鯨躍畫面")
    except Exception as exc:
        st.error(f"二聯改三聯：{exc}")
