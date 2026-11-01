"""上下半月訂單：未開發票、加收／退款資料待補、上底色（2026-11 Jenny）。"""
import pandas as pd

from tools.scheduled_monthly.uninvoiced_check import (MISSING_FILL, STATUS_FILL, find_uninvoiced,
                                                      highlight_workbook, summary_lines)

CHARGE_OK = {"加收狀態": "已加收", "加收日期": "2026-10-30", "加收金流": "ATM", "加收發票日期": "2026-10-31",
             "加收金額": 600, "加收發票號碼": "FP8"}
REFUND_OK = {"待退狀態": "已全額退款", "退款日期": "2026-10-29", "退款金流": "ATM", "退款金額": 1200,
             "折讓單號": "FP2026"}


def _rows():
    base = {"付款狀態": "已付款", "付款方式(含vip時數)": "信用卡", "發票號碼": "FP1", "服務金額": 3600}
    return [
        {**base, "訂單編號": "LC1"},
        {**base, "訂單編號": "LC2", "付款方式(含vip時數)": "儲值金", "發票號碼": None, "服務金額": 0},
        {**base, "訂單編號": "LC3", "發票號碼": None, "服務日期": "2026/10/30", "客戶姓名": "乙",
         "付款方式(含vip時數)": "ATM"},
        {**base, "訂單編號": "LC4", "發票號碼": "不開發票"},
        {**base, "訂單編號": "LC5", **CHARGE_OK, "加收發票號碼": None, "加收發票日期": None},
        {**base, "訂單編號": "LC5", **CHARGE_OK, "加收發票號碼": None, "加收發票日期": None},  # 重複列只列一次
        {**base, "訂單編號": "LC6", **CHARGE_OK},
        {**base, "訂單編號": "LC7", "待退狀態": "已部分退款"},
        {**base, "訂單編號": "LC8", **REFUND_OK},
        {**base, "訂單編號": "LC9", **REFUND_OK, "待退狀態": "已部分退款", "退款金額": 0, "折讓單號": None},
        {**base, "訂單編號": "LC10", "付款狀態": "待付款", "發票號碼": None},
    ]


def test_find_issues():
    items = find_uninvoiced(pd.DataFrame(_rows()))
    assert [(i["訂單編號"], i["類型"], i["缺"]) for i in items] == [
        ("LC3", "已付款未開發票", "發票號碼"),
        ("LC5", "已加收資料不完整", "加收發票日期、加收發票號碼"),
        ("LC7", "已部分退款資料不完整", "退款日期、退款金流、退款金額、折讓單號"),
    ]
    lines = summary_lines("台北", items)
    assert lines[0] == "[台北] 已付款未開發票 LC3（乙，服務日 2026/10/30，ATM 3,600）"
    assert lines[1].endswith("缺 加收發票日期、加收發票號碼）")


def test_highlight_workbook(tmp_path):
    from openpyxl import load_workbook

    path = tmp_path / "202610-2訂單-台北.xlsx"
    pd.DataFrame(_rows()).to_excel(path, index=False)
    assert highlight_workbook(str(path)) == 7  # LC3 + LC5×2 + LC6 + LC7 + LC8 + LC9
    sheet = load_workbook(path).worksheets[0]
    header = [c.value for c in sheet[1]]

    def color(row, name):
        return sheet.cell(row=row, column=header.index(name) + 1).fill.start_color.rgb[-6:]

    assert color(2, "訂單編號") == "000000"  # LC1 正常不上色
    assert color(4, "發票號碼") == MISSING_FILL and color(4, "訂單編號") == "000000"  # LC3 只標缺的格
    assert color(6, "訂單編號") == STATUS_FILL and color(6, "加收發票號碼") == MISSING_FILL  # LC5
    assert color(8, "訂單編號") == STATUS_FILL and color(8, "加收金額") == STATUS_FILL  # LC6 完整只有黃底
    assert color(10, "折讓單號") == STATUS_FILL  # LC8 退款完整
