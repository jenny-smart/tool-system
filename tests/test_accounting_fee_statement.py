from tools.finance_management import accounting_fee_statement as st


def test_statement_lines_like_september():
    items = [
        {"memo": "2026.07～08-台北營業稅", "amount": "277986"},
        {"memo": "2026.07～08-台中營業稅", "amount": "19204"},
        {"memo": "2026.09-辦公室租金代扣繳", "amount": "8800"},
        {"memo": "2026.10-辦公室租金代扣繳", "amount": "8800"},
        {"memo": "2026.09-辦公室二代健保", "amount": "1857"},
        {"memo": "2026.10-辦公室二代健保", "amount": "1857"},
    ]
    assert st.statement_lines(items, "2026.09") == [
        ("2026.07-台北營業稅", "稅捐", 138993),
        ("2026.08-台北營業稅", "稅捐", 138993),
        ("2026.08-台中營業稅", "稅捐", 19204),
        ("2026.09-辦公室代墊", "辦公室租金費", 8800),
        ("2026.10-辦公室代墊", "辦公室租金費", 8800),
        ("2026.09-辦公室補充保費", "辦公室租金費", 1857),
        ("2026.10-辦公室補充保費", "辦公室租金費", 1857),
    ]


def test_statement_lines_odd_split_and_others():
    lines = st.statement_lines([{"memo": "2026.09～10-台北營業稅", "amount": 101},
                                {"memo": "2026-會計師簽證費", "amount": 60000},
                                {"memo": "2026.05-營所稅", "amount": 5}], "2026.11")
    assert lines == [("2026.09-台北營業稅", "稅捐", 50), ("2026.10-台北營業稅", "稅捐", 51),
                     ("2026.11-會計師簽證費", "", 60000), ("2026.05-營所稅", "稅捐", 5)]


def test_find_statement_row_with_fee():
    header = ["序", "交易日", "帳務日", "說明", "支出"]
    values = [header,
              ["", "2026/11/09 15:46:27", "2026/11/09", "網路跨轉", "318,519", "", "", "", "稅捐"],
              ["", "2026/11/09 15:46:27", "2026/11/09", "網路跨轉", "318,519"],
              ["", "2026/11/08 10:00:00", "2026/11/08", "網路跨轉", "318,504"]]
    assert st.find_statement_row(values, 318504, "2026/11/09") == (3, 318519)
    assert st.find_statement_row(values, 1, "2026/11/09") is None


def test_build_rows_rewrites_formulas():
    original = ["", 46000.6, 46000, "網路跨轉", 318519, "", 4494242, "0708營業稅代扣", "", "",
                '=TEXT(LEFT(C120,10),"yyyy/mm/dd")', '=if(E120<>0,1,2)', "=E120", "=F120", "=G120", "=H120"]
    rows = st.build_rows(original, 120, [("a", "稅捐", 1), ("b", "", 2)], "now")
    assert rows[1][10] == '=TEXT(LEFT(C121,10),"yyyy/mm/dd")' and rows[1][12] == "=E121"
    assert rows[0][4] == 1 and rows[0][8] == "稅捐" and rows[0][11] == "a" and rows[1][11] == "b"
    assert rows[1][16] == "now" and rows[0][6] == 4494242


def test_taichung_statement_lines_and_should_split():
    items = [{"memo": "2026.07～08-台中營業稅", "amount": 19204}]
    assert st.statement_lines(items, "2026.09", "台中") == [("2026.07-營業稅-台中", "稅捐", 9602),
                                                          ("2026.08-營業稅-台中", "稅捐", 9602)]
    assert st.should_split("台中", "檸檬台北", items)
    assert not st.should_split("台中", "檸檬台北", items + [{"memo": "2026.09-台中房租", "amount": 1}])
    assert st.should_split("台北", "勤信會計事務所", items)
    assert not st.should_split("台北", "檸檬台北", items)
