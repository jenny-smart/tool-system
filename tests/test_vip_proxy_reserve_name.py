"""代客保留訂單：搬運進「XX儲值金預收」頁籤後，M 欄改標客人實際姓名
（BL 欄既有公式會自動帶入姓名＋地址），BK 欄標註「代客保留」。
"""
from services.google_sheets import col_to_num
from services.vip_workflow import VipStoredValueWorkflow


def _wf():
    return VipStoredValueWorkflow.__new__(VipStoredValueWorkflow)


def test_extract_proxy_reserve_name_fullwidth_colon():
    assert _wf()._extract_proxy_reserve_name("代客保留：王小明") == "王小明"


def test_extract_proxy_reserve_name_halfwidth_colon():
    assert _wf()._extract_proxy_reserve_name("代客保留:王小明") == "王小明"


def test_extract_proxy_reserve_name_no_match():
    wf = _wf()
    assert wf._extract_proxy_reserve_name("一般備註") == ""
    assert wf._extract_proxy_reserve_name("") == ""
    assert wf._extract_proxy_reserve_name(None) == ""


def test_extract_proxy_reserve_name_cancelled_not_matched():
    # 取消代客保留後備註會變成「取消」＋原備註，不能再誤判成代客保留
    assert _wf()._extract_proxy_reserve_name("取消代客保留：王小明") == ""


class _Sheets:
    def __init__(self):
        self.calls = []

    def write_values(self, ws, row, col, values):
        self.calls.append((row, col, values))


def _blank_row():
    return [""] * col_to_num("BJ")


def test_apply_proxy_reserve_names_marks_m_and_bk():
    wf = _wf()
    wf.sheets = _Sheets()

    row1 = _blank_row()
    row1[col_to_num("M") - 1] = "瑋萱"
    row1[col_to_num("BA") - 1] = "代客保留：王小明"

    row2 = _blank_row()
    row2[col_to_num("M") - 1] = "淑慧"
    row2[col_to_num("BB") - 1] = "代客保留：李小華"

    row3 = _blank_row()
    row3[col_to_num("M") - 1] = "一般客戶"
    row3[col_to_num("BA") - 1] = "取消代客保留：張三"

    marked = wf._apply_proxy_reserve_names(object(), [row1, row2, row3])

    assert marked == 2
    assert (2, col_to_num("M"), [["王小明"]]) in wf.sheets.calls
    assert (2, col_to_num("BK"), [["代客保留"]]) in wf.sheets.calls
    assert (3, col_to_num("M"), [["李小華"]]) in wf.sheets.calls
    assert (3, col_to_num("BK"), [["代客保留"]]) in wf.sheets.calls
    assert not any(call[0] == 4 for call in wf.sheets.calls)
