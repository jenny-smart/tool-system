"""依 J 欄代墊標記，從行銷費用矩陣拆出跨區代墊明細。

J 欄格式：YYYYMM地區代墊，例如 ``202605桃園代墊``；若只補單一科目，可用
``YYYYMM地區代墊-來源科目``，例如 ``202605桃園代墊-內勤薪資``。
來源金額來自「財報設定」G/H 欄所指向的行銷費用分頁；科目及雙方會計處理則
由主控試算表的「代墊費用設定」分頁管理。原始銀行總額列會改成第一筆明細，
其餘明細插在其下。本次所有待執行 J 欄列的銀行總額，會與本次實際拆出的代墊
明細淨額合併勾稽，允許跨月、跨科目一次執行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import (
    resolve_marketing_expense_location,
    resolve_statement_location,
)

SETTINGS_SHEET_NAME = "代墊費用設定"
SETTINGS_HEADER = [
    "啟用", "來源科目", "財報 I 欄科目", "台北處理", "支付區處理", "L 欄後綴",
]
DEFAULT_SETTINGS: list[list[object]] = [
    [True, "google行銷費", "Google行銷", "費用減項：E欄負數", "費用：E欄正數", "Google行銷費-支付區"],
    [True, "google管理費", "Google代理商服務", "費用減項：E欄負數", "費用：E欄正數", "Google代理商服務-支付區"],
    [True, "2%", "分店收入-2", "收入：F欄正數", "費用：E欄正數", "2%-支付區"],
    [True, "內勤薪資-顧問費", "內勤薪資", "費用減項：E欄負數", "費用：E欄正數", "內勤薪資-顧問費-支付區"],
    [True, "內勤薪資", "內勤薪資", "費用減項：E欄負數", "費用：E欄正數", "內勤薪資-支付區"],
    [True, "內勤勞保費", "內勤勞保費", "費用減項：E欄負數", "費用：E欄正數", "內勤勞保費-支付區"],
    [True, "內勤健保費", "內勤健保費", "費用減項：E欄負數", "費用：E欄正數", "內勤健保費-支付區"],
    [True, "內勤退休金", "內勤退休金", "費用減項：E欄負數", "費用：E欄正數", "內勤退休金-支付區"],
    [True, "稅捐", "稅捐", "費用：E欄正數", "費用減項：E欄負數", "稅捐-支付區"],
    [True, "#65", "內勤勞保費", "費用減項：E欄負數", "費用：E欄正數", "內勤勞保費-支付區"],
    [True, "#66", "內勤健保費", "費用減項：E欄負數", "費用：E欄正數", "內勤健保費-支付區"],
]

TW_TZ = ZoneInfo("Asia/Taipei")
MARKER_RE = re.compile(r"^(20\d{2})(0[1-9]|1[0-2])(.+?)代墊(?:-(.+))?$")
CONTROL_LABEL = "內部資金收入/支出"
COL_E, COL_F, COL_I, COL_J, COL_L, COL_M, COL_Q, COL_R = 5, 6, 9, 10, 12, 13, 17, 18


@dataclass(frozen=True)
class ExpenseRule:
    source_key: str
    account: str
    taipei_treatment: str
    payer_treatment: str
    suffix: str


def _cell(row: list[object], col: int) -> object:
    idx = col - 1
    return row[idx] if idx < len(row) else ""


def _to_bool(value: object) -> bool:
    return str(value or "").strip().upper() in ("TRUE", "1", "YES", "Y")


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _normalize_source_key(value: object) -> str:
    text = _normalize_text(value)
    if text in ("0.02", "2.0%", "2％"):
        return "2%"
    return text


def _normalize_month(value: object) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"(20\d{2})[./-]?(0?[1-9]|1[0-2])(?:月)?", text)
    if not match:
        return None
    return f"{match.group(1)}{int(match.group(2)):02d}"


def _amount(value: object) -> int:
    if isinstance(value, bool) or value in (None, ""):
        return 0
    text = str(value).replace(",", "").replace("NT$", "").strip()
    if not text or text in ("-", "—"):
        return 0
    try:
        return int(Decimal(text).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise RuntimeError(f"代墊來源金額不是數字：{value}") from exc


def _parse_date(value: object):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = str(value or "").strip().split(" ", 1)[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _statement_row_date(row: list[object]):
    """B 欄有時只顯示 MM/DD；改由 B、K、C 依序找可解析的完整日期。"""
    for col in (2, 11, 3):
        parsed = _parse_date(_cell(row, col))
        if parsed is not None:
            return parsed
    return None


def _ensure_settings_sheet(service, master_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=master_id, fields="sheets.properties.title"
    ).execute()
    titles = {sheet["properties"]["title"] for sheet in meta.get("sheets", [])}
    if SETTINGS_SHEET_NAME in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=master_id,
        body={"requests": [{"addSheet": {"properties": {"title": SETTINGS_SHEET_NAME}}}]},
    ).execute()
    rows = [SETTINGS_HEADER, *DEFAULT_SETTINGS]
    service.spreadsheets().values().update(
        spreadsheetId=master_id,
        range=f"'{SETTINGS_SHEET_NAME}'!A1:F{len(rows)}",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def load_expense_rules() -> list[ExpenseRule]:
    service = get_sheets_service()
    master_id = get_master_spreadsheet_id()
    _ensure_settings_sheet(service, master_id)
    res = service.spreadsheets().values().get(
        spreadsheetId=master_id,
        range=f"'{SETTINGS_SHEET_NAME}'!A2:F",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    rules: list[ExpenseRule] = []
    for row in res.get("values", []):
        if not _to_bool(_cell(row, 1)):
            continue
        source_key = _normalize_source_key(_cell(row, 2))
        account = str(_cell(row, 3) or "").strip()
        taipei_treatment = str(_cell(row, 4) or "").strip()
        payer_treatment = str(_cell(row, 5) or "").strip()
        suffix = str(_cell(row, 6) or "").strip()
        if not all((source_key, account, taipei_treatment, payer_treatment, suffix)):
            raise RuntimeError(f"「{SETTINGS_SHEET_NAME}」有啟用列缺少必要欄位：{row}")
        rules.append(ExpenseRule(source_key, account, taipei_treatment, payer_treatment, suffix))
    if not rules:
        raise RuntimeError(f"「{SETTINGS_SHEET_NAME}」沒有啟用的代墊規則")
    return rules


def _select_rules(rules: list[ExpenseRule], subject: str | None) -> list[ExpenseRule]:
    """有指定 ``-科目`` 時只執行該科目；未指定則沿用所有 A=TRUE 規則。"""
    if not subject:
        return rules
    wanted = _normalize_source_key(subject)
    source_matches = [rule for rule in rules if _normalize_source_key(rule.source_key) == wanted]
    if source_matches:
        return source_matches
    account_matches = [rule for rule in rules if _normalize_text(rule.account) == _normalize_text(subject)]
    if len(account_matches) == 1:
        return account_matches
    if len(account_matches) > 1:
        raise RuntimeError(
            f"代墊科目「{subject}」對應多個來源科目，請改用「代墊費用設定」B欄來源科目"
        )
    enabled = "、".join(rule.source_key for rule in rules)
    raise RuntimeError(f"找不到啟用中的代墊科目「{subject}」；可用來源科目：{enabled}")


def _read_statement_values(spreadsheet_id: str, title: str) -> list[list[object]]:
    service = get_sheets_service()
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:R",
        valueRenderOption="FORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return res.get("values", [])


def _read_marketing_values(spreadsheet_id: str, title: str) -> list[list[object]]:
    service = get_sheets_service()
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1:ZZ200",
        valueRenderOption="FORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return res.get("values", [])


def _sheet_id_for_title(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == title:
            return int(props["sheetId"])
    raise RuntimeError(f"試算表 {spreadsheet_id} 找不到分頁「{title}」")


def _find_source_column(values: list[list[object]], month: str, payer_area: str) -> int:
    if len(values) < 2:
        raise RuntimeError("行銷費用來源缺少年月列或地區列")
    width = max(len(values[0]), len(values[1]))
    active_month: str | None = None
    for col in range(1, width + 1):
        explicit_month = _normalize_month(_cell(values[0], col))
        if explicit_month:
            active_month = explicit_month
        region = _normalize_text(_cell(values[1], col))
        if active_month == month and region == payer_area:
            return col
    raise RuntimeError(f"行銷費用來源找不到 {month[:4]}.{month[4:]}／{payer_area} 的交叉欄")


def _source_rows(values: list[list[object]], source_col: int) -> list[tuple[int, str, int]]:
    rows = []
    for row_number, row in enumerate(values[2:], start=3):
        key = _normalize_source_key(_cell(row, 2))
        rows.append((row_number, key, _amount(_cell(row, source_col))))
    return rows


def _control_total(values: list[list[object]], source_col: int) -> int:
    totals = [
        _amount(_cell(row, source_col))
        for row in values
        if _normalize_text(_cell(row, 1)) == CONTROL_LABEL
    ]
    if not totals:
        raise RuntimeError(f"行銷費用來源找不到「{CONTROL_LABEL}」控制總額")
    return totals[-1]


def _rule_amount(rule: ExpenseRule, source_rows: list[tuple[int, str, int]]) -> int:
    if rule.source_key.startswith("#") and rule.source_key[1:].isdigit():
        wanted = int(rule.source_key[1:])
        return next((amount for row_no, _, amount in source_rows if row_no == wanted), 0)
    matches = [amount for _, key, amount in source_rows if key == rule.source_key]
    if len(matches) > 1:
        raise RuntimeError(f"行銷費用來源科目「{rule.source_key}」出現多列，請改用 #列號 指定")
    return matches[0] if matches else 0


def _posting(amount: int, treatment: str) -> tuple[int, int]:
    text = _normalize_text(treatment)
    amount = abs(amount)
    if "F欄" in text and "收入" in text:
        return 0, amount
    if "E欄" not in text:
        raise RuntimeError(f"不支援的代墊處理方式：{treatment}")
    if "負數" in text or "減項" in text:
        return -amount, 0
    if "正數" in text or "費用" in text:
        return amount, 0
    raise RuntimeError(f"不支援的代墊處理方式：{treatment}")


def _build_details(
    rules: list[ExpenseRule], source_rows: list[tuple[int, str, int]], month: str,
    payer_area: str, report_area: str, control_total: int | None = None,
) -> list[dict[str, object]]:
    side = "台北" if report_area == "台北" else "支付區"
    details: list[dict[str, object]] = []
    for rule in rules:
        amount = _rule_amount(rule, source_rows)
        if amount == 0:
            continue
        treatment = rule.taipei_treatment if side == "台北" else rule.payer_treatment
        e_value, f_value = _posting(amount, treatment)
        suffix = rule.suffix.replace("支付區", payer_area)
        details.append({
            "account": rule.account,
            "e": e_value,
            "f": f_value,
            "label": f"{month[:4]}.{month[4:]}-{suffix}",
        })
    if not details:
        raise RuntimeError(f"{month[:4]}.{month[4:]}／{payer_area} 沒有可拆分的代墊明細")

    if control_total is not None:
        expected_signed = control_total if report_area == "台北" else -control_total
        actual_signed = sum(int(item["f"]) - int(item["e"]) for item in details)
        residual = expected_signed - actual_signed
        tolerance = max(2, len(details))
        if abs(residual) > tolerance:
            raise RuntimeError(
                f"代墊明細無法勾稽：來源淨額 {control_total:,}，規則明細淨額 "
                f"{actual_signed:,}，差額 {residual:,}；請檢查「{SETTINGS_SHEET_NAME}」是否漏科目"
            )
        if residual:
            first = details[0]
            if int(first["f"]):
                first["f"] = int(first["f"]) + residual
            else:
                first["e"] = int(first["e"]) - residual
    return details


def _detail_row(source_row: list[object], detail: dict[str, object], marker: str, now_text: str) -> list[object]:
    row = list(source_row)
    while len(row) < COL_R:
        row.append("")
    row[COL_E - 1] = detail["e"]
    row[COL_F - 1] = detail["f"]
    row[COL_I - 1] = detail["account"]
    row[COL_J - 1] = marker
    row[COL_L - 1] = detail["label"]
    row[COL_M - 1] = detail["e"] if int(detail["e"]) else ""
    row[COL_Q - 1] = now_text
    row[COL_R - 1] = f"代墊拆分：I={detail['account']}；E={detail['e']}；F={detail['f']}；L={detail['label']}"
    return row


def apply_intercompany_expense_rules(area: str, start_date=None, end_date=None) -> dict[str, object]:
    """執行代墊費用拆分，並在主控檔記錄開始、完成或失敗。"""
    date_range = f"{start_date or ''}~{end_date or ''}" if (start_date or end_date) else "全部"
    log_execution("財報富邦更新代墊費用拆分", area, "開始", f"日期區間：{date_range}")
    try:
        result = _apply_intercompany_expense_rules_impl(area, start_date, end_date)
    except Exception as exc:
        log_execution("財報富邦更新代墊費用拆分", area, "失敗", str(exc))
        raise
    completion_detail = f"更新 {result['updated_rows']} 列，插入 {result['inserted_rows']} 列"
    if result.get("diagnostics"):
        completion_detail += f"；{result['diagnostics']}"
    log_execution("財報富邦更新代墊費用拆分", area, "完成", completion_detail)
    return result


def _apply_intercompany_expense_rules_impl(area: str, start_date=None, end_date=None) -> dict[str, object]:
    """處理尚未蓋 Q 欄的 ``YYYYMM地區代墊[-科目]`` 標記。"""
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
    statement_values = _read_statement_values(spreadsheet_id, title)
    if len(statement_values) < 2:
        return {
            "updated_rows": 0,
            "inserted_rows": 0,
            "diagnostics": f"試算表ID={spreadsheet_id}；分頁「{title}」；掃描 0 列；工作表沒有資料列",
        }

    marker_rows: list[tuple[int, list[object], str, str, str, str | None]] = []
    j_nonblank_count = keyword_count = matched_count = processed_count = date_excluded_count = 0
    j_nonblank_samples: list[str] = []
    marker_samples: list[str] = []
    for row_idx, row in enumerate(statement_values[1:], start=2):
        marker = str(_cell(row, COL_J) or "").strip()
        if marker:
            j_nonblank_count += 1
            j_nonblank_samples.append(f"J{row_idx}={marker}")
            if len(j_nonblank_samples) > 5:
                j_nonblank_samples.pop(0)
        if "代墊" in marker:
            keyword_count += 1
            if len(marker_samples) < 3:
                marker_samples.append(f"J{row_idx}={marker}")
        match = MARKER_RE.fullmatch(marker)
        if not match:
            continue
        matched_count += 1
        if str(_cell(row, COL_Q) or "").strip():
            processed_count += 1
            continue
        row_date = _statement_row_date(row)
        if (start_date or end_date) and row_date is None:
            raise RuntimeError(f"J{row_idx} 已辨識為「{marker}」，但 B／K／C 欄日期都無法解析")
        if start_date and row_date < start_date:
            date_excluded_count += 1
            continue
        if end_date and row_date > end_date:
            date_excluded_count += 1
            continue
        month = f"{match.group(1)}{match.group(2)}"
        payer_area = match.group(3).strip()
        subject = match.group(4).strip() if match.group(4) else None
        if payer_area == "台北":
            raise RuntimeError(f"J{row_idx} 的支付區不能是台北：{marker}")
        if area != "台北" and area != payer_area:
            raise RuntimeError(f"J{row_idx} 標示 {payer_area} 代墊，但目前執行地區是 {area}")
        marker_rows.append((row_idx, row, marker, month, payer_area, subject))

    if not marker_rows:
        diagnostics = (
            f"試算表ID={spreadsheet_id}；分頁「{title}」；掃描 {len(statement_values) - 1} 列；"
            f"J欄非空 {j_nonblank_count} 筆；J欄含代墊 {keyword_count} 筆；格式符合 {matched_count} 筆；"
            f"Q欄已處理 {processed_count} 筆；日期排除 {date_excluded_count} 筆；待處理 0 筆"
        )
        if marker_samples:
            diagnostics += "；代墊樣本：" + "、".join(marker_samples)
        elif j_nonblank_samples:
            diagnostics += "；J欄末筆樣本：" + "、".join(j_nonblank_samples)
        return {"updated_rows": 0, "inserted_rows": 0, "diagnostics": diagnostics}

    markers = [item[2] for item in marker_rows]
    duplicates = sorted({marker for marker in markers if markers.count(marker) > 1})
    if duplicates:
        raise RuntimeError(f"同一財報有重複代墊標記：{', '.join(duplicates)}")

    rules = load_expense_rules()
    marketing_id, marketing_title = resolve_marketing_expense_location("台北")
    marketing_values = _read_marketing_values(marketing_id, marketing_title)
    existing_labels = {
        str(_cell(row, COL_L) or "").strip()
        for row in statement_values[1:]
        if str(_cell(row, COL_L) or "").strip()
    }
    planned_labels: set[str] = set()
    plans: list[tuple[int, list[list[object]]]] = []
    now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    bank_total_sum = 0
    expected_signed_sum = 0
    detail_count = 0

    for row_idx, source_row, marker, month, payer_area, subject in marker_rows:
        source_col = _find_source_column(marketing_values, month, payer_area)
        selected_rules = _select_rules(rules, subject)
        control_total = _control_total(marketing_values, source_col) if subject is None else None
        details = _build_details(
            selected_rules,
            _source_rows(marketing_values, source_col),
            month,
            payer_area,
            area,
            control_total,
        )
        expected_labels = {str(detail["label"]) for detail in details}
        duplicate_labels = sorted(expected_labels & (existing_labels | planned_labels))
        if duplicate_labels:
            raise RuntimeError(f"代墊明細已存在，未重複插入：{', '.join(duplicate_labels)}")
        planned_labels.update(expected_labels)

        bank_total_sum += abs(_amount(_cell(source_row, COL_F)) - _amount(_cell(source_row, COL_E)))
        expected_signed_sum += sum(int(item["f"]) - int(item["e"]) for item in details)
        detail_count += len(details)
        rows = [_detail_row(source_row, detail, marker, now_text) for detail in details]
        plans.append((row_idx, rows))

    expected_total = abs(expected_signed_sum)
    tolerance = max(2, detail_count)
    if abs(bank_total_sum - expected_total) > tolerance:
        raise RuntimeError(
            f"本次代墊銀行總額 {bank_total_sum:,} 與本次執行科目總額 {expected_total:,} 不符，"
            f"差額 {bank_total_sum - expected_total:,}，未寫入"
        )

    service = get_sheets_service()
    sheet_id = _sheet_id_for_title(service, spreadsheet_id, title)
    inserted_rows = 0
    for row_idx, rows in sorted(plans, key=lambda item: item[0], reverse=True):
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A{row_idx}:R{row_idx}",
            valueInputOption="USER_ENTERED",
            body={"values": [rows[0]]},
        ).execute()
        extra_rows = rows[1:]
        if not extra_rows:
            continue
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_idx,
                        "endIndex": row_idx + len(extra_rows),
                    },
                    "inheritFromBefore": True,
                }
            }]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A{row_idx + 1}:R{row_idx + len(extra_rows)}",
            valueInputOption="USER_ENTERED",
            body={"values": extra_rows},
        ).execute()
        inserted_rows += len(extra_rows)
    return {"updated_rows": len(plans), "inserted_rows": inserted_rows}
