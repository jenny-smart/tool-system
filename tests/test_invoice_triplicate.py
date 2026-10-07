from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from tools.invoice_center.triplicate import candidates, complete, parse_requirements, prepare
from tools.invoice_center import triplicate_runner as runner
from tools.lemon_backend.models import BackendOrder


@pytest.fixture
def source():
    return {"source_row": 303, "order_no": "LC00215020", "customer": "測試客戶",
            "note": "2026/09/30 LC00215020 異動發票--改三聯 / 地址要刪除\n統編資料 93370180 川岩國際有限公司",
            "old_invoice": "DM51791909", "old_type": "二聯"}


@pytest.fixture
def backend():
    backend = MagicMock()
    backend.get_order.return_value = BackendOrder(
        order_no="LC00215020", invoice_no="DM51791909", invoice_type="二聯式",
        paid_status="已付款", amount="4800", address="原地址", email="test@example.com",
        service_date="2026-09-07", extra={"paid_at": "2026-08-31 16:20:57"})
    return backend


def row_for(source):
    row = [""] * 27
    for i, value in {1: "待處理發票", 2: "異動發票", 6: source["order_no"], 7: source["customer"],
                     10: source["note"], 23: source["old_invoice"], 24: "二聯"}.items():
        row[i] = value
    return row


def test_candidate_filters_completed_and_other_work(source):
    row = row_for(source)
    assert len(candidates([row])) == 1
    for column, value in [(1, "待收款"), (2, "車馬費發票"), (10, "一般開立"), (14, "AA12345678")]:
        changed = row.copy()
        changed[column] = value
        assert candidates([changed]) == []


def test_payload_preserves_total_clears_carrier_and_address(source, backend):
    plan = prepare("taipei", source, backend)
    p = plan["payload"]
    assert plan["paid_date"] == "2026-08-31"
    assert p["buyer_identifier"] == "93370180"
    assert p["buyer_name"] == "川岩國際有限公司"
    assert p["orderdate"] == ""
    assert p["hastax"] == "2"
    assert p["buyer_address"] == ""
    assert p["buyer_emailaddress"] == "test@example.com"
    assert p["carriertype"] == p["carrierid1"] == p["carrierid2"] == ""
    assert (p["saleamount"], p["taxamount"], p["totalamount"]) == ("4571", "229", "4800")
    assert p["orderid"] == "LC00215020-RDM51791909"
    source["note"] = "改三聯\n93370180 川岩國際有限公司"
    assert prepare("taipei", source, backend)["payload"]["buyer_address"] == "原地址"


@pytest.mark.parametrize("field,value", [("invoice_no", "AA12345678"),
                                        ("paid_status", "待付款"), ("extra", {}), ("amount", "0")])
def test_backend_mismatch_stops(source, backend, field, value):
    setattr(backend.get_order.return_value, field, value)
    with pytest.raises(ValueError):
        prepare("taipei", source, backend)


def test_ambiguous_company_is_rejected():
    with pytest.raises(ValueError):
        parse_requirements("改三聯\n93370180 川岩國際有限公司\n12345678 第二家公司")


def test_result_keeps_original_and_payment_status(source, backend):
    ws = MagicMock()
    ws.get.return_value = [row_for(source)]
    complete(ws, prepare("taipei", source, backend), "AA12345678")
    updates = ws.batch_update.call_args.args[0]
    assert {u["range"] for u in updates} == {"O303", "AA303", "B303", "K303"}
    with pytest.raises(ValueError):
        complete(ws, prepare("taipei", source, backend), "DM51791909")
    changed = row_for(source)
    changed[14] = "AA87654321"
    ws.get.return_value = [changed]
    with pytest.raises(ValueError):
        complete(ws, prepare("taipei", source, backend), "AA12345678")


def setup_process(monkeypatch, source, backend):
    from tools.invoice_center import cetustek_invoice_paste as paste
    plan = prepare("taipei", source, backend)
    ws = MagicMock()
    ws.get.return_value = [row_for(source)]
    state = {}
    events = []
    def save(change):
        state.update(change)
        events.append(state["stage"])
    monkeypatch.setattr(paste, "_open_invoice_create", lambda p: events.append("open"))
    monkeypatch.setattr(paste, "_paste_one", lambda *a: events.append("fill"))
    monkeypatch.setattr(paste, "_wait_for_manual_save", lambda *a: "AA12345678")
    return plan, ws, state, events, save


def test_cancel_failure_never_opens_or_issues(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    def fail(page, plan, before):
        before()
        raise RuntimeError("未知作廢結果")
    monkeypatch.setattr(runner, "cancel_original", fail)
    with pytest.raises(RuntimeError):
        runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == ["cancel_submitting"]
    with pytest.raises(RuntimeError, match="禁止自動重試"):
        runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == ["cancel_submitting"]


def test_success_checkpoint_order_and_idempotent_retry(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    monkeypatch.setattr(runner, "cancel_original", lambda p, plan, before: before())
    runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == ["cancel_submitting", "cancelled", "open", "awaiting_save", "fill", "issued", "completed"]
    events.clear()
    runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == ["completed"]


def test_awaiting_save_does_not_reissue(monkeypatch, source, backend):
    from tools.invoice_center import cetustek_invoice_paste as paste
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    state.update(plan=deepcopy(plan), stage="awaiting_save")
    monkeypatch.setattr(paste, "_extract_invoice_no_for_order", lambda *a: "")
    with pytest.raises(RuntimeError, match="不會重開"):
        runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == []
    monkeypatch.setattr(paste, "_extract_invoice_no_for_order", lambda *a: "AA12345678")
    runner.process(MagicMock(), ws, plan, state, save, backend, "taipei")
    assert events == ["issued", "completed"]


@pytest.mark.parametrize("retry_args", [
    {},
    {"resume_allowance_no": "FP20261007163523"},
    {"retry_unissued": True},
])
def test_confirmed_unsaved_retry_keeps_allowance_and_refills(
    monkeypatch, source, backend, retry_args,
):
    from tools.invoice_center import cetustek_invoice_paste as paste
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare("taipei", source, backend, original_action="allowance")
    old_plan = deepcopy(plan)
    old_plan["payload"]["orderdate"] = "115/10/20"
    state.update(plan=old_plan, stage="awaiting_save", allowance_no="FP20261007163523")
    ws.get.side_effect = lambda address: (
        [["FP20261007163523"]] if address.startswith("AB") else [row_for(source)]
    )
    monkeypatch.setattr(paste, "_extract_invoice_no_for_order", lambda *a: "")
    monkeypatch.setattr(runner, "allowance_original", lambda *a: pytest.fail("不得重複折讓"))

    runner.process(
        MagicMock(), ws, plan, state, save, backend, "taipei",
        **retry_args,
    )

    assert events == ["allowed", "open", "awaiting_save", "fill", "issued", "completed"]
    assert state["allowance_no"] == "FP20261007163523"
    assert state["plan"]["payload"]["orderdate"] == ""


@pytest.mark.parametrize("result_ready", [False, True])
@pytest.mark.parametrize("original_buyer_id", ["", "93370180"])
def test_cancel_browser_fixture(source, backend, original_buyer_id, result_ready):
    """Exercise selectors and confirmation on a local synthetic page, never EI."""
    from playwright.sync_api import sync_playwright
    plan = prepare("taipei", source, backend)
    html = '''<label><input type="radio">已開立發票</label>
    <table><tr><td>發票號碼</td><td><input><input></td></tr>
    <tr><td>發票日期</td><td><input id="date1" readonly value="115/09/24"><input id="date2" readonly></td></tr></table>
    <a href="#" onclick="document.getElementById('data').hidden=false;window.searched=true">搜&nbsp;尋 <i>arrow_forward</i> ❯</a>
    <div id="result"><table id="data"><tr><td>2026/08/31</td><td>DM51791909</td><td>2026/08/31</td>
    <td>LC00215020</td><td></td><td>測試客戶</td><td>4,800</td><td>0</td><td>4,800</td>
    <td id="state">有效</td><td><a href="javascript:void(0)" onclick="invoicecance('168315957');"><img title="作廢" src="images/Abort.png"></a></td></tr></table></div>
    <script>function invoicecance(id) {document.getElementById('modal').hidden=false;}</script>
    <div id="modal" hidden><table><tr><td>作廢原因</td><td><input></td></tr></table>
    <button onclick="document.getElementById('state').innerText='已作廢';alert('發票作廢成功')">確認作廢 <i>arrow_forward</i> ❯</button></div>'''
    if not result_ready:
        html = html.replace('<table id="data">', '<table id="data" hidden>')
    html = html.replace("<td>LC00215020</td><td></td>",
                        f"<td>LC00215020</td><td>{original_buyer_id}</td>")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html)
                   if route.request.is_navigation_request() else route.abort())
        calls = []
        if original_buyer_id:
            with pytest.raises(RuntimeError, match="二聯訂單"):
                runner.cancel_original(page, plan, lambda: calls.append("submit"))
            assert calls == []
            browser.close()
            return
        runner.cancel_original(page, plan, lambda: calls.append("submit"))
        assert calls == ["submit"]
        assert bool(page.evaluate("window.searched")) == (not result_ready)
        assert page.locator("#state").inner_text() == "已作廢"
        assert runner._field_row(page, "發票日期").locator("input").first.input_value() == "115/08/31"
        browser.close()


def test_cancel_date_reset_stops_before_search():
    field = MagicMock()
    field.input_value.return_value = "115/09/24"
    with pytest.raises(RuntimeError, match="禁止搜尋或作廢"):
        runner._set_cancel_date(field, "115/08/31")


def test_updated_company_setting_does_not_reclassify_original_invoice(source, backend):
    order = backend.get_order.return_value
    order.invoice_type = "三聯式"
    order.buyer_identifier = "93370180"
    order.buyer_name = "川岩國際有限公司"
    plan = prepare("taipei", source, backend)
    assert plan["source"]["old_type"] == "二聯"
    assert plan["source"]["old_invoice"] == "DM51791909"
    assert plan["payload"]["buyer_identifier"] == "93370180"
    assert plan["payload"]["totalamount"] == "4800"


def test_allowance_branch_records_number_then_issues(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    ws.get.side_effect = lambda address: [] if address.startswith('AB') else [row_for(source)]
    def allow(page, data, before):
        assert data['total'] == '4800'
        before()
        return 'AL1234567890'
    monkeypatch.setattr(runner, 'allowance_original', allow)
    monkeypatch.setattr(runner, 'cancel_original', lambda *a: pytest.fail('must not cancel'))
    runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')
    assert events == ['allowance_submitting', 'allowed', 'open', 'awaiting_save', 'fill', 'issued', 'completed']
    assert state['allowance_no'] == 'AL1234567890'
    assert ws.batch_update.call_args_list[0].args[0] == [{'range':'AB303','values':[['AL1234567890']]}]


def test_allowance_uncertain_result_blocks_retry_and_mode_change(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    ws.get.side_effect = lambda address: [] if address.startswith('AB') else [row_for(source)]
    def fail(page, data, before):
        before()
        raise RuntimeError('unknown')
    monkeypatch.setattr(runner, 'allowance_original', fail)
    with pytest.raises(RuntimeError):
        runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')
    with pytest.raises(RuntimeError, match='禁止自動重試'):
        runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')
    cancel_plan = prepare('taipei', source, backend)
    with pytest.raises(RuntimeError, match='不同的處理記錄'):
        runner.process(MagicMock(), ws, cancel_plan, state, save, backend, 'taipei')
    assert events == ['allowance_submitting']


def test_resume_after_allowance_never_repeats_allowance(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    state.update(plan=plan, stage='allowed', allowance_no='AL1234567890')
    ws.get.side_effect = lambda address: [['AL1234567890']] if address.startswith('AB') else [row_for(source)]
    monkeypatch.setattr(runner, 'allowance_original', lambda *a: pytest.fail('duplicate allowance'))
    runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')
    assert events == ['open','awaiting_save','fill','issued','completed']


def test_allowed_stage_accepts_corrected_invoice_payload(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    old_plan = deepcopy(plan)
    old_plan['payload']['hastax'] = '1'
    state.update(plan=old_plan, stage='allowed', allowance_no='AL1234567890')
    ws.get.side_effect = lambda address: [['AL1234567890']] if address.startswith('AB') else [row_for(source)]
    monkeypatch.setattr(runner, 'allowance_original', lambda *a: pytest.fail('不得重複折讓'))

    runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')

    assert state['plan']['payload']['hastax'] == '2'
    assert events == ['allowed', 'open', 'awaiting_save', 'fill', 'issued', 'completed']


def test_existing_allowance_is_detected_and_resumes_invoice(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    ws.get.side_effect = lambda address: [['AL1234567890']] if address.startswith('AB') else [row_for(source)]
    monkeypatch.setattr(runner, 'allowance_original', lambda *a: pytest.fail('不得重複折讓'))
    runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei')
    assert events == ['allowed', 'open', 'awaiting_save', 'fill', 'issued', 'completed']
    assert state['allowance_no'] == 'AL1234567890'


def test_full_allowance_checks_amount_year_and_save_boundary(monkeypatch):
    from tools.invoice_center import allowance_create as allowance
    page = MagicMock()
    controls = {}
    def locate(selector):
        return controls.setdefault(selector, MagicMock())
    page.locator.side_effect = locate
    option = MagicMock()
    option.get_attribute.return_value = '115'
    option.inner_text.return_value = '115年'
    locate('#qyear').locator.return_value.all.return_value = [option]
    locate('body').inner_text.return_value = '銷售額 應稅:$4,800'
    locate("#processresult a[onclick^='setInvoiceDetail']").count.return_value = 1
    locate('#msg').filter.return_value.inner_text.return_value = '折讓開立成功 AL1234567890'
    reason = MagicMock()
    monkeypatch.setattr(allowance, '_select_allowance_reason', reason)
    events = []
    locate('#save2').click.side_effect = lambda: events.append('save')
    result = allowance._create_one(page, 'DM51791909', '4800', invoice_year=2026,
                                  require_full=True, before_save=lambda: events.append('checkpoint'))
    assert result == 'AL1234567890'
    assert events == ['checkpoint', 'save']
    locate('#qyear').select_option.assert_called_once_with(value='115')
    reason.assert_called_once_with(page, full_refund=True)
    locate('#unitprice').fill.assert_not_called()
    events.clear()
    with pytest.raises(RuntimeError, match='全額折讓金額不符'):
        allowance._create_one(page, 'DM51791909', '4700', invoice_year=2026,
                              require_full=True, before_save=lambda: events.append('checkpoint'))
    assert events == []
    allowance._create_one(page, 'DM51791909', '4700')
    locate('#unitprice').fill.assert_called_once_with('4700')
    reason.assert_called_with(page, full_refund=False)


def test_resume_supplied_allowance_number_skips_creation(monkeypatch, source, backend):
    plan, ws, state, events, save = setup_process(monkeypatch, source, backend)
    plan = prepare('taipei', source, backend, original_action='allowance')
    state.update(plan=plan, stage='allowance_submitting')
    ws.get.side_effect = lambda address: [] if address.startswith('AB') else [row_for(source)]
    monkeypatch.setattr(runner, 'allowance_original', lambda *a: pytest.fail('must not reissue allowance'))
    runner.process(MagicMock(), ws, plan, state, save, backend, 'taipei', resume_allowance_no='AL1234567890')
    assert events == ['allowed','open','awaiting_save','fill','issued','completed']
    assert ws.batch_update.call_args_list[0].args[0] == [{'range':'AB303','values':[['AL1234567890']]}]


def test_successful_allowance_does_not_click_dismissed_confirmation():
    from tools.invoice_center import allowance_create as allowance
    page = MagicMock()
    notice = page.locator.return_value.filter.return_value
    notice.is_visible.return_value = True
    notice.inner_text.return_value = '折讓開立成功 AL1234567890'
    assert allowance._finish_allowance_save(page) == 'AL1234567890'
    page.locator.return_value.click.assert_not_called()


def test_disappearing_allowance_confirmation_recovers_success():
    from tools.invoice_center import allowance_create as allowance
    from playwright.sync_api import TimeoutError
    page = MagicMock()
    notice = page.locator.return_value.filter.return_value
    notice.is_visible.side_effect = [False, True]
    notice.inner_text.return_value = '折讓開立成功 AL1234567890'
    page.locator.return_value.click.side_effect = TimeoutError('not visible')
    assert allowance._finish_allowance_save(page) == 'AL1234567890'
    page.locator.return_value.click.assert_called_once()


def test_invoice_result_rejects_order_and_repairs_known_bad_value(source, backend):
    plan = prepare('taipei', source, backend)
    ws = MagicMock()
    row = row_for(source)
    row[14] = source['order_no']
    ws.get.return_value = [row]
    with pytest.raises(ValueError, match='訂單號'):
        complete(ws, plan, source['order_no'])
    ws.batch_update.assert_not_called()
    complete(ws, plan, 'AA12345678')
    updates = ws.batch_update.call_args.args[0]
    assert {'range':'O303','values':[['AA12345678']]} in updates
    assert {'range':'B303','values':[['已處理發票']]} in updates


def test_reissue_reference_cannot_be_extracted_as_new_invoice():
    from tools.invoice_center.cetustek_invoice_paste import _invoice_candidates
    assert _invoice_candidates('LC00215020 DM51791909', 'LC00215020-RDM51791909') == []
    assert _invoice_candidates('LC00215020 DM51791909 AA12345678', 'LC00215020-RDM51791909') == ['AA12345678']


def test_completion_appends_invoice_audit_note_only_once(source, backend):
    plan = prepare('taipei', source, backend)
    row = row_for(source)
    ws = MagicMock()
    ws.get.return_value = [row]
    complete(ws, plan, 'AA12345678')
    note = next(x['values'][0][0] for x in ws.batch_update.call_args.args[0] if x['range']=='K303')
    assert note.startswith(source['note'])
    assert note.endswith('原發票號碼：DM51791909\n新發票號碼：AA12345678')
    row[10], row[14], row[1] = note, 'AA12345678', '已處理發票'
    complete(ws, plan, 'AA12345678')
    assert not any(x['range']=='K303' for x in ws.batch_update.call_args.args[0])
