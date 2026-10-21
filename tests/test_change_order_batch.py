"""清潔異動整合執行：盤點規則、補公式、排任務順序、回寫紀錄。"""
from datetime import date

from tools.finance_management import change_order_batch as batch

LETTERS = "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z AA AB AC AD AE".split()


def row(**cells):
    values = [""] * len(LETTERS)
    cells.setdefault("E", "2026/10/16")
    cells.setdefault("G", "LC001")
    for key, value in cells.items():
        values[LETTERS.index(key)] = value
    return values


def plan(region="台北", **cells):
    return batch.plan_row(region, 10, row(**cells))


def test_before_cutoff_and_synced_rows_are_skipped():
    assert plan(B="待退款", E="2026/10/15") is None
    assert plan(B="已退款", AE="更新系統（B欄：已退款）") is None
    assert plan(B="異動不處理") is None


def test_done_rows_need_sync_until_marker_matches():
    assert plan(B="已退款")["actions"] == ["sync"]
    assert plan(B="已收款", AE="更新系統（B欄：待收款）")["actions"] == ["sync"]
    assert plan(B="已處理發票", AE="建立異動")["actions"] == ["sync"]


def test_pending_refund_atm_and_credit():
    item = plan(B="待退款", X="AB12345678", Z="952", S="1000", T="985", R="ATM", P="700", Q="123")
    assert item["actions"] == ["allowance", "atm_refund"] and item["missing"] == []
    assert plan(B="待退款", X="AB1", Z="952", S="1000", R="信用卡")["actions"] == ["allowance", "credit_refund"]
    item = plan("桃園", B="待退款", X="AB1", Z="952", S="1000", R="ATM", P="700", Q="1")
    assert item["actions"] == ["allowance"] and "桃園 ATM 退款需人工" in item["manual"]
    item = plan(B="待退款", S="1000", R="現金", AB="X1", AC="2026/09/20")
    assert item["actions"] == ["sync"] and item["target_status"] == "已退款"
    assert plan(B="待退款", X="AB1", Z="952", S="1000", R="現金")["manual"] == ["付款方式「現金」退款需人工"]


def test_missing_data_is_not_ready_and_blocks_actions():
    item = plan(B="待退款", X="AB1", Z="952", S="1000", R="ATM")
    assert item["missing"] == ["缺退款銀行代碼／帳號（P／Q）"] and item["actions"] == []
    assert plan(B="待退款", S="1000", R="信用卡")["missing"] == ["缺原發票號碼（X）"]
    assert plan(B="待退款", X="AB1", R="信用卡")["missing"] == ["缺退款金額（S）", "缺未稅金額（Z）"]
    assert plan(B="待退款", X="AB1", Z="952", S="1000")["missing"] == ["缺付款方式（R）"]


def test_pending_charge_waits_for_payment_and_manual_invoice():
    assert plan(B="待收款")["missing"] == ["客人尚未付款（M 收款時間空白）"]
    assert plan(B="待收款", M="2026/09/20")["missing"] == ["缺收款金額（N）"]
    item = plan(B="待收款", M="2026/09/20", N="500")
    assert item["missing"] == [] and item["manual"] == [] and item["actions"] == ["invoice"]
    assert plan(B="待收款", M="2026/09/20", N="500", O="AB1", AA="2026/10/19")["actions"] == ["sync"]
    item = plan(B="待收款", M="2026/09/20", N="500", O="AB1")
    assert item["actions"] == [] and "AA" in item["manual"][0]


def test_stored_value_and_invoice_rows():
    assert plan(B="待扣儲值金", N="500")["actions"] == ["stored_value"]
    assert plan(B="待返儲值金", S="")["missing"] == ["缺儲值金金額（S）"]
    assert "二聯改三聯" in plan(B="待處理發票", C="異動發票")["manual"][0]
    assert "開立發票" in plan(B="待處理發票", C="車馬費發票")["manual"][0]


def test_formula_fixes_only_fill_blank_cells():
    values = [["header"], row(B="待退款", S="1000"), row(B="待退款", S="1000", T="985", Z="952"),
              row(B="待退款", S="1000", E="2026/10/15"), row(B="待收款", S="1000")]
    updates = batch.formula_fixes(values)
    assert updates == [
        {"range": "T2", "values": [['=IF(R2="ATM",IF(P2="012",S2,S2-15),0)']]},
        {"range": "Z2", "values": [["=S2/1.05"]]},
    ]


def test_queue_actions_order_and_sync_last():
    items = [
        {"region": "台中", "row": 5, "actions": ["allowance", "atm_refund"]},
        {"region": "台北", "row": 7, "actions": ["credit_refund"]},
        {"region": "台北", "row": 3, "actions": ["allowance"], "selected": ["allowance"]},
        {"region": "台北", "row": 9, "actions": ["atm_refund"], "selected": []},
    ]
    created = []

    def create(action, params, created_by=""):
        created.append((action, params.get("area"), params.get("selected_rows") or params.get("regions")))
        if action == "change_order.sync":
            assert params["rows"] == {"台北": [3, 7, 9], "台中": [5]}
        return {"task_id": str(len(created))}

    batch.queue_actions(items, True, create, log=lambda *_: None)
    assert created == [
        ("cetustek.allowance", "台中", [5]), ("cetustek.allowance", "台北", [3]),
        ("fubon.atm_refund", "台中", [5]),
        ("newebpay.refund_pending", "台北", [7]),
        ("change_order.sync", None, ["台北", "台中"]),
    ]


def test_record_text_lists_rows_by_region():
    title, body = batch.build_record_text(
        {"台北": {"done": [238, 389, 498], "failed": []}, "台中": {"done": [], "failed": [(12, "找不到訂單")]}},
        date(2026, 10, 20))
    assert title == "本日清潔異動已回寫：3 筆"
    assert "台北：3筆，列數：238，389，498" in body and "台中 失敗：第12列 找不到訂單" in body


def test_sync_region_only_touches_done_rows(monkeypatch):
    values = [["h"], row(B="已退款"), row(B="待退款"), row(B="已收款", AE="更新系統（B欄：已收款）"),
              row(B="已扣儲值金", E="2026/10/15")]

    class WS:
        def get_all_values(self):
            return values

        def update_acell(self, cell, value):
            b_updates.append((cell, value))

    synced, marked, b_updates = [], [], []

    class CO:
        @staticmethod
        def get_worksheet(region):
            return WS()

        @staticmethod
        def sync_one_to_purchase_edit(item, session=None, ui_logger=None):
            synced.append(item["sheet_row"])

        @staticmethod
        def mark_sheet_row_done(region, row_no, status, ui_logger=None):
            marked.append((row_no, status))

    monkeypatch.setattr(batch, "_change_order", lambda: CO)
    monkeypatch.setattr(batch, "_login_session", lambda region, ui_logger=None: "session")
    result = batch.sync_region("台北", log=lambda *_: None)
    assert result == {"done": [2], "failed": []} and synced == [2] and marked == [(2, "已退款")]


def test_sync_respects_selected_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(batch, "sync_region", lambda region, log, rows: calls.append((region, rows)) or {"done": [], "failed": []})
    monkeypatch.setattr(batch, "record_to_calendar", lambda *a, **k: None)
    batch.sync(["台北", "台中"], log=lambda *_: None, rows={"台北": [3]})
    assert calls == [("台北", {3}), ("台中", set())]


def test_already_processed_pending_rows_go_straight_to_sync():
    cases = [
        (dict(B="待退款", S="1000", AB="AL1", AC="2026/10/17"), "已退款"),
        (dict(B="待收款", M="2026/10/17", N="500", O="AB12345678", AA="2026/10/19 10:00:00"), "已收款"),
        (dict(B="待扣儲值金", N="500", M="2026/10/17"), "已扣儲值金"),
        (dict(B="待返儲值金", S="300", AC="2026/10/17"), "已返儲值金"),
        (dict(B="待處理發票", C="車馬費發票", O="AB1"), "已處理發票"),
    ]
    for cells, target in cases:
        item = plan(**cells)
        assert item["actions"] == ["sync"] and item["target_status"] == target and item["missing"] == []
    assert plan(B="待退款", S="1000", AB="AL1", R="信用卡")["actions"] == ["credit_refund"]


def test_sync_region_sets_done_status_for_processed_pending_rows(monkeypatch):
    values = [["h"], row(B="待收款", M="2026/10/17", N="500", O="AB1", AA="2026/10/19")]
    b_updates, synced, marked = [], [], []

    class WS:
        def get_all_values(self):
            return values

        def update_acell(self, cell, value):
            b_updates.append((cell, value))

    class CO:
        @staticmethod
        def get_worksheet(region):
            return WS()

        @staticmethod
        def sync_one_to_purchase_edit(item, session=None, ui_logger=None):
            synced.append(item["status"])

        @staticmethod
        def mark_sheet_row_done(region, row_no, status, ui_logger=None):
            marked.append((row_no, status))

    monkeypatch.setattr(batch, "_change_order", lambda: CO)
    monkeypatch.setattr(batch, "_login_session", lambda region, ui_logger=None: "session")
    assert batch.sync_region("台北", log=lambda *_: None) == {"done": [2], "failed": []}
    assert synced == ["已收款"] and b_updates == [("B2", "已收款")] and marked == [(2, "已收款")]


def test_refund_amount_shows_what_will_actually_be_refunded():
    # 信用卡：T 欄公式為 0，畫面要顯示 S（退款金額），不是 0
    assert plan(B="待退款", X="FP56436829", Z="1333.333", S="1400", T="0", R="信用卡")["amount"] == "1400"
    # ATM：顯示 T（扣手續費後實際匯出金額）
    assert plan(B="待退款", X="AB1", Z="952", S="1000", T="985", R="ATM", P="700", Q="1")["amount"] == "985"
    assert plan(B="待退款", X="AB1", Z="952", S="1000", R="ATM", P="700", Q="1")["amount"] == "1000"


def test_queue_actions_invoice_first_via_ui_queue():
    items = [
        {"region": "台北", "row": 4, "actions": ["allowance"]},
        {"region": "台北", "row": 320, "actions": ["invoice"]},
        {"region": "台中", "row": 8, "actions": ["invoice"]},
    ]
    order = []

    def create(action, params, created_by=""):
        order.append((action, params.get("area") or tuple(params.get("regions", []))))
        return {"task_id": str(len(order))}

    def queue_invoices(region, rows):
        order.append(("invoice", region, rows))
        return {"task_id": "inv"}

    batch.queue_actions(items, True, create, log=lambda *_: None, queue_invoices=queue_invoices)
    assert order == [
        ("invoice", "台北", [320]), ("invoice", "台中", [8]),
        ("cetustek.allowance", "台北"),
        ("change_order.sync", ("台北", "台中")),
    ]


def test_queue_actions_without_invoice_queue_skips_invoice():
    created, logs = [], []
    batch.queue_actions([{"region": "台北", "row": 320, "actions": ["invoice"]}], False,
                        lambda action, params, created_by="": created.append(action) or {}, log=logs.append)
    assert created == [] and "開立發票未排入" in logs[0]


def test_vip_refund_coupon_flow():
    # ① 尚未歸零：儲值金／購物金歸零＋開折讓單，不退款
    item = plan(B="VIP待退券", X="AB1", Z="952", S="1000", R="信用卡")
    assert item["actions"] == ["stored_value", "allowance"] and item["manual"] == []
    assert "儲值金歸零及折讓單完成後才退款" in item["progress"]
    # 已歸零但折讓單還沒開：只開折讓單，不退款
    item = plan(B="VIP待退券", M="2026/10/21", X="AB1", Z="952", S="1000", R="信用卡")
    assert item["actions"] == ["allowance"]
    # ② 已歸零（M）、折讓單完成：信用卡走藍新
    item = plan(B="VIP待退券", M="2026/10/21", AB="AL1", S="1000", R="信用卡")
    assert item["actions"] == ["credit_refund"] and item["manual"] == []
    # ③ 信用卡：各區都由 Agent 走藍新；ATM：台北／台中富邦，桃園／新竹／高雄由財務處理
    cells = dict(B="VIP待退券", M="2026/10/21", AB="AL1", S="1000", T="985", R="ATM", P="012", Q="1")
    for region in batch.REGIONS:
        assert plan(region, **{**cells, "R": "信用卡"})["actions"] == ["credit_refund"]
    for region in ("台北", "台中"):
        assert plan(region, **cells)["actions"] == ["atm_refund"]
    for region in ("桃園", "新竹", "高雄"):
        item = plan(region, **cells)
        assert item["actions"] == [] and item["manual"] == [] and item["missing"] == []
        assert "退款由財務處理（完成後填 AC 退款時間）" in item["progress"]
    # ④ 退款完成 → 回寫，B 欄改為 VIP已退券
    item = plan(B="VIP待退券", M="2026/10/21", AB="AL1", AC="2026/10/22", S="1000", R="ATM")
    assert item["actions"] == ["sync"] and item["target_status"] == "VIP已退券"
    assert plan(B="VIP已退券")["actions"] == ["sync"]
    assert plan(B="VIP已退券", AE="更新系統（B欄：VIP已退券）") is None


def test_formula_fixes_include_vip_refund_coupon():
    updates = batch.formula_fixes([["header"], row(B="VIP待退券", S="1000")])
    assert [u["range"] for u in updates] == ["T2", "Z2"]

