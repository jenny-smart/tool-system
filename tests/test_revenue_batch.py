from tools.finance_management import revenue_batch as batch


class FakeModule:
    __name__ = "fake"
    def __init__(self): self.calls = []
    def _call(self, name, *args): self.calls.append((name, *args))
    def convert_payment_file(self, *args): self._call("convert", *args)
    def move_refund_and_prepaid(self, *args): self._call("refund", *args)
    def move_invoice_and_bluenew(self, *args): self._call("invoice", *args)
    def move_atm_from_allowance(self, *args): self._call("atm", *args)


def test_period_and_multi_area_step_order():
    module = FakeModule()
    settings = {a: {"root_folder_id": a + "-root", "allowance_id": a + "-atm"} for a in ("台北", "台中")}
    failures = batch.run("202610-2", ["台北", "台中"], ["convert", "refund", "invoice", "atm"],
                         log=lambda _text: None, module=module, settings=settings,
                         audit_writer=lambda *_args: None)
    assert failures == []
    assert [(c[0], c[1]) for c in module.calls] == [
        ("convert", "台北-root"), ("refund", "台北-root"), ("invoice", "台北-root"), ("atm", "台北-atm"),
        ("convert", "台中-root"), ("refund", "台中-root"), ("invoice", "台中-root"), ("atm", "台中-atm"),
    ]
    assert all("202610-2" in c for c in module.calls)


def test_period_without_half_defaults_to_second_half():
    assert batch.normalize_period("202610") == ("202610-2", "202610")


def test_scan_uses_latest_record_and_legacy_salary_records():
    legacy = [
        ["作業名稱", "202610-2", ""], ["", "ID/筆數", "完成時間"],
        *[[task, 0, "2026/11/01 10:00"] for task in batch.LEGACY_TASKS["convert"]],
    ]
    audit = [
        ["2026/11/01 11:00", "台北", "202610-2", "refund", "完成", ""],
        ["2026/11/01 12:00", "台北", "202610-2", "refund", "失敗", "缺檔"],
        ["2026/11/01 13:00", "台北", "202610-2", "invoice", "完成", ""],
    ]
    rows = batch._scan_steps("202610-2", ["台北"], service=object(), audit_values=audit,
                             legacy_values={"台北": legacy})
    by_step = {row["step"]: row for row in rows}
    assert by_step["convert"]["done"] is True
    assert by_step["refund"]["done"] is False and "缺檔" in by_step["refund"]["note"]
    assert by_step["invoice"]["done"] is True
    assert by_step["atm"]["done"] is False


def test_group_scan_splits_cash_flow_and_revenue():
    audit = [["t", "台北", "202610-2", step, "完成", ""]
             for step in batch.expand_groups(["cash_flow"])]
    rows = batch.scan("202610-2", ["台北"], service=object(), audit_values=audit,
                      legacy_values={"台北": []})
    assert [(r["step"], r["done"]) for r in rows] == [("cash_flow", True), ("revenue", False)]
