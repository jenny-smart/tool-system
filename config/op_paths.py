from pathlib import Path

# =========================================================
# 環境判斷
# =========================================================
HOME = Path.home()

LOCAL_GOOGLE_DRIVE = Path(
    "/Users/jenny/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟"
)

LOCAL_SCHEDULE_SHORTCUT = Path(
    "/Users/jenny/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/.shortcut-targets-by-id/1zbu45AG1adMzz24HPdi_tLfh2Tncw_Br/排班統計表"
)

IS_LOCAL_MAC = str(HOME).startswith("/Users/") and LOCAL_GOOGLE_DRIVE.exists()

# 雲端可寫暫存根目錄
CLOUD_BASE = Path("/tmp/lemon_data")

# =========================================================
# 路徑設定
# =========================================================
if IS_LOCAL_MAC:
    BASE_GOOGLE_DRIVE = LOCAL_GOOGLE_DRIVE

    # Jenny 個人輸出
    PATH_JENNY = BASE_GOOGLE_DRIVE / "lemon_Jenny" / "Jenny@lemon程式"

    # 排班統計表
    PATH_SCHEDULE = LOCAL_SCHEDULE_SHORTCUT

    # 其他輸出資料夾
    PATH_CLEANER_SCHEDULE = PATH_JENNY / "專員班表"
    PATH_CLEANER_DATA = PATH_JENNY / "專員系統個資"
    PATH_ORDER = PATH_JENNY / "訂單資料"

    # 業績報表
    PATH_REPORT = PATH_JENNY / "業績報表"

    # 財務
    PATH_VIP = BASE_GOOGLE_DRIVE / "lemon_財務" / "02.VIP儲值金"

    # 人事
    PATH_HR = BASE_GOOGLE_DRIVE / "lemon_人事" / "03 服務分潤表"

else:
    # 雲端 / Streamlit Cloud
    BASE_GOOGLE_DRIVE = CLOUD_BASE / "google_drive_mock"

    PATH_JENNY = CLOUD_BASE / "Jenny"
    PATH_SCHEDULE = CLOUD_BASE / "排班統計表"
    PATH_CLEANER_SCHEDULE = CLOUD_BASE / "專員班表"
    PATH_CLEANER_DATA = CLOUD_BASE / "專員系統個資"
    PATH_ORDER = CLOUD_BASE / "訂單資料"
    PATH_REPORT = CLOUD_BASE / "業績報表"
    PATH_VIP = CLOUD_BASE / "VIP儲值金"
    PATH_HR = CLOUD_BASE / "服務分潤表"

# =========================================================
# 建立目前環境可用資料夾
# =========================================================
PATHS_TO_CREATE = [
    PATH_JENNY,
    PATH_SCHEDULE,
    PATH_CLEANER_SCHEDULE,
    PATH_CLEANER_DATA,
    PATH_ORDER,
    PATH_REPORT,
    PATH_VIP,
    PATH_HR,
]

for p in PATHS_TO_CREATE:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# =========================================================
# 共用設定
# =========================================================
API_LIMIT = 10000
