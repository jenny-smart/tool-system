# -*- coding: utf-8 -*-
"""批次三入口 UI/runner 一致化。"""
from __future__ import annotations

import sys
from accounts import ACCOUNTS
from hybrid_batch_runner import run_process_web_direct_single, run_process_web_hybrid


def _bind_original_batch_to_single_runner() -> None:
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            filename = str(getattr(module, "__file__", "") or "")
            name = str(getattr(module, "__name__", "") or "")
            if filename.endswith("ordersapp.py") or name in ("ordersapp", "__main__"):
                if hasattr(module, "run_process_web"):
                    setattr(module, "run_process_web", run_process_web_direct_single)
        except Exception:
            continue


def _parse_rows(batch_opt, raw: str) -> list[int]:
    raw = str(raw or "").strip()
    return [] if not raw else batch_opt._parse_row_spec(raw)


def _region_credentials(region: str, fallback_email: str, fallback_password: str):
    account = ACCOUNTS.get(region) or {}
    return (
        str(account.get("email") or fallback_email or "").strip(),
        str(account.get("password") or fallback_password or "").strip(),
    )


def render_optimized_like_batch(backend_email: str, backend_password: str, env: str) -> None:
    import batch_booking_optimized as batch_opt
    from selected_row_status_guard import _auto_filter_rows

    st = batch_opt.st
    batch_opt.step("4", "執行設定")
    c1, c2, c3 = st.columns(3)
    regions = list(ACCOUNTS.keys()) or ["台北"]
    default_index = regions.index("台北") if "台北" in regions else 0
    with c1:
        region = st.selectbox("執行區域", regions, index=default_index, key="batch_opt_region")
    with c2:
        sheet_name = st.text_input("工作表名稱", placeholder="例：台北202610", key="batch_opt_sheet_name").strip()
    with c3:
        row_spec = st.text_input("執行列號", placeholder="例：2,3,5-7", key="batch_opt_row_spec").strip()
    st.markdown('<div class="hint-box">💡 列號支援：單列 <code>2</code>、逗號分隔 <code>2,3,5</code>、區間 <code>2,3,5-7</code>。範圍內不符合條件的列會略過，不會中止整批。</div>', unsafe_allow_html=True)
    st.markdown("<hr>", unsafe_allow_html=True)

    batch_opt.step("3", "執行項目")
    default_actions = ["建單", "寄確認信", "改 Google 日曆"] if env == "prod" else ["建單"]
    selected_actions = st.multiselect(
        "執行項目", ["建單", "寄確認信", "改 Google 日曆"],
        default=default_actions, key="batch_opt_actions_same_ui", label_visibility="collapsed",
    )
    st.markdown('<div class="hint-box">建單與回填優先；只有勾選改日曆且該筆已有訂單編號後，才初始化 Google Calendar。</div>', unsafe_allow_html=True)
    allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人（不動其他客人已配班專員）", value=False, key="batch_opt_allow_auto_lemon")
    auto_no_slot = st.checkbox("自動篩選：狀態未安排＋訂單編號空白＋無班表", value=False, key="batch_opt_auto_no_slot")
    auto_missing_o = st.checkbox("自動篩選：狀態未安排＋訂單編號空白＋O欄找不到訂單編號", value=False, key="batch_opt_auto_missing_o")

    run_clicked = st.button("🚀  開始執行", use_container_width=True, key="batch_opt_same_ui_run")
    with st.expander("📄  執行過程", expanded=True):
        log_box = st.empty()
        if not run_clicked:
            log_box.text("尚未執行")
    if not run_clicked:
        return
    if not sheet_name:
        st.error("請輸入工作表名稱。")
        return
    if not selected_actions:
        st.error("請至少選擇一個執行項目。")
        return

    logs = []
    def ui_log(msg):
        logs.append(str(msg or ""))
        log_box.text("\n\n".join(logs[-160:]))

    try:
        requested_rows = set(_parse_rows(batch_opt, row_spec))
        if auto_no_slot:
            requested_rows.update(_auto_filter_rows(batch_opt, sheet_name, "no_schedule", region=region))
        if auto_missing_o:
            requested_rows.update(_auto_filter_rows(batch_opt, sheet_name, "missing_order", region=region))
        requested_rows = sorted(requested_rows)
        if not requested_rows:
            raise ValueError("沒有指定列號，也沒有自動篩選到符合條件的列。")

        candidates = batch_opt._load_candidates(sheet_name)
        candidate_map = {int(row["__sheet_row__"]): row for _, row in candidates.iterrows()}
        target_rows, invalid, region_mismatch = [], [], []
        for row_no in requested_rows:
            row = candidate_map.get(row_no)
            if row is None:
                invalid.append(row_no)
                continue
            if batch_opt.get_region_by_address(batch_opt._text(row.get("地址")), ACCOUNTS) != region:
                region_mismatch.append(row_no)
                continue
            target_rows.append(row_no)

        if invalid:
            ui_log("⏭️ 略過不符合執行條件列：" + "、".join(map(str, invalid)))
        if region_mismatch:
            ui_log(f"⏭️ 略過非{region}列：" + "、".join(map(str, region_mismatch)))
        if not target_rows:
            raise ValueError("指定範圍內沒有符合執行條件的列。")

        email, password = _region_credentials(region, backend_email, backend_password)
        if not email or not password:
            raise RuntimeError(f"{region} 尚未設定後台帳號密碼")
        ui_log(f"實際執行列號：{'、'.join(map(str, target_rows))}")
        ui_log("批次優化：2 筆以上同組先批次；單筆組改走逐筆。每個多筆組建單完成後一次 batch_update 回填該組結果，再處理日曆。")
        result = run_process_web_hybrid(
            env_name=env, region=region, backend_email=email, backend_password=password,
            sheet_name=sheet_name, start_row=min(target_rows), end_row=max(target_rows),
            selected_actions=selected_actions, logger=ui_log,
            allow_auto_lemon_shift=allow_auto_lemon, selected_rows=target_rows,
        ) or {}
        st.success(f"執行完成：成功 {int(result.get('success_count', 0) or 0)}，失敗 {int(result.get('fail_count', 0) or 0)}，略過 {len(invalid) + len(region_mismatch)}。")
    except Exception as exc:
        ui_log(f"❌ 執行失敗：{exc}")
        st.error(f"執行失敗：{exc}")


def install() -> None:
    _bind_original_batch_to_single_runner()
    try:
        import batch_booking_optimized as batch_opt
        batch_opt.render = render_optimized_like_batch
        batch_opt.run_process_web = run_process_web_hybrid
    except Exception:
        pass
