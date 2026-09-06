import pandas as pd

from tools.lemon_backend.members import members_from_dataframe, normalize_member_phone


def test_member_export_deduplicates_purchase_rows_and_preserves_contact_fields():
    frame = pd.DataFrame([
        {
            "mv_id": 7,
            "客戶姓名": "王小明",
            "email": "vip@example.com",
            "電話": 912345678,
            "地址": "台北市測試路1號",
            "LINE@": "https://chat.line.biz/example",
            "剩餘儲值金": 500,
            "會員等級": "黃金VIP會員",
        },
        {
            "mv_id": 7,
            "客戶姓名": "王小明",
            "email": "vip@example.com",
            "電話": 912345678,
            "地址": "台北市測試路1號",
            "LINE@": "https://chat.line.biz/example",
            "剩餘儲值金": 800,
            "會員等級": "黃金VIP會員",
        },
    ])

    members = members_from_dataframe(frame, "台北")

    assert len(members) == 1
    assert members[0]["phone"] == "0912345678"
    assert members[0]["stored_value"] == 800
    assert normalize_member_phone("0912-345-678") == "0912345678"
