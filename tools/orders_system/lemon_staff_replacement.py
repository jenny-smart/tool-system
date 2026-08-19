# ============================================================
# 檔名：lemon_staff_replacement.py
# 功能：任何訂單含檸檬人時，提供正式專員配班推薦；不限定保留單。
# 更新時間：2026-08-19
# ============================================================
# -*- coding: utf-8 -*-
from __future__ import annotations

import streamlit as st
from staff_assignment_core import AssignmentContext, StaffCandidate, rank_combinations


def build_assignment_plan(order: dict, candidate_rows: list[dict], limit: int = 20):
    ctx = AssignmentContext(
        order_no=str(order.get("order_no") or ""), service_date=str(order.get("service_date") or ""),
        period=str(order.get("period") or ""), address=str(order.get("address") or ""),
        people_needed=int(order.get("people_needed") or 2), is_vip=bool(order.get("is_vip")),
        is_returning_customer=bool(order.get("is_returning_customer")),
        gender_requirement=str(order.get("gender_requirement") or ""), height_requirement=str(order.get("height_requirement") or ""),
        body_requirement=str(order.get("body_requirement") or ""), preferred_staff_ids=set(order.get("preferred_staff_ids") or []),
        visited_staff_ids=set(order.get("visited_staff_ids") or []), complained_staff_ids=set(order.get("complained_staff_ids") or []),
        customer_tags=set(order.get("customer_tags") or []), required_skills=set(order.get("required_skills") or []),
        case_tags=set(order.get("case_tags") or []),
    )
    candidates = [StaffCandidate(
        staff_id=str(x.get("staff_id") or ""), name=str(x.get("name") or ""), rating=float(x.get("rating") or 0),
        gender=str(x.get("gender") or ""), height_cm=x.get("height_cm"), body_tags=set(x.get("body_tags") or []),
        skills=set(x.get("skills") or []), customer_fit_tags=set(x.get("customer_fit_tags") or []),
        case_fit_tags=set(x.get("case_fit_tags") or []), unavailable_case_tags=set(x.get("unavailable_case_tags") or []),
        incompatible_staff_ids=set(x.get("incompatible_staff_ids") or []), similarity_tags=set(x.get("similarity_tags") or []),
        observation=bool(x.get("observation")), intern=bool(x.get("intern")), machine_required=bool(x.get("machine_required")),
        home_to_case_minutes=x.get("home_to_case_minutes"), previous_case_to_case_minutes=x.get("previous_case_to_case_minutes"),
        case_to_home_minutes=x.get("case_to_home_minutes"), available=bool(x.get("available", True)),
        availability_reason=str(x.get("availability_reason") or ""),
    ) for x in (candidate_rows or [])]
    return rank_combinations(ctx, candidates, limit=limit)


def render(backend_email: str, backend_password: str, env: str) -> None:
    st.subheader("檸檬人換正式專員")
    st.info("適用所有含檸檬人的訂單，不限定保留單。先以正式專員能實際到客人地址為優先，再比較星等、熟客、喜愛專員、客訴與案件適配等條件。")
    st.warning("目前先提供推薦核心／模擬模式，不會自動修改正式機。下一階段接後台搜尋、真實班表與人工確認換人。")
    st.caption(f"環境：{'prod 正式機' if env == 'prod' else 'dev 測試機'}")
