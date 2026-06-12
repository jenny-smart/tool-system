import streamlit as st
import imaplib
import email
from email.header import decode_header
import re
import os
import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from google.oauth2 import service_account


# ────────────────────────────────────────────────
# 設定載入
# ────────────────────────────────────────────────

def load_config():
    """從 systems.yaml 讀取 gmail_401 設定，
    同時支援直接從 st.secrets 讀取（Streamlit Cloud）"""
    try:
        import yaml
        with open("systems.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)["gmail_401"]
    except Exception:
        # fallback：從 secrets 直接組出 config
        return st.secrets.get("gmail_401", {})


# ────────────────────────────────────────────────
# Google Drive
# ────────────────────────────────────────────────

def get_drive_service():
    sa_info = st.secrets["GOOGLE_SERVICE_ACCOUNT"]
    if isinstance(sa_info, str):
        import json
        sa_info = json.loads(sa_info)
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def get_or_create_year_folder(drive, parent_id, year_str):
    """在父資料夾下找或建立西元年子資料夾"""
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{year_str}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    res = drive.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    # 建立
    meta = {
        "name": year_str,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = drive.files().create(
        body=meta,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return folder["id"]


def upload_to_drive(drive, folder_id, filename, data):
    """上傳 PDF 到指定資料夾，若同名檔案已存在則略過"""
    # 檢查是否已存在
    query = (
        f"'{folder_id}' in parents and "
        f"name = '{filename}' and "
        f"trashed = false"
    )
    res = drive.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    if res.get("files"):
        return "already_exists"

    file_meta = {
        "name": filename,
        "parents": [folder_id]
    }
    media = MediaInMemoryUpload(data, mimetype="application/pdf", resumable=False)
    drive.files().create(
        body=file_meta,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return "uploaded"


# ────────────────────────────────────────────────
# 工具函式
# ────────────────────────────────────────────────

def parse_roc_year(filename):
    """從檔名抓民國年（3位數），轉為西元年字串
    例：檸檬專業(檸檬台北)115.03-04 401.pdf → '2026'
    """
    match = re.search(r'(\d{3})\.\d{2}', filename)
    if match:
        roc = int(match.group(1))
        return str(roc + 1911)
    return None


def match_region(filename, regions):
    """根據檔名關鍵字判斷地區，回傳 (地區名, folder_id)"""
    for region_name, region_info in regions.items():
        for kw in region_info.get("keywords", []):
            if kw in filename:
                return region_name, region_info.get("folder_id", "")
    return None, None


def decode_filename(part):
    """解碼 MIME 附件檔名（支援中文）"""
    raw = part.get_filename()
    if not raw:
        return None
    decoded_parts = decode_header(raw)
    filename = ""
    for part_bytes, charset in decoded_parts:
        if isinstance(part_bytes, bytes):
            filename += part_bytes.decode(charset or "utf-8", errors="replace")
        else:
            filename += part_bytes
    return filename


# ────────────────────────────────────────────────
# Gmail IMAP
# ────────────────────────────────────────────────

def fetch_unprocessed_mails(gmail_user, gmail_password, search_query):
    """用 IMAP 搜尋未加標籤的信件，回傳 list of (uid, msg)"""
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_user, gmail_password)
    mail.select("inbox")

    # 搜尋含附件且未標記已處理的信件
    status, data = mail.uid("search", None, search_query.encode())
    if status != "OK" or not data[0]:
        mail.logout()
        return [], mail

    uids = data[0].split()
    messages = []
    for uid in uids:
        status, msg_data = mail.uid("fetch", uid, "(RFC822)")
        if status == "OK":
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            messages.append((uid, msg))

    return messages, mail


def mark_as_processed(mail, uid, label="401已歸檔"):
    """在 Gmail 加上標籤（需先建立標籤）"""
    # Gmail IMAP 用 COPY 到對應標籤資料夾的方式貼標籤
    # 標準做法：STORE +FLAGS (\Flagged) 或用 X-GM-LABELS
    try:
        mail.uid("store", uid, "+X-GM-LABELS", f"({label})")
    except Exception:
        # 若標籤不存在會失敗，改用已讀標記作為 fallback
        mail.uid("store", uid, "+FLAGS", r"(\Seen)")


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────

def run_gmail_401():
    st.subheader("📎 Gmail 401附件自動歸檔")

    config = load_config()
    regions = config.get("regions", {})
    search_query = config.get("search_query", 'HAS ATTACHMENT SUBJECT "401"')

    gmail_user = st.secrets.get("GMAIL_401_USER", "")
    gmail_password = st.secrets.get("GMAIL_401_APP_PASSWORD", "")

    if not gmail_user or not gmail_password:
        st.error("請先在 Streamlit secrets 設定 GMAIL_401_USER 和 GMAIL_401_APP_PASSWORD")
        return

    # 顯示設定摘要
    with st.expander("目前設定"):
        st.write(f"**信箱：** {gmail_user}")
        for r, info in regions.items():
            folder_id = info.get("folder_id", "")
            kws = "、".join(info.get("keywords", []))
            status = "✅" if folder_id else "⚠️ 未設定folder_id"
            st.write(f"**{r}** {status}｜關鍵字：{kws}")

    if st.button("🚀 開始掃描並歸檔", type="primary"):
        with st.spinner("連線 Gmail 中..."):
            try:
                messages, mail = fetch_unprocessed_mails(
                    gmail_user, gmail_password, search_query
                )
            except Exception as e:
                st.error(f"Gmail 連線失敗：{e}")
                return

        if not messages:
            st.info("沒有找到待處理的信件。")
            mail.logout()
            return

        st.write(f"找到 **{len(messages)}** 封待處理信件，開始處理...")

        drive = get_drive_service()
        success, skipped, error = 0, 0, 0
        log_lines = []

        progress = st.progress(0)
        for i, (uid, msg) in enumerate(messages):
            progress.progress((i + 1) / len(messages))

            subject = decode_header(msg.get("Subject", ""))[0]
            subject_str = subject[0].decode(subject[1] or "utf-8") if isinstance(subject[0], bytes) else subject[0]

            processed_any = False

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                filename = decode_filename(part)
                if not filename:
                    continue

                # 只處理含 401 的附檔
                if "401" not in filename:
                    continue

                # 只處理 PDF
                if not filename.lower().endswith(".pdf"):
                    log_lines.append(f"⚠️ 略過非PDF：{filename}")
                    skipped += 1
                    continue

                # 判斷地區
                region_name, folder_id = match_region(filename, regions)
                if not region_name:
                    log_lines.append(f"⚠️ 無法判斷地區（找不到關鍵字）：{filename}")
                    skipped += 1
                    continue

                if not folder_id:
                    log_lines.append(f"⚠️ {region_name} 的 folder_id 未設定：{filename}")
                    skipped += 1
                    continue

                # 抓民國年 → 西元年
                year_str = parse_roc_year(filename)
                if not year_str:
                    log_lines.append(f"⚠️ 無法解析年份（格式應為 xxx.mm）：{filename}")
                    skipped += 1
                    continue

                # 取得或建立年份資料夾
                try:
                    year_folder_id = get_or_create_year_folder(drive, folder_id, year_str)
                except Exception as e:
                    log_lines.append(f"❌ 建立年份資料夾失敗：{filename}｜{e}")
                    error += 1
                    continue

                # 下載附件
                try:
                    file_data = part.get_payload(decode=True)
                except Exception as e:
                    log_lines.append(f"❌ 下載附件失敗：{filename}｜{e}")
                    error += 1
                    continue

                # 上傳到 Drive
                try:
                    result = upload_to_drive(drive, year_folder_id, filename, file_data)
                    if result == "already_exists":
                        log_lines.append(f"⏭️ 已存在略過：{region_name}/{year_str}／{filename}")
                        skipped += 1
                    else:
                        log_lines.append(f"✅ {region_name}/{year_str}／{filename}")
                        success += 1
                    processed_any = True
                except Exception as e:
                    log_lines.append(f"❌ 上傳失敗：{filename}｜{e}")
                    error += 1

            # 貼上已處理標籤
            if processed_any:
                try:
                    mark_as_processed(mail, uid)
                except Exception:
                    pass

        mail.logout()
        progress.progress(1.0)

        # 結果摘要
        if error == 0:
            st.success(f"✅ 完成！成功 {success} 筆，略過 {skipped} 筆")
        else:
            st.warning(f"完成（有錯誤）｜成功 {success}，略過 {skipped}，失敗 {error}")

        # 詳細 log
        with st.expander("詳細記錄", expanded=True):
            for line in log_lines:
                st.write(line)

        # 寫入主控表 log
        _write_log(success, skipped, error, len(messages))


def _write_log(success, skipped, error, total):
    """寫入主控表的日排程執行Log"""
    try:
        import json
        from googleapiclient.discovery import build
        from google.oauth2 import service_account

        sa_info = st.secrets["GOOGLE_SERVICE_ACCOUNT"]
        if isinstance(sa_info, str):
            sa_info = json.loads(sa_info)

        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        sheets = build("sheets", "v4", credentials=creds)

        master_id = st.secrets.get(
            "MASTER_SPREADSHEET_ID",
            "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
        )
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [[
            now,
            "Gmail401歸檔",
            "掃描歸檔",
            "手動",
            "全區",
            now[:10].replace("-", ""),
            "Google Drive",
            "",
            "成功" if error == 0 else "部分失敗",
            f"信件數={total}｜成功={success}｜略過={skipped}｜失敗={error}"
        ]]
        sheets.spreadsheets().values().append(
            spreadsheetId=master_id,
            range="日排程執行Log!A:J",
            valueInputOption="RAW",
            body={"values": row}
        ).execute()
    except Exception:
        pass  # log 失敗不影響主流程
