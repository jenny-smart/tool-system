# ============================================================
# 檔名：tools/orders_system/ui.py（原 orders-system/ordersapp.py）
# 說明：整併進 tool-system，包成 render_orders_system() 供
#       pages/訂單系統.py 呼叫。原始 changelog 保留在函式內部註解。
# ============================================================
def render_orders_system(forced_mode=None, shared_backend_email=None, shared_backend_password=None, shared_env=None):
    # ============================================================
    # 檔名：ordersapp.py
    # 版本：v8.25
    # 模組：服務訂單系統主畫面
    # 最後更新：2026-07-07
    #
    # Change Log
    # v8.25
    # - 配合 quick_order.py v8.28（嚴格依序查儲值金→VIP→專業清潔，並跳過付款
    #   方式是儲值金的訂單），「查詢明細」展開區塊補上「被跳過的儲值金折抵訂單」
    #   表格，並更新說明文字。
    # v8.24
    # - 配合 quick_order.py v8.26 改用伺服器端「購買項目/付款狀態」篩選查詢，
    #   同步更新「查詢明細」展開區塊的表格欄位（改成顯示依序查了哪些類別、
    #   各查到幾筆已付款訂單、有哪些訂單編號），取代舊版「自己分類全部訂單」
    #   的顯示格式。
    # v8.23
    # - 「儲值金購買」查無可用付款方式/發票設定時，新增「查詢明細」展開區塊，
    #   用表格顯示這支電話實際查到的每一張訂單卡片（訂單編號/分類結果/是否
    #   已付款），配合 quick_order.py v8.25 的 search_debug，讓查不到資料的
    #   原因可以直接從畫面上判斷，不用再靠反覆截圖來回排查。
    # v8.22
    # - 「儲值金購買」結果區塊補上「發送確認信」按鈕，跟其他成單流程一致：
    #   建單成功後不自動發信，由客服確認資料無誤後手動按下再發送
    #   （沿用既有 send_confirmation，成功後畫面切換為「已發送」狀態）。
    #   LINE 通知訊息這部分從 v8.21 起本來就會自動產生並顯示，本次沒有改動。
    # v8.21
    # - 修正 _missing_quick_order_names 檢查跳出的錯誤訊息：原本寫死「請用 v8.5
    #   覆蓋 GitHub 上的 quick_order.py」，是很久以前版本號還是 v8.5 時寫的字串，
    #   之後版本一路往上加卻沒跟著更新，導致畫面一直誤導使用者要覆蓋成「v8.5」。
    #   改成不寫死版本號的通用說明，並提醒可能是 Streamlit 快取沒重新載入，
    #   建議手動 Reboot app。
    # v8.20
    # - 新增「儲值金購買」功能選單，對應 quick_order.py 新增的
    #   create_stored_value_purchase_order：輸入手機號碼、選地區、選金額即可
    #   建單，付款方式/發票自動沿用會員歷史訂單設定，不用手動選；查無可用設定
    #   時會明確提示需人工確認，不會默默送出錯誤的付款/發票組合。
    # v8.19
    # - 批次建單的訂單一致性檢查改成「全部列都跑完後才統一做一次」，不再是每一列
    #   各自比對一次（原本掛在 run_process_web 裡，會讓同一支電話在多列批次裡被
    #   重複查詢很多次，配合 orders.py 新增的獨立函式 run_batch_consistency_check）。
    # - 一致性檢查改成看得到的獨立勾選框「全部執行完後做一次訂單一致性檢查」，
    #   預設開啟，並在執行結果下方另外顯示獨立的「步驟5：訂單一致性檢查」區塊，
    #   不管有沒有異常都會顯示執行狀態，不會讓人以為系統根本沒做這件事。
    # - 「批次建單」的功能說明加入雙向比對的說明文字。
    # v8.18
    # - 批次建單的訂單一致性檢查結果顯示，配合 orders.py 的雙向比對更新：
    #   1. 方向一比對項目加入地址，訊息文字同步更新為「電話/地址/日期/時段」。
    #   2. 方向二（系統反查 Sheet）查到的異常沒有對應到特定列（row_num 為
    #      None），畫面改顯示「（系統反查）」而不是「第 None 列」。
    # v8.17
    # - 修正「新客資料拆解」與「訂單轉換」的 LINE 訊息文字框，成立新訂單後畫面還
    #   停留在上一張訂單內容的問題：這幾個 st.text_area 原本帶了固定 key
    #   （nc_line_out / conv_line_{index} / conv_combined_line），Streamlit 的
    #   規則是「帶 key 的 widget 只有第一次渲染時吃 value 參數，之後即使傳入新的
    #   value，畫面仍以 session_state 裡的舊值為準」，導致訂單編號/金額/日期都已經
    #   換成新訂單了，LINE 訊息內文卻還是前一張訂單的。修法：拿掉這三處不需要保留
    #   使用者編輯狀態的固定 key，改成跟舊客快速建單、儲值金補價差的 LINE 訊息
    #   一樣不帶 key，每次都用最新的 value 重新渲染。
    # v8.16
    # - 修正 v8.15 造成的 AttributeError：清空舊結果時原本寫成
    #   `st.session_state.nc_result = None`，但下面讀取是
    #   `st.session_state.get("nc_result", {})` 再接 `.get("order_no")`——
    #   get() 的預設值只在「key 不存在」時生效，key 存在但值是 None 時直接拿到
    #   None，後面 `.get()` 就會炸出 AttributeError。修法：五個成單流程清空舊結果
    #   時一律改成清成 `{}` 而不是 `None`（空字典一樣是 falsy，所有 if 判斷維持
    #   正常，但不會再有 None.get() 的問題）。
    # v8.15
    # - 修正五個成單流程（舊客快速建單、新客資料拆解、訂單轉換、儲值金補價差兩段）
    #   按下執行按鈕時沒有先清空上一次殘留在 session_state 的舊結果，導致這次執行
    #   失敗（或還在拆解資料階段）時，畫面下方還顯示上一次成功的舊訂單資訊，
    #   跟這次的錯誤訊息重疊在一起造成混淆。現在改為：每個「執行」按鈕一按下，
    #   立刻清空自己那個結果區塊，再開始新的一次嘗試。
    # v8.14
    # - 批次建單（Google Sheet）補上「查無班表時自動補檸檬人排班」勾選框，預設不勾選，
    #   與舊客快速建單、新客資料拆解、訂單轉換三個流程行為一致（配合 orders.py
    #   process_one_group / run_process_web 新增的 allow_auto_lemon_shift 參數）。
    #   　※上次 v8.13 只更新了 quick_order.py，批次建單走的是 orders.py，
    #   　　這次才一併補上，五個成單功能現在才真的共用同一套邏輯。
    # - 批次建單執行完畢後，顯示訂單一致性檢查結果：若 Google Sheet 上寫回的訂單編號
    #   跟該列電話/日期/時段對不上（例如訂單編號重複寫入兩列、或該列其實沒有真的
    #   成單），會直接在畫面上列出異常，不用再自己肉眼比對（配合 orders.py 新增的
    #   verify_batch_order_consistency）。
    # v8.13
    # - 新增訂單編號重複提醒視窗（show_duplicate_order_warning）：建單成功後若偵測到
    #   訂單編號重複（配合 quick_order v8.13 的 order_no_duplicated），會用
    #   st.dialog 跳出提醒視窗（不支援 st.dialog 的 Streamlit 版本則退回醒目的
    #   st.error），涵蓋舊客快速建單、新客資料拆解、訂單轉換、儲值金補價差四個流程。
    # - 舊客快速建單、新客資料拆解、訂單轉換三個流程都新增「查無班表時自動補檸檬人
    #   排班」勾選框，預設不勾選；未勾選時查無班表不會自動嘗試勾檸檬人（配合
    #   quick_order v8.13 的 allow_auto_lemon_shift 參數）。
    # v8.12
    # - 「新客資料拆解」貼上文字後即時顯示拆解預覽（姓名/電話/地址）；若判斷不出付款
    #   方式，直接顯示手動選擇的下拉選單，未選擇前擋下「建立新客訂單」按鈕，
    #   不再默默預設成信用卡（配合 quick_order v8.12 的 need_ask_payway）。
    # v8.10
    # - 「新客資料拆解」流程的 LINE 訊息旁補上「複製 N-J Memo」區塊，
    #   與「舊客快速建單」版面一致（原本只有舊單有，新單沒有）。
    # v8.9
    # - 新客建單結果加上「地址比對警示」：若後台實際地址與送出地址不同（例如後台自動
    #   判斷區域時加了不正確的市/區前綴），會直接顯示警示文字並附上後台實際地址，
    #   方便立即發現、回報或至後台手動修正（配合 quick_order v8.9 的
    #   address_mismatch_warning）。經確認此類情況是後台端自身的地址正規化行為，
    #   並非本系統送出的地址資料有誤。
    # v8.8
    # - 修正「舊客快速建單」結果區塊（訂單編號/金額/車馬費/確認信 metrics + LINE 訊息）
    #   原本沒有限定分頁，導致切到「新客資料拆解」等其他分頁後，session_state 裡
    #   殘留的舊訂單結果還黏在畫面下方，跟當前分頁剛建立的訂單混在一起顯示
    #   （例如畫面同時出現兩筆不同訂單、不同日期、不同金額，造成混淆）。
    #   現在改為只在「舊客快速建單」分頁才顯示。
    # v8.7
    # - 新客建單結果（舊客快速建單>查無會員 / 新客資料拆解）加上金額比對警示：
    #   若後台實際金額與人時公式（600平日/700週末，不含車馬費）算出的金額不同，
    #   會直接顯示警示文字，方便立即發現金額被後台另行計價覆蓋的情況
    #   （配合 quick_order v8.7 的 price_mismatch_warning）。
    # v8.6
    # - 舊客快速建單：付款方式選單改為「信用卡/ATM」「信用卡」「ATM」「儲值金」四選一。
    #   選「信用卡/ATM」時沿用上次付款紀錄（僅限信用卡或ATM，查無則預設信用卡）；
    #   選「信用卡」或「ATM」則直接以該選項作為付款方式；「儲值金」維持獨立選項。
    #   實際送單一律解析為信用卡／ATM／儲值金三者之一，caption 同步顯示解析結果。
    # - 修正「新客資料拆解」流程從未組出 LINE 訊息的問題（配合 quick_order v8.6
    #   quick_create_new_customer_order 補齊回傳欄位，這裡改為直接呼叫 build_line_message）。
    # v8.5
    # - 舊客快速建單：付款方式選單改為永遠顯示（信用卡／ATM／儲值金），
    #   預設值帶上次付款紀錄，但客服可隨時切換，不再被歷史紀錄鎖死。
    # - 建單介面 caption 加上送單網址顯示，方便確認 /booking/single 或
    #   /booking/stored_value_routine 是否選對。
    # v8.4
    # - 訂單轉換改為一對多：可設定多筆新訂單（日期/時段/人數各自選）。
    #   每筆新單各建一張折價券（面額=該筆含稅金額）。
    #   原單A配班：一般專員優先，不足補檸檬人。
    #   新單配班：同上。備註：A+B1+B2+B3 合併服務。
    # - _REQUIRED_QUICK_ORDER_NAMES 加入 convert_order_multi。
    # v8.3 - 排班換人必須勾選足夠不同的檸檬人
    # v8.2 - 檸檬人依序補勾多位不同檸檬人
    # v8.1 - 第二段補價差單沿用第一段原儲值金餘額
    # v8.0 - 檸檬人清單解析新增 shift 頁掃描備援
    # v7.9 - 配合 quick_order v7.9
    # v7.8 - 儲值金清零說明與計算修正
    # v7.7 - 儲值金補價差拆兩段按鈕
    # ============================================================
    # -*- coding: utf-8 -*-
    __version__ = "8.25"

    import html
    import requests
    import json
    import streamlit as st
    import streamlit.components.v1 as components
    from datetime import date, timedelta

    from .orders import run_process_web, get_region_by_address, run_batch_consistency_check
    from .accounts import ACCOUNTS
    try:
        from . import quick_order as qo
    except Exception as e:
        st.error(f"quick_order.py 載入失敗：{type(e).__name__}: {e}")
        st.stop()

    if not hasattr(qo, "stored_value_makeup_convert") and hasattr(qo, "stored_value_makeup"):
        qo.stored_value_makeup_convert = qo.stored_value_makeup

    _REQUIRED_QUICK_ORDER_NAMES = [
        "quick_lookup_member",
        "quick_create_order",
        "quick_check_available_slots",
        "send_confirmation",
        "build_line_message",
        "build_line_message_from_order_no",
        "build_combined_line_message_from_order_nos",
        "get_last_paid_summary",
        "get_last_paid_per_address",
        "get_unserved_paid_orders",
        "get_last_purchase_fetch_debug",
        "build_equivalent_plans",
        "search_available_service_dates",
        "parse_new_customer_order_text",
        "create_coupon",
        "convert_order",
        "convert_order_multi",
        "get_stored_value",
        "calc_stored_value_plan",
        "stored_value_makeup_convert",
        "stored_value_makeup_create_stored_order",
        "stored_value_makeup_create_paid_order",
        "create_stored_value_purchase_order",
        "COUPON_COMPANY_ID_MAP",
        "COUPON_SERVICE_ITEM_MAP",
        "COUPON_TYPE_MAP",
    ]

    _missing_quick_order_names = [name for name in _REQUIRED_QUICK_ORDER_NAMES if not hasattr(qo, name)]
    if _missing_quick_order_names:
        st.error(
            f"quick_order.py 目前版本不完整（缺少必要函式），請確認 GitHub 上的 quick_order.py 是最新版本"
            f"（本檔 ordersapp.py 為 v{__version__}），並確認 Streamlit 有重新載入最新內容"
            f"（若剛覆蓋過檔案，畫面右下角選單可手動 Reboot app）。"
            + "\n缺少：" + "、".join(_missing_quick_order_names)
        )
        st.stop()

    for _name in _REQUIRED_QUICK_ORDER_NAMES:
        globals()[_name] = getattr(qo, _name)

    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');

    :root {
        --lemon: #F5C518;
        --lemon-dark: #D4A017;
        --lemon-soft: #FFFBEA;
        --lemon-mid: #FFF3C4;
        --charcoal: #1C1C1E;
        --ink: #3A3A3C;
        --muted: #8E8E93;
        --border: #E5E5EA;
        --surface: #FFFFFF;
        --success: #34C759;
        --danger: #FF3B30;
        --radius: 14px;
    }

    html, body, [class*="css"] { font-family: 'Noto Sans TC', sans-serif; color: var(--charcoal); }
    #MainMenu, footer, header { visibility: hidden; }
    [data-testid="stAppViewContainer"] { background: #FAFAFA; }
    .block-container { padding-top: 2rem !important; padding-bottom: 2rem !important; max-width: 1180px !important; }

    .hero { background: linear-gradient(135deg, #FFFDF0 0%, #FFFBEA 100%); border: 1.5px solid var(--lemon-mid); border-radius: var(--radius); padding: 2rem 2.5rem 1.6rem; margin-bottom: 2rem; display: flex; align-items: center; gap: 1.2rem; box-shadow: 0 2px 12px rgba(245,197,24,0.10); }
    .hero-emoji { font-size: 3rem; line-height: 1; }
    .hero-title { font-family: 'Space Grotesk', sans-serif; font-size: 1.9rem; font-weight: 700; color: var(--charcoal); letter-spacing: -0.5px; }
    .hero-sub { color: var(--ink); font-size: 0.92rem; margin-top: 0.3rem; opacity: 0.78; }

    .step-pill { display: inline-flex; align-items: center; gap: 0.5rem; background: var(--lemon-mid); border: 1.5px solid var(--lemon); border-radius: 30px; padding: 0.28rem 0.9rem; font-size: 0.78rem; font-weight: 700; color: var(--charcoal); margin-bottom: 0.9rem; letter-spacing: 0.02em; }
    .step-num { background: var(--lemon); border-radius: 50%; width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; font-size: 0.72rem; font-weight: 700; }
    .sec-label { font-size: 12px; font-weight: 700; color: var(--muted); letter-spacing: .04em; margin-bottom: 8px; }
    .hint-box { background: var(--lemon-soft); border-left: 4px solid var(--lemon); border-radius: 0 8px 8px 0; padding: 0.75rem 1rem; font-size: 0.9rem; color: var(--ink); margin-top: 0.6rem; }

    [data-testid="stTextInput"] label, [data-testid="stNumberInput"] label, [data-testid="stSelectbox"] label, [data-testid="stMultiSelect"] label, [data-testid="stDateInput"] label, [data-testid="stRadio"] label { font-size: 13px !important; color: var(--ink) !important; font-weight: 700 !important; }
    [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, [data-testid="stSelectbox"] > div > div, [data-testid="stMultiSelect"] > div > div, [data-testid="stDateInput"] input { border-radius: 10px !important; border: 1.5px solid var(--border) !important; background: white !important; font-size: 15px !important; }
    [data-testid="stTextInput"] input:focus { border-color: var(--lemon-dark) !important; box-shadow: 0 0 0 2px rgba(245,197,24,0.22) !important; }
    [data-testid="stButton"] > button { background: var(--lemon) !important; color: var(--charcoal) !important; border: none !important; border-radius: 10px !important; font-size: 15px !important; font-weight: 700 !important; padding: 0.55rem 1.2rem !important; box-shadow: 0 2px 10px rgba(245,197,24,0.28) !important; }
    [data-testid="stButton"] > button:hover { background: var(--lemon-dark) !important; transform: translateY(-1px) !important; }
    [data-testid="stButton"] > button:disabled { background: #D1D5DB !important; color: #777 !important; }
    [data-testid="stExpander"] { border: 1px solid #ececec !important; border-radius: 14px !important; background: white !important; overflow: hidden !important; box-shadow: 0 2px 12px rgba(0,0,0,0.04); }
    [data-testid="stExpander"] summary { font-size: 14px !important; font-weight: 700 !important; color: var(--charcoal) !important; padding: 12px 16px !important; }
    [data-testid="stCode"] { font-size: 13px !important; border-radius: 0 0 12px 12px !important; min-height: 420px !important; max-height: 560px !important; overflow-y: auto !important; background: #1C1C1E !important; margin: 0 !important; white-space: pre-wrap !important; }
    [data-testid="stMetric"] { background: white !important; border: 1px solid #ececec !important; border-radius: 14px !important; padding: 14px 16px !important; text-align: center !important; box-shadow: 0 2px 12px rgba(0,0,0,0.04); }
    [data-testid="stMetricLabel"] { font-size: 12px !important; color: var(--muted) !important; font-weight: 700 !important; }
    [data-testid="stMetricValue"] { font-family: 'Space Grotesk', sans-serif; font-size: 32px !important; font-weight: 700 !important; color: var(--charcoal) !important; }
    [data-testid="stAlert"] { border-radius: 10px !important; font-size: 14px !important; }
    hr { border-color: #e8e8e8 !important; margin: 1.4rem 0 !important; }

    .history-card { background: var(--lemon-soft); border-left: 4px solid var(--lemon); border-radius: 0 10px 10px 0; padding: 1rem 1.1rem; margin-top: 0.85rem; font-size: 0.94rem; color: var(--ink); }
    .history-title { font-size: 1rem; font-weight: 800; color: var(--charcoal); margin-bottom: 0.75rem; }
    .history-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.45rem 1.2rem; }
    .history-field { display: grid; grid-template-columns: 5.5rem minmax(0, 1fr); gap: 0.35rem; align-items: start; }
    .history-label { color: var(--muted); font-weight: 700; white-space: nowrap; }
    .history-value { color: var(--charcoal); font-weight: 600; overflow-wrap: anywhere; }
    .history-subtitle { margin-top: 0.9rem; padding-top: 0.75rem; border-top: 1px solid var(--lemon-mid); font-weight: 800; color: var(--charcoal); }
    .history-order { margin-top: 0.55rem; padding: 0.65rem 0.75rem; background: rgba(255,255,255,0.58); border: 1px solid var(--lemon-mid); border-radius: 8px; }
    .history-order-main { font-weight: 800; color: var(--charcoal); margin-bottom: 0.35rem; }
    .history-order-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.25rem 1rem; color: var(--ink); }
    .history-note { margin-top: 0.75rem; color: var(--muted); }
    @media (max-width: 720px) { .history-grid, .history-order-meta { grid-template-columns: 1fr; } }
    </style>
    """, unsafe_allow_html=True)

    CLEAN_TYPE_ID_MAP = {"居家清潔": "1", "辦公室清潔": "2", "裝修細清": "3"}

    PERIOD_OPTIONS = [
        "08:30-12:30", "09:00-11:00", "09:00-12:00",
        "14:00-16:00", "14:00-17:00", "14:00-18:00",
        "09:00-16:00", "09:00-18:00",
    ]

    PERIOD_HOUR_MAP = {
        "08:30-12:30": 4, "09:00-11:00": 2, "09:00-12:00": 3,
        "14:00-16:00": 2, "14:00-17:00": 3, "14:00-18:00": 4,
        "09:00-16:00": 6, "09:00-18:00": 8,
    }

    NJ_MEMO = (
        "**N-J**\n"
        "請現場跟客戶溝通清潔優先順序,並請回報以下內容\n"
        "*工作項目+時間分配\n"
        "*特別注意事項\n"
        "*服務小貼心"
    )


    def compact_period(value):
        return str(value or "").replace(" ", "")


    def nonzero_money(value):
        try:
            return float(str(value or "0").replace(",", "")) != 0
        except Exception:
            return bool(str(value or "").strip())


    def payment_invoice_display(payway, invoice_text):
        if payway == "儲值金":
            return "儲值金客（無付款方式/發票資訊）"
        return f"付款：{payway or '未知'}　發票：{invoice_text or '未知'}"


    def booking_route_display(payway):
        if payway == "儲值金":
            return "儲值金客", "/booking/stored_value_routine"
        return "一般客", "/booking/single"


    def h(value, default="未知"):
        text = str(value or "").strip()
        return html.escape(text if text else default)


    def person_hour_display(person, hour):
        return f"{person}人{hour}小時" if (person or hour) else "未知"


    def history_field(label, value):
        return (
            '<div class="history-field">'
            f'<span class="history-label">{h(label, "")}</span>'
            f'<span class="history-value">{h(value)}</span>'
            '</div>'
        )


    def order_history_row(order):
        ph_text = person_hour_display(order.get("person"), order.get("hour"))
        payment_text = payment_invoice_display(order.get("payway"), order.get("invoice_text"))
        notice = order.get("service_notice") or "無"
        fare = order.get("fare") or ""
        fare_part = f'<div>車馬費：{h(fare, "")}</div>' if nonzero_money(fare) else ""
        return (
            '<div class="history-order">'
            f'<div class="history-order-main">{h(order.get("order_no"))}　{h(order.get("date"))} {h(order.get("time"), "")}</div>'
            '<div class="history-order-meta">'
            f'<div>人時：{h(ph_text)}</div>'
            f'<div>服務人員：{h(order.get("staff"))}</div>'
            f'<div>地址：{h(order.get("address"))}</div>'
            f'<div>{h(payment_text)}</div>'
            f'<div>客服備註：{h(notice)}</div>'
            f'{fare_part}'
            '</div>'
            '</div>'
        )


    def last_summary_card_html(summary):
        ph_text = person_hour_display(summary.get("person"), summary.get("hour"))
        payment_text = payment_invoice_display(summary.get("payway"), summary.get("invoice_text"))
        fields = [
            ("訂單", summary.get("order_no")),
            ("服務時間", f'{summary.get("date") or ""} {summary.get("time") or ""}'.strip()),
            ("地址", summary.get("address") or "無法判斷地址"),
            ("類別", summary.get("clean_type")),
            ("服務人員", summary.get("staff")),
            ("人時", ph_text),
            ("付款/發票", payment_text),
            ("客服備註", summary.get("service_notice") or "無"),
        ]
        if nonzero_money(summary.get("fare")):
            fields.append(("車馬費", summary.get("fare")))
        same_date_orders = summary.get("same_date_orders") or []
        same_date_html = ""
        if len(same_date_orders) > 1:
            same_date_html = (
                f'<div class="history-subtitle">該日期共有 {len(same_date_orders)} 筆已付款訂單</div>'
                + "".join(order_history_row(order) for order in same_date_orders)
            )
        return (
            '<div class="history-card">'
            '<div class="history-title">📌 上次（已付款）服務</div>'
            '<div class="history-grid">'
            + "".join(history_field(label, value) for label, value in fields)
            + '</div>'
            + same_date_html
            + '<div class="history-note">以上已預設帶入，如有變動請手動調整對應欄位。</div>'
            + '</div>'
        )


    def copy_button(label, text, key):
        payload = json.dumps(text, ensure_ascii=False)
        label_payload = json.dumps(label, ensure_ascii=False)
        components.html(
            f"""
            <button id="{key}" style="width:100%;padding:0.65rem 1rem;border:0;border-radius:10px;background:#F5C518;color:#1C1C1E;font-size:15px;font-weight:700;cursor:pointer;">{html.escape(label)}</button>
            <script>
            const btn = document.getElementById({json.dumps(key)});
            const text = {payload};
            const label = {label_payload};
            btn.addEventListener("click", async () => {{
                try {{ await navigator.clipboard.writeText(text); btn.textContent = "已複製"; }}
                catch (err) {{ const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); document.body.removeChild(ta); btn.textContent = "已複製"; }}
                setTimeout(() => {{ btn.textContent = label; }}, 1600);
            }});
            </script>
            """,
            height=54,
        )


    def show_duplicate_order_warning(order_no, count, dedup_key=""):
        """
        v8.13：訂單編號重複提醒視窗。
        優先使用 st.dialog 跳出真正的提醒視窗（Streamlit 1.31+）；
        若目前版本不支援 st.dialog，退回使用醒目的 st.error 區塊，
        確保任何 Streamlit 版本都看得到警示，不會被畫面其他內容淹沒。
        dedup_key 用來避免同一筆訂單在同一次畫面重繪中重複跳出視窗。
        """
        _seen_key = f"_dup_order_seen_{dedup_key or order_no}"
        if st.session_state.get(_seen_key):
            return
        st.session_state[_seen_key] = True

        message = (
            f"訂單編號 **{order_no}** 目前查詢到 **{count}** 張不同的訂單卡片，"
            f"這是後台偶發的「訂單編號重複」問題。\n\n"
            f"請務必至後台人工確認這幾張訂單卡片的實際內容，避免訂單資料互相搞混或覆蓋！"
        )

        if hasattr(st, "dialog"):
            @st.dialog("⚠️ 訂單編號重複警示")
            def _dup_order_dialog():
                st.error(message)
                if st.button("我知道了", use_container_width=True, key=f"dup_ack_{dedup_key or order_no}"):
                    st.rerun()
            _dup_order_dialog()
        else:
            st.error(f"⚠️ 訂單編號重複警示\n\n{message}")


    def step(num, title):
        st.markdown(f'<div class="step-pill"><span class="step-num">{num}</span>{title}</div>', unsafe_allow_html=True)


    def info_panel(title, bullets):
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in bullets)
        st.markdown(f'<div class="hint-box"><b>{html.escape(str(title))}</b><ul style="margin:0.45rem 0 0 1.1rem; padding:0;">{items}</ul></div>', unsafe_allow_html=True)


    def parse_row_input(row_text: str):
        if not row_text or not row_text.strip():
            raise ValueError("請輸入列號，例如：2,3,5-7")
        rows = set()
        for part in [p.strip() for p in row_text.split(",") if p.strip()]:
            if "-" in part:
                s, e = part.split("-", 1)
                s, e = int(s.strip()), int(e.strip())
                if s <= 0 or e <= 0:
                    raise ValueError("列號必須大於 0")
                if s > e:
                    raise ValueError(f"區間錯誤：{part}")
                rows.update(range(s, e + 1))
            else:
                n = int(part)
                if n <= 0:
                    raise ValueError("列號必須大於 0")
                rows.add(n)
        return sorted(rows)


    def format_log_message(msg):
        text = str(msg)
        text = text.replace("\\n", "\n")
        text = text.replace("目前環境：", "\n目前環境：")
        text = text.replace("BASE_URL：", "\nBASE_URL：")
        text = text.replace("執行區域：", "\n執行區域：")
        text = text.replace("執行工作表：", "\n執行工作表：")
        text = text.replace("執行列範圍：", "\n執行列範圍：")
        text = text.replace("處理第", "\n處理第")
        text = text.replace("已回填 Google Sheet。", "\n已回填 Google Sheet。")
        if text.startswith("▶"):
            text = "\n" + text
        return text.strip()


    # =========================================================
    # 主畫面
    # =========================================================

    if shared_backend_email is None:
        st.markdown("""
        <div class="hero">
          <div class="hero-emoji">🧹</div>
          <div>
            <div class="hero-title">服務訂單系統</div>
            <div class="hero-sub">支援批次建單、舊客快速建單、新客資料拆解、LINE 通知、確認信與 Google 日曆同步。</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    if shared_backend_email is not None:
        # 由整合頁面（pages/訂單系統.py）統一提供帳密/環境，這裡不再重複顯示登入欄位。
        backend_email = shared_backend_email
        backend_password = shared_backend_password
        env = shared_env
    else:
        step("1", "登入與環境設定")
        col_e, col_p, col_env = st.columns([3.2, 3.2, 1.2])
        with col_e:
            backend_email = st.text_input("後台帳號")
        with col_p:
            backend_password = st.text_input("後台密碼", type="password")
        with col_env:
            env = st.selectbox("環境", ["prod", "dev"], index=0)

        st.markdown("<hr>", unsafe_allow_html=True)

    if forced_mode is not None:
        # 由整合頁面直接指定要執行哪個功能，不再重複顯示功能選單。
        mode = forced_mode
    else:
        step("2", "功能選單")
        info_panel(
            "功能說明",
            [
                "批次建單：從 Google Sheet 逐列建立訂單、寄確認信、同步 Google 日曆。",
                "舊客快速建單：用電話查會員，帶入歷史已付款服務資料後建單；需求搜尋整合在此流程內。",
                "新客資料拆解：貼上客人提供的制式文字，系統拆成欄位供客服修改與複製，不直接送單。",
                "LINE 通知產生器：用已成立訂單編號補產生通知訊息，支援多筆同時產生。",
                "訂單轉換：原單A → 多筆新單B1/B2/B3，每筆各建折價券，混合配班（一般專員優先）。",
                "儲值金補價差：兩段式流程，先建儲值金清零單，再建客付補差價單。",
                "儲值金購買：客人自己買/儲值一筆金額，付款方式/發票自動沿用會員最近一次 VIP 或"
                "儲值金購買訂單的設定，都找不到才用最近一次一般服務訂單的設定。",
            ],
        )
        mode = st.radio(
            "功能選單",
            ["批次建單（Google Sheet）", "舊客快速建單", "新客資料拆解", "LINE 通知產生器", "訂單轉換", "儲值金補價差", "儲值金購買"],
            horizontal=True,
        )

    st.markdown("<hr>", unsafe_allow_html=True)

    # =========================================================
    # 模式一：批次建單
    # =========================================================
    if mode == "批次建單（Google Sheet）":
        step("3", "批次建單")
        info_panel("功能說明", [
            "適合已將多筆訂單整理在 Google Sheet 的批次處理情境。",
            "可依列號建立訂單、寄確認信、改 Google 日曆，並回填結果。",
            "全部列都執行完、Google Sheet 回填完成後，會再做一次「訂單一致性檢查」：",
            "方向一從 Sheet 出發，依電話/地址/日期/時段回查系統訂單是否相符；"
            "方向二反過來，從系統該電話的實際訂單查回 Sheet，抓出「系統其實已成單，"
            "但 Sheet 沒記錄」的情況。這是整批列都跑完後才統一做一次，不是每一列各自比對。",
        ])
        info_panel("使用說明", ["先選擇執行區域與工作表名稱。", "輸入要執行的列號，例如 2、2,3,5 或 5-10。", "勾選要執行的項目後按開始執行。"])
        step("4", "執行設定")
        c1, c2, c3 = st.columns(3)
        with c1:
            region = st.selectbox("執行區域", ["台北", "台中", "桃園", "新竹", "高雄"])
        with c2:
            sheet_name = st.text_input("工作表名稱", value="", placeholder="例：202604")
        with c3:
            row_input = st.text_input("執行列號", value="", placeholder="例：2,3,5-7")
        st.markdown('<div class="hint-box">💡 列號支援：單列 <code>2</code>、逗號分隔 <code>2,3,5</code>、區間 <code>2,3,5-7</code></div>', unsafe_allow_html=True)
        st.markdown("<hr>", unsafe_allow_html=True)
        step("3", "執行項目")
        default_actions = (["建單", "寄確認信", "改 Google 日曆"] if env == "prod" else ["建單"])
        selected_actions = st.multiselect("執行項目", options=["建單", "寄確認信", "改 Google 日曆"], default=default_actions, label_visibility="collapsed")
        st.markdown('<div class="hint-box">可自由組合，例如只寄確認信、只改日曆，或全流程一起跑。</div>', unsafe_allow_html=True)
        # v8.14：查無班表時是否自動補檸檬人，預設不勾選，需客服明確開啟。
        # 與舊客快速建單、新客資料拆解、訂單轉換三個流程行為一致。
        batch_allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人排班", value=False, key="batch_allow_auto_lemon")
        # v8.19：一致性檢查改成看得到的獨立勾選框，預設開啟；在「全部列都執行完」
        # 之後才統一做一次整批雙向比對，而不是每一列各自比對一次。
        batch_run_consistency_check = st.checkbox(
            "全部執行完後做一次訂單一致性檢查（雙向比對電話/地址/日期/時段）",
            value=True, key="batch_run_consistency_check",
        )
        st.markdown("<hr>", unsafe_allow_html=True)
        run_clicked = st.button("🚀  開始執行", use_container_width=True)
        with st.expander("📄  執行過程", expanded=True):
            log_box = st.empty()
            log_box.code("尚未執行")
        result_container = st.container()
        if run_clicked:
            if not backend_email.strip():
                st.error("請輸入後台帳號"); st.stop()
            if not backend_password.strip():
                st.error("請輸入後台密碼"); st.stop()
            if not sheet_name.strip():
                st.error("請輸入工作表名稱"); st.stop()
            if not selected_actions:
                st.error("請至少選擇一個執行項目"); st.stop()
            try:
                target_rows = parse_row_input(row_input)
            except Exception as e:
                st.error(f"列號格式錯誤：{e}"); st.stop()
            logs = []
            def ui_log(msg):
                logs.append(format_log_message(msg))
                display_text = "\n\n".join(logs[-120:])
                log_box.code(display_text)
            total_success = 0
            total_fail = 0
            total_processed = 0
            with st.spinner("執行中，請稍候…"):
                for row_no in target_rows:
                    ui_log(f"▶ 開始執行第 {row_no} 列…")
                    try:
                        result = run_process_web(
                            env_name=env, region=region,
                            backend_email=backend_email.strip(), backend_password=backend_password.strip(),
                            sheet_name=sheet_name.strip(), start_row=row_no, end_row=row_no,
                            selected_actions=selected_actions, logger=ui_log,
                            allow_auto_lemon_shift=batch_allow_auto_lemon,
                        )
                        if isinstance(result, dict):
                            total_success += result.get("success_count", 0)
                            total_fail += result.get("fail_count", 0)
                            total_processed += result.get("total_processed", 0)
                    except Exception as e:
                        total_fail += 1
                        ui_log(f"❌ 第 {row_no} 列失敗：{e}")
            ui_log("===== 建單流程執行完成 =====")

            # v8.19：所有列都跑完、Google Sheet 都回填之後，才統一做一次整批
            # 一致性檢查（雙向比對），而不是每一列各自比對一次。
            all_consistency_problems = []
            consistency_ran = False
            if batch_run_consistency_check and total_processed > 0:
                ui_log("▶ 開始整批訂單一致性檢查（雙向比對）…")
                try:
                    with st.spinner("整批比對中，請稍候…"):
                        all_consistency_problems = run_batch_consistency_check(
                            env_name=env, region=region,
                            backend_email=backend_email.strip(), backend_password=backend_password.strip(),
                            sheet_name=sheet_name.strip(), target_rows=target_rows, logger=ui_log,
                        )
                    consistency_ran = True
                except Exception as e:
                    ui_log(f"❌ 一致性檢查失敗：{e}")
            ui_log("===== 全部執行完成 =====")

            with result_container:
                st.markdown("<hr>", unsafe_allow_html=True)
                step("4", "執行結果")
                c1, c2, c3 = st.columns(3)
                c1.metric("執行筆數", total_processed)
                c2.metric("成功", total_success)
                c3.metric("失敗", total_fail)
                if total_fail == 0 and total_processed > 0:
                    st.success(f"✅ 全部完成，共處理 **{total_processed}** 筆，成功 **{total_success}** 筆。")
                elif total_fail > 0:
                    st.warning(f"⚠️ 執行完成，但有 **{total_fail}** 筆失敗，請查看執行過程。")
                else:
                    st.info("執行完成，無資料被處理。")

                st.markdown("<hr>", unsafe_allow_html=True)
                step("5", "訂單一致性檢查（雙向比對）")
                if not batch_run_consistency_check:
                    st.info("本次未勾選「全部執行完後做一次訂單一致性檢查」，未執行比對。")
                elif not consistency_ran:
                    st.warning("一致性檢查未成功執行，請查看上方執行過程的錯誤訊息。")
                elif all_consistency_problems:
                    st.error(f"⚠️ 發現 {len(all_consistency_problems)} 筆異常，請人工確認：")
                    for _p in all_consistency_problems:
                        _row_label = f"第 {_p.get('row_num')} 列" if _p.get("row_num") is not None else "（系統反查）"
                        st.warning(f"{_row_label}（訂單 {_p.get('order_no', '')}）：{_p.get('issue')}")
                else:
                    st.success("✅ 檢查通過，本次寫回的訂單編號皆與 Google Sheet 電話/地址/日期/時段相符。")



    # =========================================================
    # 其他功能
    # =========================================================
    else:
        single_feature = mode
        step("3", single_feature)

        # --------------------------------------------------
        # LINE 通知產生器
        # --------------------------------------------------
        if single_feature == "LINE 通知產生器":
            col_left, col_right = st.columns([3, 1])
            with col_left:
                info_panel("使用說明", ["輸入已成立訂單編號，每行一個，可一次輸入多筆。", "系統讀取訂單日期、地址、付款方式與金額，區域由地址自動判斷。"])
                line_order_nos_input = st.text_area("訂單編號（每行一個）", value="", height=120, placeholder="LC00211537\nLC00211538", key="line_order_nos")
                if st.button("產生 LINE 訊息", use_container_width=True, key="make-line-from-order-no"):
                    if not backend_email.strip() or not backend_password.strip():
                        st.error("請先輸入後台帳號密碼")
                    else:
                        raw_lines = [x.strip() for x in line_order_nos_input.splitlines() if x.strip()]
                        order_groups = []
                        for line in raw_lines:
                            nos = [n.strip() for n in line.split(",") if n.strip()]
                            if nos:
                                order_groups.append(nos)
                        if not order_groups:
                            st.error("請輸入至少一個訂單編號")
                        else:
                            st.session_state.line_from_order_nos_results = []
                            for _k in list(st.session_state.keys()):
                                if _k.startswith("line_text_") or _k.startswith("nj_memo_"):
                                    del st.session_state[_k]
                            results_list = []
                            for nos in order_groups:
                                label = "、".join(nos)
                                try:
                                    with st.spinner(f"查詢訂單 {label}…"):
                                        line_result, line_text = build_combined_line_message_from_order_nos(
                                            env_name=env, backend_email=backend_email.strip(),
                                            backend_password=backend_password.strip(), order_nos=nos,
                                        )
                                    safe_result = {k: v for k, v in line_result.items() if k != "session"}
                                    results_list.append({"order_no": label, "result": safe_result, "text": line_text, "error": None})
                                except Exception as e:
                                    results_list.append({"order_no": label, "result": None, "text": "", "error": str(e)})
                            st.session_state.line_from_order_nos_results = results_list
                            st.rerun()
            with col_right:
                st.markdown('<div class="sec-label">N-J Memo</div>', unsafe_allow_html=True)
                st.text_area("N-J Memo", NJ_MEMO, height=220, key="nj_memo_fixed", label_visibility="collapsed")
                copy_button("複製 N-J Memo", NJ_MEMO, "copy-nj-memo-fixed")
            results_list = st.session_state.get("line_from_order_nos_results", [])
            for idx, item in enumerate(results_list):
                if item["error"]:
                    st.error(f"訂單 {item['order_no']} 產生失敗：{item['error']}")
                    continue
                line_result = item["result"]
                line_text = item["text"]
                all_nos = line_result.get("all_order_nos") or [line_result.get("order_no")]
                order_no_display = "、".join(str(n) for n in all_nos if n)
                is_combined = len(all_nos) > 1
                is_multi_date = line_result.get("multi_date", False)
                combined_note = "　⚠️ 跨日合併單" if (is_combined and is_multi_date) else ("　⚠️ 同日合併單" if is_combined else "")
                st.caption(f"訂單：{order_no_display}{combined_note}　付款方式：{line_result.get('payway')}　區域：{line_result.get('region')}　金額：{line_result.get('service_amount') or '—'}　車馬費：{line_result.get('fare') or '0'}")
                st.text_area(f"LINE 訊息（{line_result.get('order_no')}）", line_text, height=380, label_visibility="collapsed")
                copy_button("複製 LINE 訊息", line_text, f"copy-line-msg-{idx}")
                if idx < len(results_list) - 1:
                    st.markdown("<hr>", unsafe_allow_html=True)

        # --------------------------------------------------
        # 舊客快速建單
        # --------------------------------------------------
        elif single_feature == "舊客快速建單":
            info_panel("功能說明", ["用電話查詢會員與歷史已付款服務。", "多地址客人會顯示各地址近一年紀錄，請先跟客人確認地址。", "可選已知日期查班表，也可依客人需求搜尋可服務日期。"])
            q1, q2 = st.columns(2)
            with q1:
                q_phone = st.text_input("客人電話", key="old_phone")
            with q2:
                q_clean_type = st.selectbox("購買項目", list(CLEAN_TYPE_ID_MAP.keys()), key="old_clean_type")
            if st.button("🔍  查詢會員", use_container_width=True, key="old_lookup_btn"):
                if not backend_email.strip() or not backend_password.strip():
                    st.error("請先輸入後台帳號密碼"); st.stop()
                if not q_phone.strip():
                    st.error("請輸入客人電話"); st.stop()
                try:
                    with st.spinner("查詢中…"):
                        st.session_state.q_lookup = quick_lookup_member(env_name=env, backend_email=backend_email.strip(), backend_password=backend_password.strip(), phone=q_phone.strip(), clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type])
                    st.session_state.q_order_result = {}
                except Exception as e:
                    st.error(f"查詢失敗：{e}")
                    st.session_state.q_lookup = None
            lookup = st.session_state.get("q_lookup")
            if lookup is not None:
                member_payload = lookup.get("member_payload")
                st.markdown("<hr>", unsafe_allow_html=True)
                if not member_payload:
                    st.warning("查無此會員，請填寫下方資料建立新客訂單。")
                    st.markdown("**新客資料**")
                    nc1, nc2, nc3 = st.columns(3)
                    with nc1:
                        nc_name = st.text_input("姓名", key="nc_name")
                    with nc2:
                        nc_email = st.text_input("Email", key="nc_email")
                    with nc3:
                        nc_tel = st.text_input("市內電話（選填）", key="nc_tel")
                    nc_address = st.text_input("服務地址", key="nc_address")
                    na1, na2, na3, na4 = st.columns(4)
                    with na1:
                        nc_date = st.date_input("服務日期", value=date.today() + timedelta(days=1), key="nc_date")
                    with na2:
                        nc_period = st.selectbox("時段", PERIOD_OPTIONS, key="nc_period")
                    with na3:
                        nc_person = st.number_input("人數", min_value=1, max_value=8, value=2, key="nc_person")
                    with na4:
                        nc_hour = PERIOD_HOUR_MAP.get(nc_period, 3)
                        st.markdown(f"<br><b>{nc_hour} 小時</b>", unsafe_allow_html=True)
                    nb1, nb2 = st.columns(2)
                    with nb1:
                        nc_payway = st.selectbox("付款方式", ["信用卡", "ATM"], key="nc_payway")
                    with nb2:
                        nc_invoice = st.selectbox("發票", ["會員載具（email）", "手機載具", "三聯式統編"], key="nc_invoice")
                    nc_carrier = ""
                    nc_company_title = ""
                    nc_company_no = ""
                    if nc_invoice == "手機載具":
                        nc_carrier = st.text_input("手機條碼", placeholder="/ABC1234", key="nc_carrier")
                    elif nc_invoice == "三聯式統編":
                        nci1, nci2 = st.columns(2)
                        with nci1:
                            nc_company_title = st.text_input("公司抬頭", key="nc_company_title")
                        with nci2:
                            nc_company_no = st.text_input("統一編號", key="nc_company_no")
                    nc_clean_type = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), key="nc_clean_type")
                    # v8.13：查無班表時是否自動補檸檬人，預設不勾選，需客服明確開啟
                    nc_allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人排班", value=False, key="nc_allow_auto_lemon")
                    if st.button("🚀 建立新客訂單", use_container_width=True, key="nc_create_btn"):
                        # v8.15：開始新的一次建單嘗試前，先清空上一次殘留在畫面下方的舊結果，
                        # 避免這次失敗時，舊的成功訊息還留在畫面上跟新的錯誤訊息重疊混淆。
                        st.session_state.q_order_result = {}
                        if not nc_name.strip() or not nc_email.strip() or not nc_address.strip():
                            st.error("請填寫姓名、Email、服務地址")
                        elif not backend_email.strip() or not backend_password.strip():
                            st.error("請先輸入後台帳號密碼")
                        else:
                            try:
                                with st.spinner("建立會員 → 查詢地址 → 建立訂單…"):
                                    nc_result = qo.quick_create_new_customer_order(
                                        env_name=env,
                                        backend_email=backend_email.strip(),
                                        backend_password=backend_password.strip(),
                                        allow_auto_lemon_shift=nc_allow_auto_lemon,
                                        customer={
                                            "name": nc_name.strip(),
                                            "phone": q_phone.strip(),
                                            "email": nc_email.strip(),
                                            "tel": nc_tel.strip(),
                                            "address": nc_address.strip(),
                                            "payway": nc_payway,
                                            "clean_type_id": CLEAN_TYPE_ID_MAP[nc_clean_type],
                                            "date_s": nc_date.strftime("%Y-%m-%d"),
                                            "period_s": nc_period,
                                            "hour": str(nc_hour),
                                            "person": str(int(nc_person)),
                                            "carrier": nc_carrier,
                                            "company_title": nc_company_title,
                                            "company_no": nc_company_no,
                                        }
                                    )
                                    # 不立即發確認信，等 user 確認後再發
                                    nc_result["mail_sent"] = False
                                    nc_result["mail_msg"] = "尚未發送"
                                st.session_state.q_order_result = nc_result
                                st.success(f"✅ 訂單建立成功：{nc_result['order_no']}")
                            except Exception as e:
                                st.error(f"建單失敗：{e}")
                else:
                    member = member_payload.get("member", {})
                    addr_list = member_payload.get("member", {}).get("memberAddressList", [])
                    addr_options = [a.get("address", "") for a in addr_list if a.get("address")]
                    st.markdown(f"**會員姓名：** {member.get('name', '')}　|　**會員電話：** {lookup.get('phone', '')}")
                    step("3", "舊客服務資訊")
                    info_panel("使用說明", ["先確認服務地址。", "確認服務類別、付款方式與區域。", "依客人狀況選擇『已知日期』或『依需求搜尋』。"])
                    if not addr_options:
                        st.error("此會員沒有留存地址，請改用新客建單或先至後台補會員地址。")
                    else:
                        last_summary = get_last_paid_summary(lookup["session"], lookup["phone"], member_payload, addr_options)
                        default_addr_index = addr_options.index(last_summary["address"]) if last_summary and last_summary.get("address") in addr_options else 0
                        q_address = st.selectbox("服務地址", addr_options, index=default_addr_index, key="old_address")
                        if len(addr_options) > 1:
                            st.caption(f"⚠️ 此客人留存 {len(addr_options)} 個地址，請務必跟客人確認本次地點是否正確。")
                            per_addr_summary = get_last_paid_per_address(lookup["session"], lookup["phone"], member_payload, addr_options, within_days=365)
                            addr_rows = []
                            for addr in addr_options:
                                info = per_addr_summary.get(addr)
                                if not info:
                                    addr_rows.append(f"・{addr}　——　近一年內查無已付款服務紀錄")
                                else:
                                    ph_text = f"{info['person']}人{info['hour']}小時" if (info["person"] or info["hour"]) else "未知"
                                    payment_text = payment_invoice_display(info.get("payway"), info.get("invoice_text"))
                                    addr_rows.append(f"・{addr}　——　{info['date']} {info['time']}　類別：{info['clean_type'] or '未知'}　人時：{ph_text}　{payment_text}")
                            st.markdown('<div class="hint-box">📍 <b>各地址近一年內最近一次已付款服務</b>：<br>' + "<br>".join(addr_rows) + '</div>', unsafe_allow_html=True)
                        default_clean_type = last_summary["clean_type"] if last_summary and last_summary.get("clean_type") in CLEAN_TYPE_ID_MAP else "居家清潔"
                        default_person = int(last_summary["person"]) if last_summary and str(last_summary.get("person", "")).isdigit() else 2
                        q_clean_type_confirm = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), index=list(CLEAN_TYPE_ID_MAP.keys()).index(default_clean_type), key="old_clean_confirm")
                        # v8.6：付款方式選單新增「信用卡/ATM」選項——維持上次付款方式（僅限信用卡或ATM）
                        # 選單顯示：信用卡/ATM、信用卡、ATM、儲值金
                        # 實際送單時一律解析成「信用卡」「ATM」或「儲值金」三者之一
                        _payway_ui_options = ["信用卡/ATM", "信用卡", "ATM", "儲值金"]
                        _last_payway = last_summary.get("payway") if last_summary else ""
                        _default_ui_payway = "儲值金" if _last_payway == "儲值金" else "信用卡/ATM"
                        _q_payway_ui = st.selectbox(
                            "付款方式",
                            _payway_ui_options,
                            index=_payway_ui_options.index(_default_ui_payway),
                            key="old_payway",
                        )
                        if _q_payway_ui == "信用卡/ATM":
                            # 沿用上次付款方式；若上次不是信用卡或ATM（例如儲值金或查無紀錄），預設信用卡
                            q_payway = _last_payway if _last_payway in ("信用卡", "ATM") else "信用卡"
                            _payway_note = f"（沿用上次：{q_payway}）"
                        else:
                            q_payway = _q_payway_ui
                            _payway_note = ""
                        q_region = get_region_by_address(q_address, ACCOUNTS) or "台北"
                        _route_label, _route_url = booking_route_display(q_payway)
                        st.caption(f"建單介面：{_route_label}　｜　送單網址：{_route_url}　｜　實際付款方式：{q_payway}{_payway_note}　｜　區域：{q_region}")
                        if last_summary:
                            st.markdown(last_summary_card_html(last_summary), unsafe_allow_html=True)
                        upcoming_orders = get_unserved_paid_orders(lookup["session"], lookup["phone"], member_payload, addr_options, today_value=date.today())
                        if upcoming_orders:
                            st.markdown('<div class="hint-box"><b>⚠️ 目前已付款但尚未服務訂單</b><br>請先確認客人是否要異動既有訂單，避免重複建單。</div>', unsafe_allow_html=True)
                            for idx, order in enumerate(upcoming_orders, start=1):
                                ph_text = person_hour_display(order.get("person"), order.get("hour"))
                                payment_text = payment_invoice_display(order.get("payway"), order.get("invoice_text"))
                                address_text = order.get("address") or "未能對應留存地址，請至後台確認"
                                staff_text = order.get("staff") or "待確認"
                                fare_text = f"｜車馬費：{order.get('fare')}" if nonzero_money(order.get("fare")) else ""
                                st.markdown(f'<div class="history-order"><div class="history-order-main">{idx}. {h(order.get("order_no"))}　{h(order.get("date"))} {h(order.get("time"), "")}</div><div class="history-order-meta"><div>地址：{h(address_text)}</div><div>類別：{h(order.get("clean_type"))}</div><div>服務人員：{h(staff_text)}</div><div>人時：{h(ph_text)}{h(fare_text, "")}</div><div>{h(payment_text)}</div></div></div>', unsafe_allow_html=True)
                        date_mode = st.radio("日期/班表查詢方式", ["已知日期", "依需求搜尋可服務日期"], horizontal=True, key="old_date_mode")
                        if date_mode == "已知日期":
                            info_panel("已知日期使用說明", ["客人已指定某一天時使用。", "此模式才需要選服務日期與時段。", "若客人只說平日、週末、不限或幾小時，請改選『依需求搜尋可服務日期』。"])
                            d1, d2, d3, d4 = st.columns(4)
                            with d1:
                                q_date = st.date_input("服務日期", value=date.today(), key="old_known_date")
                            with d2:
                                q_period = st.selectbox("時段", PERIOD_OPTIONS, key="old_known_period")
                            with d3:
                                q_person = st.number_input("人數", min_value=1, max_value=8, value=default_person, key="old_known_person")
                            with d4:
                                q_hour = PERIOD_HOUR_MAP.get(q_period, 3)
                                st.markdown(f'<br><b>{q_hour} 小時</b>（依時段自動帶出）<br><span style="color:#8E8E93;font-size:13px;">人時：{int(q_person) * int(q_hour)}</span>', unsafe_allow_html=True)
                            if st.button("🔎 查詢該日班表", use_container_width=True, key="old_check_known"):
                                try:
                                    with st.spinner("查詢班表中…"):
                                        rows = quick_check_available_slots(env_name=env, payway=q_payway, lookup_result=lookup, address=q_address, clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm], date_s=q_date.strftime("%Y-%m-%d"), hour=q_hour, person=q_person, periods=[q_period], period_hours=PERIOD_HOUR_MAP)
                                    st.session_state.old_known_slots = rows
                                except Exception as e:
                                    st.session_state.old_known_slots = []
                                    st.error(f"查詢班表失敗：{e}")
                            rows = st.session_state.get("old_known_slots")
                            if rows:
                                if any(r.get("available") for r in rows):
                                    for r in rows:
                                        st.success(f"{r.get('date')} {r.get('period')} 可安排　服務人員：{r.get('staff') or '待確認'}")
                                else:
                                    st.warning("此日期/時段目前無可安排班表。")
                            # v8.13：查無班表時是否自動補檸檬人，預設不勾選，需客服明確開啟
                            old_allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人排班", value=False, key="old_allow_auto_lemon")
                            if st.button("🚀 建立訂單", use_container_width=True, key="old_create_known"):
                                # v8.15：開始新的一次建單嘗試前，先清空上一次殘留的舊結果。
                                st.session_state.q_order_result = {}
                                try:
                                    with st.spinner("建單中，請稍候…"):
                                        result = quick_create_order(env_name=env, payway=q_payway, region=q_region, lookup_result=lookup, address=q_address, clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm], date_s=q_date.strftime("%Y-%m-%d"), period_s=q_period, hour=q_hour, person=q_person, allow_auto_lemon_shift=old_allow_auto_lemon)
                                        # 不立即發確認信，等 user 確認後再發
                                        result["mail_sent"] = False
                                        result["mail_msg"] = "尚未發送"
                                    st.session_state.q_order_result = result
                                except Exception as e:
                                    st.error(f"建單失敗：{e}")
                        else:
                            info_panel("依需求搜尋使用說明", ["客人尚未指定日期時使用。", "可選平日 / 週末 / 不限，也可選上午 / 下午 / 不限。"])
                            a1, a2, a3, a4 = st.columns(4)
                            with a1:
                                day_type = st.selectbox("日期類型", ["平日", "週末", "不限"], key="old_day_type")
                            with a2:
                                time_pref = st.selectbox("時段偏好", ["上午", "下午", "不限"], key="old_time_pref")
                            with a3:
                                base_person = st.number_input("人數", min_value=1, max_value=8, value=2, key="old_search_person")
                            with a4:
                                base_hour = st.number_input("每人時數", min_value=2, max_value=8, value=4, key="old_search_hour")
                            search_days = st.slider("往後搜尋天數", min_value=7, max_value=60, value=30, step=1, key="old_search_days")
                            plans = build_equivalent_plans(base_person, base_hour)
                            total_ph = int(base_person) * int(base_hour)
                            st.caption(f"人時 = {int(base_person)} 人 × {int(base_hour)} 小時 = {total_ph} 人時")
                            st.caption("將查詢方案：" + "、".join([f"{p['person']}人{p['hour']}小時" for p in plans]))
                            if st.button("🔎 搜尋可服務日期", use_container_width=True, key="old_search_dates"):
                                try:
                                    with st.spinner("搜尋可服務日期中…"):
                                        rows = search_available_service_dates(env_name=env, payway=q_payway, lookup_result=lookup, address=q_address, clean_type_id=CLEAN_TYPE_ID_MAP[q_clean_type_confirm], start_date=date.today(), days=search_days, day_type=day_type, time_preference=time_pref, plans=plans, periods=PERIOD_OPTIONS, period_hours=PERIOD_HOUR_MAP)
                                    st.session_state.old_search_results = rows
                                except Exception as e:
                                    st.session_state.old_search_results = []
                                    st.error(f"搜尋失敗：{e}")
                            rows = st.session_state.get("old_search_results")
                            if rows is not None:
                                if rows:
                                    st.markdown("**可服務日期搜尋結果**")
                                    for idx, r in enumerate(rows[:20]):
                                        st.write(f"{idx+1}. 方案：{r['person']}人{r['hour']}小時　{r['date']} {r['period']}　服務人員：{r.get('staff') or '待確認'}")
                                else:
                                    st.warning("目前依條件搜尋不到可服務日期，請放寬日期類型、時段偏好或延長搜尋天數。")

        # --------------------------------------------------
        # 新客資料拆解
        # --------------------------------------------------
        elif single_feature == "新客資料拆解":
            info_panel("功能說明", [
                "貼上客人提供的完整資料（含姓名/電話/email/地址/坪數/付款/發票），",
                "填入服務日期與人時後按建單，系統自動拆解、建會員、建單，",
                "班表無人時自動勾檸檬人，完成後顯示訂單資訊與 LINE 訊息。",
            ])

            step("1", "貼上客人資料")
            nc_raw = st.text_area(
                "客人提供的資料（直接整段貼入）",
                height=200, key="nc_raw_input",
                placeholder="訂購人姓名：XXX\n訂購人電話：09XXXXXXXX\n訂購人Email：xxx@xxx.com\n服務地址：台北市...\n室內坪數：約25坪\n付款方式：信用卡\n發票載具：手機載具 /XXXXXXX",
            )

            # v8.12：不管有沒有「訂購人姓名：」等標籤都要能辨識欄位，貼上後即時拆解預覽。
            # 付款方式若判斷不出來，不可默默預設，直接請客服在這裡手動選擇。
            _nc_live_parsed = {}
            if nc_raw.strip():
                try:
                    _nc_live_parsed = qo.parse_new_customer_text(nc_raw)
                except Exception:
                    _nc_live_parsed = {}
            if _nc_live_parsed:
                _preview_bits = []
                if _nc_live_parsed.get("name"):
                    _preview_bits.append(f"姓名：{_nc_live_parsed['name']}")
                if _nc_live_parsed.get("phone"):
                    _preview_bits.append(f"電話：{_nc_live_parsed['phone']}")
                if _nc_live_parsed.get("address"):
                    _preview_bits.append(f"地址：{_nc_live_parsed['address']}")
                if _preview_bits:
                    st.caption("已辨識　" + "　".join(_preview_bits))
                if _nc_live_parsed.get("need_ask_payway"):
                    st.warning("⚠️ 無法從貼上的資料中判斷付款方式，請手動選擇：")
                    st.selectbox("付款方式（手動選擇）", ["信用卡", "ATM"], key="nc_payway_manual_select")
                elif _nc_live_parsed.get("payway"):
                    st.caption(f"✅ 已偵測付款方式：{_nc_live_parsed['payway']}")

            step("2", "服務設定")
            sc1, sc2, sc3 = st.columns(3)
            with sc1:
                nc_clean_type = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), key="nc_clean_type_d")
            with sc2:
                nc_service_type = ""
                if nc_clean_type == "裝修細清":
                    _stype_map = {"裝修細清": "1", "搬出清潔": "2", "搬入清潔": "3"}
                    _stype_sel = st.selectbox("裝修類型", list(_stype_map.keys()), key="nc_stype_d")
                    nc_service_type = _stype_map[_stype_sel]
            with sc3:
                pass

            # 清潔項目細節
            with st.expander("🏠 清潔項目細節（選填，用於計算時數）", expanded=False):
                _ci1, _ci2, _ci3, _ci4, _ci5, _ci6 = st.columns(6)
                with _ci1:
                    nc_room = st.number_input("房間", min_value=0, value=0, key="nc_room_d")
                with _ci2:
                    nc_bathroom = st.number_input("衛浴", min_value=0, value=0, key="nc_bathroom_d")
                with _ci3:
                    nc_balcony = st.number_input("陽台", min_value=0, value=0, key="nc_balcony_d")
                with _ci4:
                    nc_livingroom = st.number_input("客廳", min_value=0, value=0, key="nc_livingroom_d")
                with _ci5:
                    nc_kitchen = st.number_input("廚房", min_value=0, value=0, key="nc_kitchen_d")
                with _ci6:
                    nc_window = st.text_input("窗戶", value="", placeholder="數量", key="nc_window_d")
                _ci7, _ci8 = st.columns([1, 5])
                with _ci7:
                    nc_shutter = st.text_input("百葉窗", value="", placeholder="數量", key="nc_shutter_d")
                st.markdown("**加購項目**")
                _bv1, _bv2, _bv3, _bv4, _bv5, _bv6, _bv7, _bv8, _bv9 = st.columns(9)
                with _bv1:
                    nc_clothes = "1" if st.checkbox("衣物洗晾", key="nc_clothes_d") else "0"
                with _bv2:
                    nc_dyson = "1" if st.checkbox("DYSON除蟎", key="nc_dyson_d") else "0"
                with _bv3:
                    nc_refrigerator = "1" if st.checkbox("冰箱清理", key="nc_fridge_d") else "0"
                with _bv4:
                    nc_disinfection = "1" if st.checkbox("簡易消毒", key="nc_disinfect_d") else "0"
                with _bv5:
                    nc_go_abroad = "1" if st.checkbox("30日內出國", key="nc_abroad_d") else "0"
                with _bv6:
                    nc_home_move = "1" if st.checkbox("搬家打包", key="nc_move_d") else "0"
                with _bv7:
                    nc_storage = "1" if st.checkbox("收納整理", key="nc_storage_d") else "0"
                with _bv8:
                    nc_cabinet = "1" if st.checkbox("櫥櫃清潔", key="nc_cabinet_d") else "0"
                with _bv9:
                    nc_quintuple = "1" if st.checkbox("五倍券", key="nc_quintuple_d") else "0"

            step("3", "日期與人時")
            sd1, sd2, sd3, sd4 = st.columns(4)
            with sd1:
                nc_date = st.date_input("服務日期", value=date.today() + timedelta(days=1), key="nc_date_d")
            with sd2:
                nc_period = st.selectbox("時段", PERIOD_OPTIONS, key="nc_period_d")
            with sd3:
                nc_person = st.number_input("人數", min_value=1, max_value=8, value=2, key="nc_person_d")
            with sd4:
                nc_hour = PERIOD_HOUR_MAP.get(nc_period, 3)
                _day_type_nc = "週末" if nc_date.weekday() >= 5 else "平日"
                _unit_nc = 700 if _day_type_nc == "週末" else 600
                _total_nc = int(nc_person) * nc_hour * _unit_nc
                st.markdown(f"**{nc_hour}小時 / {_day_type_nc}**")
                st.markdown(f"預估：**{_total_nc:,}元**")

            step("4", "備註欄位（選填）")
            nb1, nb2, nb3 = st.columns(3)
            with nb1:
                nc_actual_time = st.text_input("簡訊實際服務時間", placeholder="例：09:00-12:00", key="nc_actual_time_d")
            with nb2:
                nc_memo = st.text_area("客人備註", height=80, key="nc_memo_d")
            with nb3:
                nc_notice = st.text_area("客服備註", height=80, key="nc_notice_d")

            # v8.13：查無班表時是否自動補檸檬人，預設不勾選，需客服明確開啟
            nc_d_allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人排班", value=False, key="nc_d_allow_auto_lemon")

            if st.button("🚀 建立新客訂單", use_container_width=True, key="nc_create_d", type="primary"):
                # v8.15：開始新的一次建單嘗試前，先清空上一次殘留在畫面下方的舊結果
                # （包含成功訊息、LINE 訊息），避免這次失敗/拆解失敗時，
                # 舊的成功結果還留在畫面上跟新的錯誤訊息重疊混淆。
                st.session_state.nc_result = {}
                if not nc_raw.strip():
                    st.error("請貼上客人資料")
                elif not backend_email.strip() or not backend_password.strip():
                    st.error("請先在上方輸入後台帳號密碼")
                else:
                    # 拆解客人資料
                    try:
                        _parsed = qo.parse_new_customer_text(nc_raw)
                    except Exception:
                        _parsed = {}
                    _nc_name = _parsed.get("name", "")
                    _nc_phone = _parsed.get("phone", "")
                    _nc_email = _parsed.get("email", "")
                    _nc_address = _parsed.get("address", "")
                    _nc_ping = _parsed.get("ping", "4")
                    # v8.12：付款方式偵測不到時，改用上方手動選擇的值；兩者皆無則擋下建單，
                    # 不可默默預設成信用卡。
                    _nc_payway = _parsed.get("payway", "") or st.session_state.get("nc_payway_manual_select", "")
                    _nc_carrier = _parsed.get("carrier", "")
                    _nc_company_title = _parsed.get("company_title", "")
                    _nc_company_no = _parsed.get("company_no", "")

                    _missing = [k for k, v in [("姓名", _nc_name), ("電話", _nc_phone), ("Email", _nc_email), ("地址", _nc_address)] if not v.strip()]
                    if not _nc_payway:
                        st.error("無法判斷付款方式，請於上方「付款方式（手動選擇）」選單選擇信用卡或ATM後再建單。")
                    elif _missing:
                        st.error(f"資料拆解失敗，請確認以下欄位：{'、'.join(_missing)}\n\n拆解結果：{_parsed}")
                    else:
                        try:
                            with st.spinner(f"建立會員 → 查詢地址 → 建單（{nc_date} {nc_period} {nc_person}人{nc_hour}小時）…"):
                                nc_result = qo.quick_create_new_customer_order(
                                    env_name=env,
                                    backend_email=backend_email.strip(),
                                    backend_password=backend_password.strip(),
                                    allow_auto_lemon_shift=nc_d_allow_auto_lemon,
                                    customer={
                                        "name": _nc_name, "phone": _nc_phone,
                                        "email": _nc_email, "address": _nc_address,
                                        "ping": _nc_ping, "payway": _nc_payway,
                                        "clean_type_id": CLEAN_TYPE_ID_MAP[nc_clean_type],
                                        "service_type": nc_service_type,
                                        "room": str(nc_room), "bathroom": str(nc_bathroom),
                                        "balcony": str(nc_balcony), "livingroom": str(nc_livingroom),
                                        "kitchen": str(nc_kitchen), "window": nc_window,
                                        "shutter": nc_shutter, "clothes": nc_clothes,
                                        "dyson": nc_dyson, "refrigerator": nc_refrigerator,
                                        "disinfection": nc_disinfection, "go_abord": nc_go_abroad,
                                        "home_move": nc_home_move, "storage": nc_storage,
                                        "cabinet": nc_cabinet, "quintuple": nc_quintuple,
                                        "date_s": nc_date.strftime("%Y-%m-%d"),
                                        "period_s": nc_period,
                                        "hour": str(nc_hour),
                                        "person": str(int(nc_person)),
                                        "carrier": _nc_carrier,
                                        "company_title": _nc_company_title,
                                        "company_no": _nc_company_no,
                                        "memo": nc_memo,
                                        "notice": nc_notice,
                                        "actual_time": nc_actual_time,
                                    }
                                )
                                # 不立即發確認信，等 user 確認後再發
                                nc_result["mail_sent"] = False
                                nc_result["mail_msg"] = "尚未發送"
                                # v8.6：quick_create_new_customer_order 已回傳 build_line_message
                                # 所需的完整欄位（date/period/region/fare 等），這裡直接組出 LINE 訊息，
                                # 修正原本此流程從未產生 line_message、畫面永遠不顯示的問題。
                                try:
                                    nc_result["line_message"] = build_line_message(nc_result)
                                except Exception as _e_line_nc:
                                    nc_result["line_message"] = ""
                                    st.warning(f"LINE 訊息組裝失敗：{_e_line_nc}")
                            st.session_state.nc_result = nc_result
                            st.rerun()
                        except Exception as e:
                            st.error(f"建單失敗：{e}")

            # 顯示建單結果
            _r = st.session_state.get("nc_result", {})
            if _r.get("order_no"):
                st.success(f"✅ 訂單：{_r['order_no']}　{_r.get('date_s')} {_r.get('period_s')}　{_r.get('person')}人{_r.get('hour')}小時　{_r.get('price_with_tax', 0):,}元")
                if _r.get("price_mismatch_warning"):
                    st.warning(_r["price_mismatch_warning"])
                if _r.get("address_mismatch_warning"):
                    st.warning(_r["address_mismatch_warning"])
                if _r.get("order_no_duplicated"):
                    show_duplicate_order_warning(_r.get("order_no"), _r.get("order_no_duplicate_count", 2), dedup_key=f"nc_{_r.get('order_no')}")
                if not _r.get("mail_sent"):
                    if st.button("📧 發送確認信", key="nc_send_mail_btn", type="primary"):
                        try:
                            ok_m2, msg_m2 = send_confirmation(_r)
                            if ok_m2:
                                _r["mail_sent"] = True
                                st.session_state.nc_result = _r
                                st.success("✅ 確認信已發送")
                                st.rerun()
                            else:
                                st.error(f"確認信發送失敗：{msg_m2}")
                        except Exception as e:
                            st.error(f"確認信發送失敗：{e}")
                else:
                    st.success("✅ 確認信已發送")
                if _r.get("line_message"):
                    col_nc_msg, col_nc_memo = st.columns([3, 1])
                    with col_nc_msg:
                        # v8.17：拿掉固定 key——帶 key 的 st.text_area 一旦畫過一次，
                        # 之後即使傳入新的 value 也不會更新畫面，只會顯示 session_state
                        # 裡的舊內容，導致新訂單成立後 LINE 訊息還停留在上一張訂單。
                        st.text_area("LINE 訊息", _r["line_message"], height=320, label_visibility="collapsed")
                        copy_button("複製 LINE 訊息", _r["line_message"], "copy_nc_line_d")
                    with col_nc_memo:
                        st.text_area("N-J Memo", NJ_MEMO, height=200, label_visibility="collapsed", key="nj_memo_nc_result")
                        copy_button("複製 N-J Memo", NJ_MEMO, "copy-nj-memo-nc-result")


        elif single_feature == "訂單轉換":
            info_panel(
                "功能說明",
                [
                    "將原訂單A拆成多筆新訂單（B1/B2/B3...），每筆各建一張折價券（面額＝該筆含稅金額）。",
                    "原訂單A配班：一般專員優先，不足補檸檬人（混合邏輯）。",
                    "新訂單配班：同上。",
                    "備註自動寫入：A+B1+B2+B3 合併服務。",
                ],
            )

            step("4", "原訂單A")
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                conv_order_no_a = st.text_input("原訂單A編號", placeholder="LC002115551", key="conv_order_no_a")
            with col_a2:
                conv_clean_type = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), key="conv_clean_type")

            st.markdown("<hr>", unsafe_allow_html=True)
            step("4", "新訂單筆數與規格")

            conv_order_count = st.number_input("新訂單筆數", min_value=1, max_value=6, value=2, step=1, key="conv_order_count")
            st.markdown('<div class="hint-box">💡 每筆新訂單各自選日期、時段、人數。時數由時段自動帶出。</div>', unsafe_allow_html=True)

            new_orders_input = []
            for i in range(int(conv_order_count)):
                st.markdown(f"**新訂單 B{i+1}**")
                b1, b2, b3, b4 = st.columns(4)
                with b1:
                    b_date = st.date_input(f"B{i+1} 日期", value=date.today() + timedelta(days=1), key=f"conv_date_{i}")
                with b2:
                    b_period = st.selectbox(f"B{i+1} 時段", PERIOD_OPTIONS, key=f"conv_period_{i}")
                with b3:
                    b_person = st.number_input(f"B{i+1} 人數", min_value=1, max_value=8, value=2, key=f"conv_person_{i}")
                with b4:
                    b_hour = PERIOD_HOUR_MAP.get(b_period, 4)
                    st.markdown(f"<br><b>{b_hour} 小時</b>（依時段帶出）", unsafe_allow_html=True)
                new_orders_input.append({
                    "date_s": b_date.strftime("%Y-%m-%d"),
                    "period_s": b_period,
                    "hour": b_hour,
                    "person": int(b_person),
                })

            st.markdown("<hr>", unsafe_allow_html=True)

            # v8.13：查無班表時是否自動補檸檬人，預設不勾選，需客服明確開啟
            conv_allow_auto_lemon = st.checkbox("查無班表時自動補檸檬人排班", value=False, key="conv_allow_auto_lemon")

            if st.button("🔄 執行訂單轉換", use_container_width=True, key="run_convert_btn"):
                # v8.15：開始新的一次轉換前，先清空上一次殘留的舊結果。
                st.session_state.conv_result = {}
                if not backend_email.strip() or not backend_password.strip():
                    st.error("請先輸入後台帳號密碼")
                elif not conv_order_no_a.strip():
                    st.error("請輸入原訂單A編號")
                else:
                    try:
                        with st.spinner("執行中：查訂單 → 原單配班 → 建折價券 → 建新訂單 → 新單配班…"):
                            conv_result = convert_order_multi(
                                env_name=env,
                                backend_email=backend_email.strip(),
                                backend_password=backend_password.strip(),
                                order_no_a=conv_order_no_a.strip(),
                                new_orders=new_orders_input,
                                clean_type_id=CLEAN_TYPE_ID_MAP[conv_clean_type],
                                allow_auto_lemon_shift=conv_allow_auto_lemon,
                            )
                        st.session_state.conv_result = conv_result
                    except Exception as e:
                        st.session_state.conv_result = {}
                        st.error(f"轉換失敗：{e}")

            conv_result = st.session_state.get("conv_result")
            if conv_result:
                lr_a = conv_result.get("lemon_result_a", {}) or {}
                new_orders_ok = [r for r in conv_result.get("new_order_results", []) if r.get("order_no")]

                # 人時金額計算
                _PERIOD_HOURS_UI = {
                    "09:00-16:00": 6, "09:00-18:00": 8,
                    "08:30-12:30": 4, "09:00-12:00": 3, "09:00-11:00": 2,
                    "14:00-18:00": 4, "14:00-17:00": 3, "14:00-16:00": 2,
                }
                # 人數：優先用 actual_person_count（originShiftId 數量）
                orig_person = int(lr_a.get("actual_person_count", 0) or conv_result.get("person_a_count", 0) or 0)
                # 時數：從 period_a_raw 直接查表
                _period_raw_ui = str(conv_result.get("period_a_raw", "")).strip().replace(" ", "")
                orig_hour = _PERIOD_HOURS_UI.get(_period_raw_ui, 0)
                if not orig_hour:
                    orig_hour = int(conv_result.get("hour_per_person_a", 0) or 0)
                orig_ph = orig_person * orig_hour if orig_person and orig_hour else 0
                try:
                    orig_amount = int(float(str(conv_result.get("service_amount_a_display", 0) or 0)))
                except Exception:
                    orig_amount = 0
                new_ph = sum(int(r.get("person", 0)) * int(r.get("hour", 0)) for r in new_orders_ok)
                new_amount = sum(int(r.get("price_with_tax", 0)) for r in new_orders_ok)
                diff_ph = orig_ph - new_ph if orig_ph else 0
                diff_amt = orig_amount - new_amount if orig_amount else 0
                st.caption(f"debug: orig_person={orig_person} orig_hour={orig_hour} orig_ph={orig_ph} orig_amount={orig_amount} period_a_raw={repr(conv_result.get('period_a_raw'))}")

                # ── 摘要區塊 ─────────────────────────────────────────
                st.markdown("<hr>", unsafe_allow_html=True)
                st.markdown("### 執行結果")

                # 步驟1摘要
                lemon_names = lr_a.get("assigned", [])
                actual_count = int(lr_a.get("actual_person_count", 0) or len(lemon_names) or 0)
                new_svc_date = lr_a.get("new_service_date", "")
                period_a = str(conv_result.get("period_a_raw", "")).replace(" ", "")
                date_ok = lr_a.get("date_change_ok", True)
                orig_date = conv_result.get("service_date_a", "")

                if date_ok and new_svc_date:
                    date_str = f"{orig_date} → {new_svc_date}"
                elif not date_ok:
                    date_str = f"❌ 日期修改失敗，請手動改為 {new_svc_date}"
                else:
                    date_str = orig_date

                if lr_a.get("success") and lemon_names:
                    lemon_str = "X".join(lemon_names)
                    step1_line = f"✅ 步驟1：原訂單 {conv_result['order_no_a']} 服務日期 {date_str} {period_a}，{lemon_str}，{actual_count}人×{orig_hour}小時"
                    st.success(step1_line)
                else:
                    st.warning(f"⚠️ 步驟1：原訂單配班未完成 — {lr_a.get('message', '未知')}")

                # 步驟2摘要
                for r in new_orders_ok:
                    ph_str = f"{r['person']}人{r['hour']}小時"
                    st.success(f"✅ 步驟2：新訂單 {r['order_no']}，{r['date_s']} {r['period_s']} {ph_str}，折價券 {r['coupon_code']}（{r['price_with_tax']}元）")
                    _r_order_result = r.get("order_result") or {}
                    if _r_order_result.get("order_no_duplicated"):
                        show_duplicate_order_warning(
                            r.get("order_no"), _r_order_result.get("order_no_duplicate_count", 2),
                            dedup_key=f"conv_{r.get('order_no')}",
                        )
                for r in [r for r in conv_result.get("new_order_results", []) if r.get("error")]:
                    st.error(f"❌ 步驟2 B{r['index']}（{r['date_s']} {r['period_s']}）失敗：{r['error']}")

                # 步驟3摘要（只要原訂單人時或金額任一有值就顯示）
                new_ph_detail = "＋".join(f"{r['person']}人{r['hour']}小時" for r in new_orders_ok) if new_orders_ok else "（無）"
                new_amt_detail = "＋".join(f"{r['price_with_tax']}元" for r in new_orders_ok) if new_orders_ok else "0元"

                if orig_ph or orig_amount:
                    orig_ph_str = f"{orig_person}人x{orig_hour}小時共{orig_ph}人時" if orig_ph else f"{orig_person}人x未知小時（無法計算人時）"
                    orig_amt_str = f"{orig_amount}元" if orig_amount else "金額未知"
                    step3_orig = f"原訂單：{orig_ph_str}，{orig_amt_str}"
                    step3_new = f"新訂單：{new_ph_detail} 共{new_ph}人時，{new_amt_detail} = {new_amount}元"

                    if orig_ph and orig_amount and diff_ph == 0 and diff_amt == 0:
                        st.success(f"步驟3：{step3_orig} ＝ {step3_new}")
                    else:
                        diff_parts = []
                        if orig_ph and diff_ph != 0:
                            diff_parts.append(f"{abs(diff_ph)}人時")
                        if orig_amount and diff_amt != 0:
                            diff_parts.append(f"{abs(diff_amt)}元")
                        diff_str = f"共差 {'、'.join(diff_parts)}" if diff_parts else ""
                        full_msg = "步驟3：\n\n" + step3_orig + "\n\n" + step3_new + "\n\n" + diff_str + "，請確認是否需要補建新訂單。"
                        st.warning(full_msg)
                else:
                    full_msg = "步驟3：原訂單人數/時段/金額解析失敗，無法比較。\n\n新訂單：" + new_ph_detail + f" 共{new_ph}人時，" + new_amt_detail + f" = {new_amount}元\n\n請手動核對原訂單金額是否與新訂單合計相符。"
                    st.warning(full_msg)

                # ── 細項 ────────────────────────────────────────────
                with st.expander("🔍 細項", expanded=False):
                    st.markdown(f"[🔗 開啟原訂單A後台]({conv_result['purchase_url_a']})")

                    st.markdown("**步驟2 新訂單 LINE 訊息**")
                    for r in new_orders_ok:
                        if r.get("line_message"):
                            # v8.17：拿掉固定 key，避免新一輪轉換的 LINE 訊息被舊內容卡住不更新。
                            st.text_area(f"B{r['index']} LINE（{r['order_no']}）", r["line_message"], height=300, label_visibility="collapsed")
                            copy_button(f"複製 B{r['index']} LINE 訊息", r["line_message"], f"copy_conv_line_{r['index']}")

                    combined_msg = conv_result.get("combined_line_message", "")
                    if combined_msg:
                        st.markdown("**💬 合併 LINE 訊息（全部新訂單）**")
                        st.text_area("合併 LINE 訊息", combined_msg, height=380, label_visibility="collapsed")
                        copy_button("複製合併 LINE 訊息", combined_msg, "copy_conv_combined_line")

                    st.markdown("**備註文字**")
                    note_a_status = "✅ 已自動寫入" if conv_result.get("note_a_ok") else f"⚠️ 需手動貼上（{conv_result.get('note_a_msg', '')}）"
                    st.markdown(f"原訂單A備註 {note_a_status}")
                    st.text_area("原訂單A備註", conv_result.get("note_a", ""), height=70, label_visibility="collapsed", key="conv_note_a_out")
                    copy_button("複製原訂單A備註", conv_result.get("note_a", ""), "copy_note_a")
                    st.caption(f"全單備註：{conv_result.get('note', '')}")
        # --------------------------------------------------
        # 儲值金補價差
        # --------------------------------------------------
        elif single_feature == "儲值金補價差":
            info_panel("流程說明", [
                "此功能拆成兩段：先成立儲值金折抵單，再成立客付補價差訂單。",
                "日期類型由服務日期自動判斷：週一到週五為平日，週六日為週末。",
                "儲值金清零單走 /booking/stored_value_routine，優惠券A = 服務總額 - 儲值金餘額；剩餘額用儲值金扣掉後歸零。",
                "補差價訂單走 /booking/single，優惠券B = 原儲值金餘額，付款方式限 ATM / 信用卡。",
            ])
            step("4", "客人與服務資料")
            sv1, sv2, sv3 = st.columns(3)
            with sv1:
                sv_phone = st.text_input("客人手機號碼", key="sv_auto_phone")
            with sv2:
                sv_ctype = st.selectbox("服務類別", list(CLEAN_TYPE_ID_MAP.keys()), key="sv_auto_ctype")
            with sv3:
                sv_svc_date = st.date_input("服務日期", value=date.today() + timedelta(days=7), key="sv_auto_date")
                sv_day_type_auto = "週末" if sv_svc_date.weekday() >= 5 else "平日"
                st.caption(f"日期類型：{sv_day_type_auto}（自動判斷）")
            sd1, sd2, sd3, sd4 = st.columns(4)
            with sd1:
                sv_svc_period = st.selectbox("服務時段", PERIOD_OPTIONS, key="sv_auto_period")
            with sd2:
                sv_svc_person = st.number_input("人數", min_value=1, max_value=8, value=2, key="sv_auto_person")
            with sd3:
                sv_svc_hour = PERIOD_HOUR_MAP.get(sv_svc_period, 4)
                sv_person_hours = int(sv_svc_person) * int(sv_svc_hour)
                st.markdown(f"<br><b>{sv_svc_hour} 小時</b><br><span style='color:#8E8E93;font-size:13px;'>人時：{sv_person_hours}</span>", unsafe_allow_html=True)
            with sd4:
                sv_unit_price = 700 if sv_day_type_auto == "週末" else 600
                st.markdown(f"<br><b>{sv_unit_price} 元 / 人時</b><br><span style='color:#8E8E93;font-size:13px;'>儲值金單目標金額：{sv_unit_price * sv_person_hours}</span>", unsafe_allow_html=True)
            st.markdown("<hr>", unsafe_allow_html=True)
            step("4", "客付訂單付款與發票")
            pay1, pay2 = st.columns(2)
            with pay1:
                sv_customer_payway = st.selectbox("付款方式", ["ATM", "信用卡"], key="sv_auto_customer_payway")
            with pay2:
                sv_invoice_mode = st.selectbox("發票", ["會員載具", "手機載具", "三聯式"], key="sv_auto_invoice_mode")
            sv_mobile_carrier = ""
            sv_company_title = ""
            sv_company_no = ""
            if sv_invoice_mode == "手機載具":
                sv_mobile_carrier = st.text_input("手機條碼", placeholder="例：/ABC1234", key="sv_auto_mobile_carrier")
            elif sv_invoice_mode == "三聯式":
                inv_a, inv_b = st.columns(2)
                with inv_a:
                    sv_company_title = st.text_input("發票抬頭", key="sv_auto_company_title")
                with inv_b:
                    sv_company_no = st.text_input("統一編號", key="sv_auto_company_no")
            else:
                st.caption("二聯會員載具會使用會員 email。")
            st.markdown("<hr>", unsafe_allow_html=True)
            step("4", "選填設定")
            opt1, opt2 = st.columns(2)
            with opt1:
                sv_address = st.text_input("指定服務地址（留空則用會員第一個地址）", key="sv_auto_address")
            with opt2:
                sv_region = st.selectbox("適用地區", [""] + list(COUPON_COMPANY_ID_MAP.keys()), format_func=lambda x: x or "依地址自動判斷", key="sv_auto_region")
            st.markdown("<hr>", unsafe_allow_html=True)
            step("5", "第一段：建立儲值金清零訂單")
            sv_stored_total_preview = sv_unit_price * sv_person_hours
            st.markdown(f'<div class="hint-box">儲值金清零訂單會送到 <b>/booking/stored_value_routine</b>。服務總額為 <b>{sv_unit_price} × {sv_person_hours} = {sv_stored_total_preview}</b>；優惠券A會用「服務總額 - 儲值金餘額」計算，剩餘金額由儲值金扣抵後歸零。</div>', unsafe_allow_html=True)
            if st.button("① 建立儲值金清零訂單（stored_value_routine）", use_container_width=True, key="sv_create_stored_btn"):
                # v8.15：開始新的一次嘗試前，先清空上一次殘留的舊結果（含第二段）。
                st.session_state.sv_stored_stage = {}
                st.session_state.sv_paid_stage = {}
                if not backend_email.strip() or not backend_password.strip():
                    st.error("請先輸入後台帳號密碼")
                elif not sv_phone.strip():
                    st.error("請輸入客人手機號碼")
                else:
                    try:
                        with st.spinner("第一段執行中：查儲值金 → 建優惠券A → 建儲值金清零訂單 → 換檸檬人…"):
                            stored_stage = stored_value_makeup_create_stored_order(
                                env_name=env, backend_email=backend_email.strip(), backend_password=backend_password.strip(),
                                phone=sv_phone.strip(), clean_type_id=CLEAN_TYPE_ID_MAP[sv_ctype],
                                service_date=sv_svc_date.strftime("%Y-%m-%d"), period_s=sv_svc_period,
                                hour=str(sv_svc_hour), person=str(int(sv_svc_person)),
                                address=sv_address.strip(), region=sv_region, coupon_prefix_base=sv_phone.strip(),
                            )
                        st.session_state.sv_stored_stage = stored_stage
                        st.session_state.sv_paid_stage = {}
                    except Exception as e:
                        st.error(f"第一段建立失敗：{e}")
            stored_stage = st.session_state.get("sv_stored_stage")
            if stored_stage:
                plan = stored_stage["plan"]
                so = stored_stage["stored_order"]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("儲值金餘額", f"{stored_stage['balance']} 元")
                c2.metric("日期類型", stored_stage.get("day_type", sv_day_type_auto))
                c3.metric("優惠券A", f"{plan['coupon_a']} 元")
                st.caption(f"計算式：{plan['dummy_price']} - {stored_stage['balance']} = {plan['coupon_a']}；剩餘 {plan.get('stored_value_applied', stored_stage['balance'])} 扣儲值金。")
                c4.metric("儲值金單", so.get("order_no", "—"))
                ca = stored_stage.get("coupon_a", {})
                st.success(f"✅ 第一段完成：儲值金清零訂單 {so.get('order_no', '—')}；優惠券A {ca.get('coupon_code') or ca.get('coupon_prefix')}，面額 {plan['coupon_a']} 元。")
                if so.get("order_no_duplicated"):
                    show_duplicate_order_warning(so.get("order_no"), so.get("order_no_duplicate_count", 2), dedup_key=f"sv_stored_{so.get('order_no')}")
                lemon_r = stored_stage.get("lemon_result", {})
                if lemon_r.get("success"):
                    st.success(lemon_r.get("message", "已改為檸檬人"))
                else:
                    st.warning(lemon_r.get("message", "檸檬人配班未完成，請手動確認"))
                step("6", "第二段：建立客付補價差訂單")
                st.markdown(f'<div class="hint-box">客付補價差單會建立優惠券B，面額為原儲值金餘額 <b>{stored_stage["balance"]}</b> 元，付款方式為 <b>{sv_customer_payway}</b>。</div>', unsafe_allow_html=True)
                if st.button("② 建立客付補價差訂單（single）", use_container_width=True, key="sv_create_paid_btn"):
                    # v8.15：開始新的一次嘗試前，先清空上一次殘留的舊結果。
                    st.session_state.sv_paid_stage = {}
                    try:
                        with st.spinner("第二段執行中：建優惠券B → 建客付補價差訂單…"):
                            paid_stage = stored_value_makeup_create_paid_order(
                                env_name=env, backend_email=backend_email.strip(), backend_password=backend_password.strip(),
                                phone=stored_stage.get("phone") or sv_phone.strip(),
                                clean_type_id=stored_stage.get("clean_type_id") or CLEAN_TYPE_ID_MAP[sv_ctype],
                                service_date=stored_stage.get("service_date") or sv_svc_date.strftime("%Y-%m-%d"),
                                period_s=stored_stage.get("period_s") or sv_svc_period,
                                hour=stored_stage.get("hour") or str(sv_svc_hour),
                                person=stored_stage.get("person") or str(int(sv_svc_person)),
                                customer_payway=sv_customer_payway, invoice_mode=sv_invoice_mode,
                                mobile_carrier=sv_mobile_carrier, company_title=sv_company_title, company_no=sv_company_no,
                                address=stored_stage.get("address") or sv_address.strip(),
                                region=stored_stage.get("region") or sv_region,
                                coupon_prefix_base=stored_stage.get("coupon_prefix_base") or sv_phone.strip(),
                                stored_order_no=stored_stage.get("stored_order", {}).get("order_no", ""),
                                balance_override=stored_stage.get("balance"),
                            )
                        st.session_state.sv_paid_stage = paid_stage
                    except Exception as e:
                        st.error(f"第二段建立失敗：{e}")
            paid_stage = st.session_state.get("sv_paid_stage")
            if paid_stage:
                po = paid_stage["paid_order"]
                cb = paid_stage.get("coupon_b", {})
                st.success(f"✅ 第二段完成：客付補價差訂單 {po.get('order_no', '—')}；優惠券B {cb.get('coupon_code') or cb.get('coupon_prefix')}。")
                if po.get("order_no_duplicated"):
                    show_duplicate_order_warning(po.get("order_no"), po.get("order_no_duplicate_count", 2), dedup_key=f"sv_paid_{po.get('order_no')}")
                st.markdown("#### 📋 備註文字")
                combined_note = ""
                if stored_stage:
                    combined_note += stored_stage.get("note", "")
                combined_note += "\n" + paid_stage.get("note", "")
                st.text_area("備註", combined_note.strip(), height=110, label_visibility="collapsed")
                copy_button("複製備註", combined_note.strip(), "copy_sv_stage_note")
                if paid_stage.get("line_message"):
                    st.markdown("#### 💬 客付訂單 LINE 訊息")
                    st.text_area("LINE 訊息", paid_stage["line_message"], height=320, label_visibility="collapsed")
                    copy_button("複製 LINE 訊息", paid_stage["line_message"], "copy_sv_paid_line")

        # --------------------------------------------------
        # 儲值金購買（v8.21 新增）
        # 客人自己買/儲值一筆金額，付款方式/發票不用手動選，自動沿用會員最近一次
        # VIP 或儲值金購買訂單的設定；都找不到才退回最近一次一般服務訂單。
        # --------------------------------------------------
        elif single_feature == "儲值金購買":
            info_panel("流程說明", [
                "地區＝登入帳號本身所屬地區（例如用台北帳號登入，就會建在台北）。",
                "付款方式與發票不用手動選：自動抓這支電話最近一次 VIP 或儲值金購買訂單的設定，"
                "都找不到才退回抓最近一次一般服務訂單的設定，兩者都沒有則不會自動送出，"
                "改請你人工確認後至後台手動建立。",
            ])
            sv2_col1, sv2_col2 = st.columns(2)
            with sv2_col1:
                sv2_phone = st.text_input("客人手機號碼", key="sv2_phone")
            with sv2_col2:
                sv2_region = st.selectbox("地區（登入帳號所屬地區）", ["台北", "桃園", "新竹", "台中"], key="sv2_region")

            sv2_amount_labels = {
                "儲值金20,000（贈購物金800）": 20000,
                "儲值金50,000（贈購物金2,500）": 50000,
                "儲值金9,900（無贈送）": 9900,
                "儲值金17,000（無贈送）": 17000,
                "儲值金18,900（無贈送）": 18900,
                "儲值金19,400（無贈送）": 19400,
                "儲值金36,000（無贈送）": 36000,
            }
            sv2_amount_label = st.selectbox("儲值金金額", list(sv2_amount_labels.keys()), key="sv2_amount_label")
            sv2_amount = sv2_amount_labels[sv2_amount_label]
            sv2_notice = st.text_area("備註（選填）", height=80, key="sv2_notice")

            if st.button("🚀 建立儲值金購買訂單", use_container_width=True, key="sv2_create_btn", type="primary"):
                # v8.21：開始新的一次嘗試前，先清空上一次殘留的舊結果。
                st.session_state.sv2_result = {}
                if not backend_email.strip() or not backend_password.strip():
                    st.error("請先輸入後台帳號密碼")
                elif not sv2_phone.strip():
                    st.error("請輸入客人手機號碼")
                else:
                    try:
                        with st.spinner("查詢會員 → 判斷付款方式/發票 → 建立儲值金訂單…"):
                            sv2_result = qo.create_stored_value_purchase_order(
                                env_name=env,
                                backend_email=backend_email.strip(),
                                backend_password=backend_password.strip(),
                                phone=sv2_phone.strip(),
                                stored_value_amount=sv2_amount,
                                region=sv2_region,
                                notice=sv2_notice.strip(),
                            )
                        st.session_state.sv2_result = sv2_result
                    except Exception as e:
                        st.error(f"建立失敗：{e}")

            sv2_result = st.session_state.get("sv2_result") or {}
            if sv2_result:
                if sv2_result.get("need_manual_confirm"):
                    st.warning(f"⚠️ {sv2_result.get('message', '')}")
                    _dbg = sv2_result.get("search_debug") or {}
                    with st.expander("🔍 查詢明細（點開看實際查到什麼，不用再用猜的）", expanded=True):
                        if _dbg.get("error"):
                            st.error(f"查詢時發生例外：{_dbg['error']}")
                        _queries_dbg = _dbg.get("queries") or []
                        if _queries_dbg:
                            st.table([
                                {
                                    "查詢類別": q.get("label", ""),
                                    "HTTP狀態碼": q.get("http_status", q.get("error", "")),
                                    "查到筆數（已付款）": q.get("count", 0),
                                    "訂單編號": "、".join(q.get("order_nos", [])) or "（無）",
                                }
                                for q in _queries_dbg
                            ])
                            _skipped_dbg = _dbg.get("skipped_stored_value_payway") or []
                            if _skipped_dbg:
                                st.caption("以下訂單因為付款方式是「儲值金」（既有餘額折抵消費，非真正付款）已跳過：")
                                st.table([{"類別": s.get("label", ""), "訂單編號": s.get("order_no", "")} for s in _skipped_dbg])
                            st.caption(
                                "依序查詢「儲值金」→「VIP券」→「專業清潔」三個類別（都只查已付款的訂單），"
                                "查到就直接用該類別裡最新一筆「付款方式是信用卡/ATM」的訂單，不會混在一起比日期，"
                                "也不會用付款方式是儲值金的訂單當範本。如果上表看起來明明有查到訂單、"
                                "卻還是被判定查無資料，麻煩把這個表格截圖給開發人員確認。"
                            )
                        else:
                            st.info("查詢過程沒有回傳任何結果，請檢查上方是否有例外訊息。")
                elif sv2_result.get("success"):
                    st.success(
                        f"✅ 訂單：{sv2_result.get('order_no') or '（已送出，但查不到訂單編號，請至後台確認）'}　"
                        f"儲值金額：{sv2_result.get('stored_value_amount')}元　"
                        f"贈購物金：{sv2_result.get('bonus')}元　"
                        f"付款方式：{sv2_result.get('payway')}"
                    )
                    _invoice_type_label = {"1": "捐贈發票", "2": "二聯式", "3": "三聯式"}.get(sv2_result.get("invoice_type", ""), "")
                    if _invoice_type_label:
                        st.caption(
                            f"發票設定沿用自會員歷史訂單：{_invoice_type_label}"
                            + (f"（{sv2_result.get('company_title')} / {sv2_result.get('company_no')}）" if sv2_result.get("invoice_type") == "3" else "")
                            + (f"（{sv2_result.get('carrier_info')}）" if sv2_result.get("invoice_type") == "2" and sv2_result.get("carrier_info") else "")
                        )
                    # v8.23：跟其他成單流程一致，訂單建立後不自動發確認信，
                    # 由客服確認資料無誤後手動按下「發送確認信」再送出。
                    if sv2_result.get("order_no"):
                        if not sv2_result.get("mail_sent"):
                            if st.button("📧 發送確認信", key="sv2_send_mail_btn", type="primary"):
                                try:
                                    ok_m, msg_m = send_confirmation(sv2_result)
                                    if ok_m:
                                        sv2_result["mail_sent"] = True
                                        st.session_state.sv2_result = sv2_result
                                        st.success("✅ 確認信已發送")
                                        st.rerun()
                                    else:
                                        st.error(f"確認信發送失敗：{msg_m}")
                                except Exception as e:
                                    st.error(f"確認信發送失敗：{e}")
                        else:
                            st.success("✅ 確認信已發送")
                    else:
                        st.info("查不到訂單編號，無法自動發送確認信，請至後台確認訂單後手動發送。")
                    if sv2_result.get("line_message"):
                        st.markdown("#### 💬 LINE 訊息")
                        st.text_area("LINE 訊息", sv2_result["line_message"], height=380, label_visibility="collapsed")
                        copy_button("複製 LINE 訊息", sv2_result["line_message"], "copy_sv2_line")
                    else:
                        st.info("此地區/付款方式組合目前沒有現成 LINE 通知文案，請至「LINE 通知產生器」用訂單編號查詢並人工確認內容。")
                else:
                    st.error(f"❌ 建立失敗：{sv2_result.get('message', '未知錯誤')}")

        # --------------------------------------------------
        # 舊客快速建單：建單後結果顯示
        # v8.8：限定只在「舊客快速建單」分頁顯示，避免切到其他分頁後，
        # session_state 裡殘留的舊訂單結果（q_order_result）還黏在畫面下方，
        # 跟當前分頁剛建立的訂單（例如「新客資料拆解」的 nc_result）混在一起顯示。
        # --------------------------------------------------
        order_result = st.session_state.get("q_order_result") if single_feature == "舊客快速建單" else None
        if order_result:
            st.markdown("<hr>", unsafe_allow_html=True)
            step("5", "執行結果")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("訂單編號", order_result["order_no"])
            c2.metric("金額（含稅）", order_result.get("service_amount") or order_result.get("price_with_tax") or "—")
            c3.metric("車馬費", order_result.get("fare") or "0")
            c4.metric("確認信", "已發送" if order_result.get("mail_sent") else "未發送")
            st.success(f"✅ 訂單建立成功：{order_result['order_no']}")
            if order_result.get("price_mismatch_warning"):
                st.warning(order_result["price_mismatch_warning"])
            if order_result.get("address_mismatch_warning"):
                st.warning(order_result["address_mismatch_warning"])
            if order_result.get("order_no_duplicated"):
                show_duplicate_order_warning(order_result.get("order_no"), order_result.get("order_no_duplicate_count", 2), dedup_key=f"old_{order_result.get('order_no')}")
            if not order_result.get("mail_sent"):
                if st.button("📧 發送確認信", key="send_mail_btn", type="primary"):
                    try:
                        ok_m, msg_m = send_confirmation(order_result)
                        if ok_m:
                            order_result["mail_sent"] = True
                            st.session_state.q_order_result = order_result
                            st.success("✅ 確認信已發送")
                            st.rerun()
                        else:
                            st.error(f"確認信發送失敗：{msg_m}")
                    except Exception as e:
                        st.error(f"確認信發送失敗：{e}")
            else:
                st.success("✅ 確認信已發送")
            line_message = build_line_message(order_result)
            col_msg, col_memo = st.columns([3, 1])
            with col_msg:
                st.text_area("LINE 訊息內容", line_message, height=420, label_visibility="collapsed")
                copy_button("複製 LINE 訊息", line_message, "copy-line-message")
            with col_memo:
                st.text_area("N-J Memo", NJ_MEMO, height=200, label_visibility="collapsed", key="nj_memo_order_result")
                copy_button("複製 N-J Memo", NJ_MEMO, "copy-nj-memo-order-result")

