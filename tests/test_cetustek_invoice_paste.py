from __future__ import annotations

import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


queue_module = types.ModuleType("tools.invoice_center.invoice_payload_queue")
queue_module.list_pending_payloads = MagicMock()
queue_module.update_payload_status = MagicMock()
queue_module.write_invoice_result = MagicMock()
queue_module.enqueue_payload = MagicMock()
sys.modules.setdefault("tools.invoice_center.invoice_payload_queue", queue_module)

from tools.invoice_center import cetustek_invoice_paste as paste


def _locator(count: int = 1, *, visible: bool = True, value: str = "") -> MagicMock:
    locator = MagicMock()
    locator.count.return_value = count
    locator.first = locator
    locator.is_visible.return_value = visible
    locator.input_value.return_value = value
    return locator


def _invoice_page(order_id: str = "LC001") -> MagicMock:
    page = MagicMock()
    page.url = paste.INVOICE_CREATE_URL

    def locate(selector: str) -> MagicMock:
        if selector == "#orderid":
            return _locator(value=order_id)
        if selector in paste.INVOICE_FORM_SELECTORS:
            return _locator()
        return _locator(count=0, visible=False)

    page.locator.side_effect = locate
    return page


class CetustekInvoicePasteTest(unittest.TestCase):
    def test_tampermonkey_triplicate_selects_tax_inclusive(self) -> None:
        script = (
            Path(__file__).parents[1]
            / "tools/invoice_center/tampermonkey_ei_fill.js"
        ).read_text(encoding="utf-8")

        self.assertIn('forceRadio(isTriplicate ? "hastax2"', script)
        self.assertGreaterEqual(script.count("setTax(d);"), 3)
        self.assertNotIn('isTriplicate ? "hastax1"', script)
        self.assertIn(
            'if (valueText(d.orderdate)) setValue("orderdate", d.orderdate)',
            script,
        )

    def test_helper_button_alone_is_not_invoice_page(self) -> None:
        page = MagicMock()
        page.locator.return_value = _locator(count=0, visible=False)
        self.assertFalse(paste._is_invoice_create_page(page))

    def test_native_invoice_fields_identify_invoice_page(self) -> None:
        self.assertTrue(paste._is_invoice_create_page(_invoice_page()))

    def test_native_fields_on_wrong_url_are_not_invoice_page(self) -> None:
        page = _invoice_page()
        page.url = "https://www.ei.com.tw/InvoiceRent/index.jsp"

        self.assertFalse(paste._is_invoice_create_page(page))

    def test_open_invoice_create_uses_fixed_url(self) -> None:
        page = _invoice_page()
        page.url = "https://www.ei.com.tw/InvoiceRent/index.jsp"

        def goto(url: str, **_: object) -> None:
            page.url = url

        page.goto.side_effect = goto
        paste._open_invoice_create(page)

        page.goto.assert_called_once_with(
            paste.INVOICE_CREATE_URL,
            wait_until="domcontentloaded",
            timeout=15000,
        )

    def test_paste_fills_native_form_and_matches_order_id(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {"ok": True, "message": "已直接填入鯨躍原生表單"}
        payload = {"orderid": "LC001"}
        paste._paste_one(page, json.dumps(payload))

        self.assertEqual(page.evaluate.call_args.args[1], payload)
        self.assertIn("setValue", page.evaluate.call_args.args[0])
        self.assertIn(
            'const hastaxId = isTriplicate ? "hastax2"',
            page.evaluate.call_args.args[0],
        )
        self.assertGreaterEqual(page.evaluate.call_args.args[0].count("forceRadio(hastaxId)"), 3)
        self.assertIn(
            'if (text(d.orderdate)) setValue("orderdate", d.orderdate)',
            page.evaluate.call_args.args[0],
        )
        page.expect_event.assert_not_called()

    def test_company_invoice_rejects_non_tax_inclusive_form_result(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {
            "ok": True,
            "message": "已直接填入鯨躍原生表單",
            "hastax": "1",
        }

        with self.assertRaisesRegex(RuntimeError, "必須選擇單價含稅"):
            paste._paste_one(page, json.dumps({
                "orderid": "LC001",
                "buyer_identifier": "93370180",
            }))

    def test_paste_rejects_false_success_when_form_order_id_is_blank(self) -> None:
        page = _invoice_page("")
        page.evaluate.return_value = {"ok": True, "message": "已直接填入鯨躍原生表單"}

        with self.assertRaisesRegex(RuntimeError, "表單驗證失敗"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_paste_rejects_direct_fill_error(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {"ok": False, "message": "缺少鯨躍欄位：totalamount"}

        with self.assertRaisesRegex(RuntimeError, "填入結果異常"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_wait_for_save_accepts_new_invoice_number_without_click_marker(self) -> None:
        page = MagicMock()
        page.url = paste.INVOICE_CREATE_URL
        page.evaluate.return_value = False
        page.locator("body").inner_text.side_effect = [
            "",
            "開立成功 發票號碼 AB-12345678",
        ]
        page.locator("input").evaluate_all.return_value = []

        with patch.object(
            paste,
            "_extract_invoice_no_for_order",
            side_effect=["", "AB12345678"],
        ):
            self.assertEqual(
                paste._wait_for_manual_save(page, "LC001", timeout_ms=1000),
                "AB12345678",
            )
        page.bring_to_front.assert_called_once()

    def test_wait_for_save_accepts_invoice_already_saved_before_monitoring(self) -> None:
        # 貼入後 Google API 重試期間人工已按完儲存、否：監控開始時查詢頁已有這張訂單的發票
        page = MagicMock()
        with patch.object(paste, "_extract_invoice_no_for_order", return_value="FP68064063"):
            self.assertEqual(
                paste._wait_for_manual_save(page, "LC00217870", timeout_ms=1000, new_order=True),
                "FP68064063",
            )

    def test_wait_for_save_ignores_preexisting_invoice_unless_new_order(self) -> None:
        # 二聯改三聯等流程：頁面上原本就有的同訂單發票不能當成新開的
        page = MagicMock()
        page.evaluate.return_value = False
        page.wait_for_timeout.side_effect = lambda ms: None
        with patch.object(paste, "_extract_invoice_no_for_order", return_value="FP68064063"), \
                patch.object(paste.time, "monotonic", side_effect=[0.0, 0.0, 2.0]):
            with self.assertRaisesRegex(TimeoutError, "未偵測到儲存結果"):
                paste._wait_for_manual_save(page, "LC00217870", timeout_ms=1000)

    def test_existing_query_result_is_recovered_without_opening_new_invoice(self) -> None:
        page = MagicMock()
        item = {
            "_row": 7,
            "source_row": 12,
            "order_no": "LC002146661",
            "payload_json": json.dumps({"orderid": "LC002146661"}),
        }
        paste.list_pending_payloads.return_value = [item]

        with patch.object(
            paste,
            "_extract_invoice_no_for_order",
            return_value="DM51790871",
        ), patch.object(paste, "_open_invoice_create") as open_invoice:
            self.assertEqual(paste.process_pending_invoice_payloads(page, "台北"), 1)

        open_invoice.assert_not_called()
        paste.write_invoice_result.assert_called_once_with(
            "台北", 12, "LC002146661", "DM51790871"
        )
        paste.update_payload_status.assert_called_with(
            7,
            "completed",
            "已從查詢頁復原並回填 O/AA：DM51790871",
        )

    def test_awaiting_save_with_other_amount_invoice_stops(self) -> None:
        page = MagicMock()
        item = {
            "_row": 8, "source_row": 13, "order_no": "LC002145591",
            "payload_json": json.dumps({"orderid": "LC002145591"}), "status": "awaiting_save",
        }
        paste.list_pending_payloads.return_value = [item]
        # 照金額查不到，不限金額查到 → 停下
        with patch.object(paste, "_extract_invoice_no_for_order", return_value=""), patch.object(
            paste, "_source_row_values", return_value=("2026/11/21", "")
        ), patch.object(paste, "find_existing_invoice", side_effect=[None, "HR11111111"]), patch.object(
            paste, "_open_invoice_create"
        ) as open_invoice:
            with self.assertRaisesRegex(RuntimeError, "為避免重複開立"):
                paste.process_pending_invoice_payloads(page, "台北")
        open_invoice.assert_not_called()

    def test_awaiting_save_without_any_invoice_issues(self) -> None:
        # 2026-11-21 桃園 LC00220013：前一天貼入沒存檔，鯨躍查無 → 要開立
        item = {
            "_row": 8, "source_row": 13, "order_no": "LC00220013",
            "payload_json": json.dumps({"orderid": "LC00220013-1"}), "status": "awaiting_save",
        }
        paste.list_pending_payloads.return_value = [item]
        from tools.invoice_center import ei_issue
        with patch.object(paste, "_extract_invoice_no_for_order", return_value=""), patch.object(
            paste, "_source_row_values", return_value=("2026/11/20", "")
        ), patch.object(paste, "find_existing_invoice", return_value=None), patch.object(
            ei_issue, "issue_invoice", return_value=ei_issue.InvoiceResult("HR22222222", False)
        ) as issue, patch.object(paste, "write_invoice_result") as write_result, patch.object(
            paste, "update_payload_status"
        ):
            self.assertEqual(paste.process_pending_invoice_payloads(MagicMock(), "桃園"), 1)
        issue.assert_called_once()
        write_result.assert_called_once_with("桃園", 13, "LC00220013", "HR22222222")

    def test_processes_all_pending_in_sequence(self) -> None:
        page = MagicMock()
        pending = [
            {
                "_row": 7,
                "source_row": 12,
                "order_no": "LC001",
                "payload_json": json.dumps({"orderid": "LC001"}),
                "status": "pending",
            },
            {
                "_row": 8,
                "source_row": 13,
                "order_no": "LC002",
                "payload_json": json.dumps({"orderid": "LC002"}),
                "status": "pending",
            },
        ]

        with patch.object(paste, "list_pending_payloads", return_value=pending), patch.object(
            paste, "_extract_invoice_no_for_order", return_value=""
        ), patch.object(paste, "_source_row_values", return_value=("2026/10/19", "1400")), patch.object(
            paste, "find_existing_invoice", return_value=None
        ), patch.object(paste, "_open_invoice_create") as open_invoice, patch.object(
            paste, "_clear_dialog_handlers"
        ), patch.object(paste, "_paste_one") as paste_one, patch.object(
            paste,
            "_wait_for_manual_save",
            side_effect=["AB12345678", "AB12345679"],
        ), patch.object(paste, "write_invoice_result") as write_result, patch.object(
            paste, "update_payload_status"
        ):
            self.assertEqual(paste.process_pending_invoice_payloads(page, "台北"), 2)

        self.assertEqual(open_invoice.call_count, 2)
        self.assertEqual(paste_one.call_count, 2)
        self.assertEqual(write_result.call_count, 2)

    def test_extract_invoice_number_from_matching_order_row(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = [
            "DM51791827 LC002147991",
            "DM51790871 LC002146661-\n1",
        ]

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )

    def test_extract_invoice_number_from_enriched_value_fields(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661 DM51790871"],
            "page": "LC002146661 DM51790871",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )
        evaluated_script = page.evaluate.call_args.args[0]
        self.assertIn("input, a", evaluated_script)
        self.assertIn("data-orderid", evaluated_script)

    def test_extract_separate_dom_fields_only_when_invoice_is_unique(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661", "DM51790871"],
            "page": "LC002146661 DM51790871",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )

    def test_extract_separate_dom_fields_rejects_multiple_invoices(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661", "DM51790871", "DM51791827"],
            "page": "LC002146661 DM51790871 DM51791827",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "",
        )

    def test_maps_ei_column_four_order_to_column_two_invoice(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": [{
                "text": "115/08/25 DM51790873 115/08/25 LC00213133-1",
                "columns": [
                    "115/08/25",
                    "DM51790873",
                    "115/08/25",
                    "LC00213133-1",
                ],
            }],
            "page": "115/08/25 DM51790873 115/08/25 LC00213133-1",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "DM51790873",
        )

    def test_does_not_take_invoice_from_row_with_other_column_four_order(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": [{
                "text": "115/08/25 DM51790872 115/08/25 LC002145591-1",
                "columns": [
                    "115/08/25",
                    "DM51790872",
                    "115/08/25",
                    "LC002145591-1",
                ],
            }],
            "page": "",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "",
        )

    def test_order_shaped_like_invoice_is_excluded(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC00213133-1 DM51790873"],
            "page": "LC00213133-1 DM51790873",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "DM51790873",
        )

    def test_order_without_invoice_is_not_returned_as_invoice(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC00213133-1"],
            "page": "LC00213133-1",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "",
        )

    def test_extract_invoice_number_does_not_match_similar_order(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = ["DM51791827 LC0021466619-1"]

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "",
        )

    def test_extract_invoice_number_from_saved_page(self) -> None:
        page = MagicMock()
        page.locator("body").inner_text.return_value = "開立成功 發票號碼 AB-12345678"
        page.locator("input").evaluate_all.return_value = []

        self.assertEqual(paste._extract_invoice_no(page), "AB12345678")


    # 2026-11-21 台北 LC00221068-1 合併開立：G 欄多張訂單、查不到已開立 → 重複開立；原訂單也沒折讓
    MERGED_ITEM = {
        "_row": 9, "source_row": 40, "status": "pending",
        "order_no": "LC00221068\nLC00221069、LC00221070",
        "payload_json": json.dumps({"orderid": "LC00221068-1", "totalamount": "10800",
                                    "mainremark": "合併開立：LC00221068、LC00221069、LC00221070"}),
    }

    def test_lookup_uses_single_base_order_and_payload_total(self) -> None:
        self.assertEqual(paste.lookup_order_no(self.MERGED_ITEM), "LC00221068")
        self.assertEqual(paste.lookup_order_no({"order_no": "LC001 LC002", "payload_json": "{}"}), "LC001")
        self.assertEqual(paste.payload_amount(self.MERGED_ITEM, "3600"), "10800")
        import base64
        detail = base64.b64encode("CLEAN|居家清潔|式|1|3600|3600|x\nCLEAN|居家清潔|式|1|7200|7200|y".encode()).decode()
        three = {"payload_json": json.dumps({"detaildata": detail, "totalamount": "11340"})}
        self.assertEqual(paste.payload_amount(three), "10800|11340")
        rows = ["1 115/11/21 HR60354608 115/11/21 LC00221068-1 76941045 10,286 514 10,800 ATM"]
        self.assertEqual(paste.parse_invoice_rows(rows, "LC00221068", amount="10800|11340",
                                                  not_before="2026/11/21"), "HR60354608")
        self.assertIsNone(paste.parse_invoice_rows(rows, "LC00221068", amount="11340"))
        self.assertEqual(paste.payload_amount({"payload_json": "{}"}, "3600"), "3600")
        self.assertEqual(paste.merged_orders(self.MERGED_ITEM), ["LC00221068", "LC00221069", "LC00221070"])
        self.assertEqual(paste.merged_orders({"payload_json": json.dumps({"mainremark": "車馬費發票"})}), [])

    def test_merged_allowances_originals_before_checking_invoice(self) -> None:
        calls: list[str] = []
        with patch.object(paste, "list_pending_payloads", return_value=[dict(self.MERGED_ITEM)]), patch.object(
            paste, "_extract_invoice_no_for_order", return_value=""
        ), patch.object(paste, "_source_row_values", return_value=("2026/11/21", "3600")), patch.object(
            paste, "_source_row_invoice", return_value=""
        ), patch.object(
            paste, "find_existing_invoice", side_effect=lambda *a, **k: calls.append("find") or "HR60354608"
        ) as find, patch.object(
            paste, "allowance_merged_originals",
            side_effect=lambda *a, **k: calls.append("allowance") or ["LC00221069 HR11111111→HR22222222"],
        ) as allowances, patch.object(paste, "_write_allowance_numbers"), patch.object(
            paste, "_paste_one"
        ) as paste_one, patch.object(paste, "write_invoice_result") as write_result, patch.object(
            paste, "update_payload_status"
        ):
            self.assertEqual(paste.process_pending_invoice_payloads(MagicMock(), "台北"), 1)
        self.assertEqual(calls, ["allowance", "find"])  # 先折讓原訂單，再查合併發票
        find.assert_called_once_with(unittest.mock.ANY, "LC00221068", amount=unittest.mock.ANY, not_before="2026/11/21")
        self.assertEqual(allowances.call_args.kwargs["skip_invoices"], set())
        paste_one.assert_not_called()
        write_result.assert_called_once_with("台北", 40, self.MERGED_ITEM["order_no"], "HR60354608")

    def test_merged_skips_invoice_already_in_column_o(self) -> None:
        with patch.object(paste, "list_pending_payloads", return_value=[dict(self.MERGED_ITEM)]), patch.object(
            paste, "_extract_invoice_no_for_order", return_value=""
        ), patch.object(paste, "_source_row_values", return_value=("2026/11/21", "")), patch.object(
            paste, "_source_row_invoice", return_value="HR60354608"
        ), patch.object(paste, "find_existing_invoice", return_value="HR60354608"), patch.object(
            paste, "allowance_merged_originals", return_value=[]
        ) as allowances, patch.object(paste, "_write_allowance_numbers"), patch.object(
            paste, "write_invoice_result"
        ), patch.object(paste, "update_payload_status"):
            paste.process_pending_invoice_payloads(MagicMock(), "台北")
        self.assertEqual(allowances.call_args.kwargs["skip_invoices"], {"HR60354608"})

    def test_merged_allowance_skips_merged_invoice_and_orders_without_invoice(self) -> None:
        orders = {"LC1": types.SimpleNamespace(invoice_no="HR60354608"),
                  "LC2": types.SimpleNamespace(invoice_no=""),
                  "LC3": types.SimpleNamespace(invoice_no="HR11111111")}
        backend = MagicMock()
        backend.get_order.side_effect = orders.get
        from tools.invoice_center import ei_issue
        with patch("tools.lemon_backend.client.BackendClient", return_value=backend), patch.object(
            ei_issue, "issue_allowance", return_value=ei_issue.AllowanceResult("HR22222222", "2026/11/21", False)
        ) as issue:
            done = paste.allowance_merged_originals(MagicMock(), "台北", ["LC1", "LC2", "LC3"],
                                                    skip_invoices={"HR60354608"}, not_before="2026/11/21")
        issue.assert_called_once_with(unittest.mock.ANY, "HR11111111", "", require_full=True, not_before="2026/11/21")
        self.assertEqual(done, ["LC3 HR11111111→HR22222222"])


if __name__ == "__main__":
    unittest.main()


class ExistingInvoiceCheckTest(unittest.TestCase):
    ROWS = [
        "1 115/10/05 AB11111111 LC002175641 杜冠勳 12,000 開立",   # 原訂單發票（登記日前）
        "2 115/10/19 AB22222222 LC002175641 杜冠勳 1,400 作廢",    # 作廢
        "3 115/10/19 AB33333333 LC002175641-1 杜冠勳 1,400 開立",  # 這一筆
    ]

    def test_finds_this_charge_invoice_only(self) -> None:
        self.assertEqual(
            paste.parse_invoice_rows(self.ROWS, "LC002175641", amount="1400", not_before="2026/10/19"),
            "AB33333333",
        )

    def test_original_order_invoice_is_not_treated_as_issued(self) -> None:
        self.assertIsNone(
            paste.parse_invoice_rows(self.ROWS[:2], "LC002175641", amount="1400", not_before="2026/10/19")
        )
        self.assertIsNone(paste.parse_invoice_rows(self.ROWS, "LC00217564", amount="1400"))

    def test_existing_invoice_is_backfilled_without_pasting(self) -> None:
        pending = [{"_row": 7, "source_row": 320, "order_no": "LC002175641",
                    "payload_json": "{}", "status": "pending"}]
        with patch.object(paste, "list_pending_payloads", return_value=pending), patch.object(
            paste, "_extract_invoice_no_for_order", return_value=""
        ), patch.object(paste, "_source_row_values", return_value=("2026/10/19", "1400")), patch.object(
            paste, "find_existing_invoice", return_value="AB33333333"
        ), patch.object(paste, "_paste_one") as paste_one, patch.object(
            paste, "write_invoice_result"
        ) as write_result, patch.object(paste, "update_payload_status"):
            self.assertEqual(paste.process_pending_invoice_payloads(MagicMock(), "台北"), 1)
        paste_one.assert_not_called()
        write_result.assert_called_once_with("台北", 320, "LC002175641", "AB33333333")
