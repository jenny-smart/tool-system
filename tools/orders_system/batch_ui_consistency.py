# -*- coding: utf-8 -*-
"""批次三入口 UI/runner 一致化。"""
from __future__ import annotations

import sys
from accounts import ACCOUNTS
import dev_sheet_copy
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
    from selected_row_status_guard import (_auto_filter_rows, _held_for_request, _safe_load_candidates, held_message,
                                           render_o_text_filter, render_service_date_filter, render_stage_filter,
                                           validate_service_date_range)

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
    selected_actions = st.multiselect(
        "執行項目", dev_sheet_copy.action_options(env),
        default=dev_sheet_copy.default_actions(env), key="batch_opt_actions_same_ui", label_visibility="collapsed",
    )
    st.markdown('<div class="hint-box">建單與回填優先；只有勾選改日曆且該筆已有訂單編號後，才初始化 Google Calendar。</div>', unsafe_allow_html=True)
    allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人（不動其他客人已配班專員）", value=False, key="batch_opt_allow_auto_lemon")
    st.caption("指定範圍及自動篩選可包含失敗列；調整後可直接重試，不必清空 N 欄結果。")
    st.caption("三種批次共用建單條件：訂單編號空白＋狀態未安排＋「預約條件」空白或在本次預約對象內；其他列會略過並在執行過程註明原因。")
    render_stage_filter(st, "batch_opt_booking_stages")
    service_start, service_end = render_service_date_filter(st, "batch_opt_service_date")
    auto_no_slot = st.checkbox("自動篩選：狀態未安排＋訂單編號空白＋無班表", value=False, key="batch_opt_auto_no_slot")
    auto_missing_o = st.checkbox("自動篩選：狀態未安排＋訂單編號空白＋O欄找不到訂單編號", value=False, key="batch_opt_auto_missing_o")
    o_texts = render_o_text_filter(st, batch_opt, dev_sheet_copy.read_sheet(st, env, sheet_name), region, "batch_opt_o_texts") if auto_missing_o else []
    dev_sheet_copy.render_notice(st, env, sheet_name, "batch_opt")

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
    try:
        validate_service_date_range(service_start, service_end)
    except ValueError as exc:
        st.error(str(exc))
        return
    if dev_sheet_copy.is_dev(env):
        # 測試機：先複製分頁，後續篩選、建單、回填都改用副本。
        source_sheet = sheet_name
        try:
            sheet_name = dev_sheet_copy.resolve_run_sheet(st, env, source_sheet)
        except Exception as exc:
            st.error(f"建立測試副本失敗：{exc}")
            return
        st.info(f"🧪 測試機 dev：本次使用測試副本「{sheet_name}」（原分頁「{source_sheet}」不受影響）。")

    logs = []
    def ui_log(msg):
        logs.append(str(msg or ""))
        log_box.text("\n\n".join(logs[-160:]))

    try:
        explicit_rows = _parse_rows(batch_opt, row_spec)
        requested_rows = set(explicit_rows)
        if auto_no_slot:
            requested_rows.update(_auto_filter_rows(batch_opt, sheet_name, "no_schedule", region=region, allow_failed=True,
                                                    service_start=service_start, service_end=service_end))
        if auto_missing_o:
            requested_rows.update(_auto_filter_rows(batch_opt, sheet_name, "missing_order", region=region, allow_failed=True,
                                                    o_texts=o_texts, service_start=service_start, service_end=service_end))
        requested_rows = sorted(requested_rows)
        if not requested_rows and not (auto_no_slot or auto_missing_o):
            raise ValueError("沒有指定列號，也沒有自動篩選到符合條件的列。")

        candidates = _safe_load_candidates(batch_opt, sheet_name, allow_failed=True,
                                           service_start=service_start, service_end=service_end)
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

        modes = [mode for mode, on in (("no_schedule", auto_no_slot), ("missing_order", auto_missing_o)) if on]
        held = _held_for_request(batch_opt, sheet_name, region, explicit_rows, modes, o_texts=o_texts,
                                 service_start=service_start, service_end=service_end)
        invalid = [row_no for row_no in invalid if row_no not in set(held)]
        if held:
            ui_log(f"⏭️ {held_message(batch_opt, sheet_name, held)}：" + "、".join(map(str, held)))
        if invalid:
            ui_log("⏭️ 略過不符合執行條件列：" + "、".join(map(str, invalid)))
        if region_mismatch:
            ui_log(f"⏭️ 略過非{region}列：" + "、".join(map(str, region_mismatch)))
        if not target_rows:
            if held:
                st.info(f"{held_message(batch_opt, sheet_name, held)}，沒有其他符合執行條件的列。")
                return
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
        st.success(f"執行完成：成功 {int(result.get('success_count', 0) or 0)}，失敗 {int(result.get('fail_count', 0) or 0)}，"
                   f"未選對象略過 {len(held)}，其他略過 {len(invalid) + len(region_mismatch)}。")
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
