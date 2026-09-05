# -*- coding: utf-8 -*-
"""修正檸檬人補班：只排除與目標時段衝突的既有班，不再因當天任何班別就整天排除。"""
from __future__ import annotations


def _install_for(module):
    if not hasattr(module, "_get_cleaner_shift_form_info"):
        return

    def _set_cleaner_shift_if_available(session, base_url, cleaner_id, cleaner_name, date_str, target_shift_code):
        csrf, checked_fields, checked_codes, err = module._get_cleaner_shift_form_info(
            session, base_url, cleaner_id, date_str
        )
        if err:
            return {
                "success": False,
                "name": cleaner_name,
                "id": cleaner_id,
                "reason": err,
                "checked": sorted(checked_codes),
            }

        target_shift_code = module._shift_value_to_code(target_shift_code)

        # 同一時段已經有班：不要重複勾，避免搶到既有訂單已使用的人力。
        if target_shift_code in checked_codes:
            return {
                "success": False,
                "name": cleaner_name,
                "id": cleaner_id,
                "reason": f"{date_str} {target_shift_code} 已勾班（可能已有其他訂單使用），換下一位",
                "checked": sorted(checked_codes),
                "already_checked": True,
            }

        # 核心修正：只檢查真正衝突的班別。
        # 例如目標 14:00-17:00（下3），上午上2/上3/上4不衝突，可繼續補下午班。
        conflicts = sorted(
            code for code in checked_codes
            if module._shift_codes_conflict(code, target_shift_code)
        )
        if conflicts:
            return {
                "success": False,
                "name": cleaner_name,
                "id": cleaner_id,
                "reason": f"{date_str} 已勾 {'、'.join(conflicts)}，與 {target_shift_code} 衝突",
                "checked": sorted(checked_codes),
                "protected_existing_shift": True,
            }

        target_name = f"shift_{date_str}_{module._shift_code_to_group(target_shift_code)}"
        target_value = module._shift_code_to_value(target_shift_code)
        fields = []
        if csrf:
            fields.append(("_token", csrf))

        # 保留原本所有已勾班別，只新增這次目標時段；不覆蓋、不清除任何既有班。
        seen = set()
        for name, value in checked_fields:
            key = (name, value)
            if key in seen:
                continue
            seen.add(key)
            fields.append((name, value))
        if (target_name, target_value) not in seen:
            fields.append((target_name, target_value))

        resp = session.post(
            f"{base_url}/cleaner1/{cleaner_id}/shift",
            params={"month": str(date_str)[:7]},
            data=fields,
            headers=module.HEADERS,
            allow_redirects=True,
        )
        ok = resp.status_code in (200, 302)

        # 寫入成功不代表後台真的存到；重讀一次確認目標班別存在。
        if ok:
            _csrf2, _fields2, checked_after, err2 = module._get_cleaner_shift_form_info(
                session, base_url, cleaner_id, date_str
            )
            ok = not err2 and target_shift_code in checked_after

        return {
            "success": ok,
            "name": cleaner_name,
            "id": cleaner_id,
            "message": (
                f"{cleaner_name} 已補勾 {date_str} {target_shift_code}"
                if ok
                else f"POST 後未確認到班別 {date_str} {target_shift_code}（HTTP {resp.status_code}）"
            ),
            "checked": sorted(checked_codes),
            "target": target_shift_code,
        }

    module._set_cleaner_shift_if_available = _set_cleaner_shift_if_available


def install_patch():
    import orders
    _install_for(orders)

    # 舊客／新客快速建單使用 quick_order.py；同步套用同一規則，避免兩套邏輯再次分叉。
    try:
        import quick_order
        _install_for(quick_order)
    except Exception:
        pass
