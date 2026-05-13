from __future__ import annotations

import argparse
import json
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import streamlit as st
    HAS_STREAMLIT = True
except Exception:
    st = None
    HAS_STREAMLIT = False

TZ_TAIPEI = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[2]
PATH_LATEST = BASE_DIR / "dashboard_data" / "latest"
PATH_SNAPSHOTS = BASE_DIR / "dashboard_data" / "snapshots"
PATH_LOGS = BASE_DIR / "logs"

LOGIN_URL = "https://backend.lemonclean.com.tw/login"
PURCHASE_URL = "https://backend.lemonclean.com.tw/purchase"
HEADERS = {"User-Agent": "Mozilla/5.0"}
CITY_ORDER = ["台北", "台中", "桃園", "新竹", "高雄"]


def now_dt() -> datetime:
    return datetime.now(TZ_TAIPEI)


def now_text() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def today_key() -> str:
    return now_dt().strftime("%Y%m%d")


def month_key() -> str:
    return now_dt().strftime("%Y%m")


def log(message: str) -> None:
    print(f"[{now_text()}] {message}", flush=True)


def ensure_dirs() -> None:
    PATH_LATEST.mkdir(parents=True, exist_ok=True)
    (PATH_SNAPSHOTS / month_key()).mkdir(parents=True, exist_ok=True)
    (PATH_LOGS / today_key()).mkdir(parents=True, exist_ok=True)


def get_secret(path_list: list[str], env_name: str | None = None, required: bool = False, default: Any = None) -> Any:
    if HAS_STREAMLIT:
        try:
            cur = st.secrets
            for key in path_list:
                cur = cur[key]
            if cur is not None and str(cur) != "":
                return cur
        except Exception:
            pass

    if env_name:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value

    if required:
        raise RuntimeError(f"讀不到設定值：{'/'.join(path_list)}")
    return default


def apply_email_fallback_env() -> None:
    if not os.getenv("REPORT_EMAIL_SENDER") and os.getenv("NOTIFY_EMAIL"):
        os.environ["REPORT_EMAIL_SENDER"] = os.getenv("NOTIFY_EMAIL", "")
    if not os.getenv("REPORT_EMAIL_APP_PASSWORD") and os.getenv("NOTIFY_PASSWORD"):
        os.environ["REPORT_EMAIL_APP_PASSWORD"] = os.getenv("NOTIFY_PASSWORD", "")
    if not os.getenv("REPORT_EMAIL_RECIPIENT") and os.getenv("NOTIFY_TO"):
        os.environ["REPORT_EMAIL_RECIPIENT"] = os.getenv("NOTIFY_TO", "")


def load_accounts_dict() -> dict[str, dict[str, str]]:
    city_env_map = {
        "台北": ("taipei", "TAIPEI_EMAIL", "TAIPEI_PASSWORD"),
        "台中": ("taichung", "TAICHUNG_EMAIL", "TAICHUNG_PASSWORD"),
        "桃園": ("taoyuan", "TAOYUAN_EMAIL", "TAOYUAN_PASSWORD"),
        "新竹": ("hsinchu", "HSINCHU_EMAIL", "HSINCHU_PASSWORD"),
        "高雄": ("kaohsiung", "KAOHSIUNG_EMAIL", "KAOHSIUNG_PASSWORD"),
    }
    accounts: dict[str, dict[str, str]] = {}
    for city, (key, email_env, password_env) in city_env_map.items():
        email = get_secret(["accounts", key, "email"], env_name=email_env, required=False)
        password = get_secret(["accounts", key, "password"], env_name=password_env, required=False)
        if email and password:
            accounts[city] = {"email": str(email), "password": str(password)}
    return accounts


@dataclass
class CityResult:
    city: str
    ok: bool
    rows: int = 0
    message: str = ""


def login(session: requests.Session, email: str, password: str) -> None:
    resp = session.get(LOGIN_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    token_tag = soup.find("input", {"name": "_token"})
    token = token_tag.get("value", "") if token_tag else ""
    payload = {"email": email, "password": password}
    if token:
        payload["_token"] = token
    resp = session.post(LOGIN_URL, data=payload, headers=HEADERS, timeout=30, allow_redirects=True)
    resp.raise_for_status()


def normalize_table(df: pd.DataFrame, city: str) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df["城市"] = city
    df["抓取時間"] = now_text()
    return df


def fetch_city_data(city: str, account: dict[str, str]) -> tuple[pd.DataFrame, CityResult]:
    log(f"開始抓取：{city}")
    session = requests.Session()
    login(session, account["email"], account["password"])
    log(f"✅ 登入成功：{city}")
    resp = session.get(PURCHASE_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    tables = pd.read_html(resp.text)
    frames = [normalize_table(t, city) for t in tables if t is not None and not t.empty]
    if not frames:
        return pd.DataFrame(), CityResult(city=city, ok=False, rows=0, message="沒有抓到表格資料")
    df = pd.concat(frames, ignore_index=True)
    log(f"✅ {city} 取得 {len(df)} 筆")
    return df, CityResult(city=city, ok=True, rows=len(df), message="完成")


def safe_number(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return 0.0


def guess_amount_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        text = str(col).lower()
        if any(k in text for k in ["金額", "收入", "總計", "合計", "price", "amount", "total"]):
            return col
    return None


def build_summary(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df.empty:
        return pd.DataFrame(columns=["城市", "筆數", "金額"])
    work = raw_df.copy()
    amount_col = guess_amount_column(work)
    work["_amount"] = work[amount_col].map(safe_number) if amount_col else 0
    return work.groupby("城市", dropna=False).agg(筆數=("城市", "size"), 金額=("_amount", "sum")).reset_index()


def df_to_html_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>尚無資料</p>"
    return df.to_html(index=False, border=0, classes="report-table")


def build_email_html(summary_df: pd.DataFrame, raw_df: pd.DataFrame, results: list[CityResult]) -> str:
    total_rows = int(summary_df["筆數"].sum()) if not summary_df.empty and "筆數" in summary_df.columns else 0
    total_amount = float(summary_df["金額"].sum()) if not summary_df.empty and "金額" in summary_df.columns else 0
    result_rows = "".join(
        f"<tr><td>{r.city}</td><td>{'✅ 成功' if r.ok else '⚠️ 無資料/失敗'}</td><td>{r.rows}</td><td>{r.message}</td></tr>"
        for r in results
    )
    return f"""
<!doctype html><html><head><meta charset="utf-8"><style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#111827; }}
.card {{ border:1px solid #e5e7eb; border-radius:16px; padding:24px; max-width:880px; margin:auto; }}
.stat {{ display:inline-block; background:#f3f4f6; border-radius:12px; padding:12px 16px; margin:4px; }}
.report-table {{ border-collapse: collapse; width:100%; margin-top:16px; }}
.report-table th {{ background:#eef2ff; text-align:left; padding:8px; }}
.report-table td {{ border-bottom:1px solid #e5e7eb; padding:8px; }}
</style></head><body><div class="card">
<h1>📊 業績報表</h1><p>產生時間：{now_text()}</p>
<div class="stat">總筆數：{total_rows}</div><div class="stat">估算金額：{total_amount:,.0f}</div>
<h2>城市摘要</h2>{df_to_html_table(summary_df)}
<h2>執行狀態</h2><table class="report-table"><thead><tr><th>城市</th><th>狀態</th><th>筆數</th><th>訊息</th></tr></thead><tbody>{result_rows}</tbody></table>
</div></body></html>"""


def load_existing_if_needed() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    raw_path = PATH_LATEST / "performance_raw.csv"
    summary_path = PATH_LATEST / "performance_summary.csv"
    if raw_path.exists() and summary_path.exists():
        return pd.read_csv(raw_path), pd.read_csv(summary_path)
    return None


def save_outputs(raw_df: pd.DataFrame, summary_df: pd.DataFrame, html_text: str, results: list[CityResult]) -> dict[str, str]:
    ensure_dirs()
    ts = now_dt().strftime("%Y%m%d_%H%M%S")
    snap_dir = PATH_SNAPSHOTS / month_key()
    snap_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "raw_csv": PATH_LATEST / "performance_raw.csv",
        "summary_csv": PATH_LATEST / "performance_summary.csv",
        "daily_df_csv": PATH_LATEST / "daily_df.csv",
        "df4_csv": PATH_LATEST / "df4.csv",
        "month_csv": PATH_LATEST / f"{month_key()}.csv",
        "email_html": PATH_LATEST / "email_preview.html",
        "meta_json": PATH_LATEST / "meta.json",
        "snapshot_email_html": snap_dir / f"{ts}_email_preview.html",
        "snapshot_meta_json": snap_dir / f"{ts}_meta.json",
        "snapshot_daily_df_csv": snap_dir / f"{ts}_daily_df.csv",
        "snapshot_df4_csv": snap_dir / f"{ts}_df4.csv",
    }
    raw_df.to_csv(files["raw_csv"], index=False, encoding="utf-8-sig")
    summary_df.to_csv(files["summary_csv"], index=False, encoding="utf-8-sig")
    summary_df.to_csv(files["daily_df_csv"], index=False, encoding="utf-8-sig")
    raw_df.head(200).to_csv(files["df4_csv"], index=False, encoding="utf-8-sig")
    summary_df.to_csv(files["month_csv"], index=False, encoding="utf-8-sig")
    files["email_html"].write_text(html_text, encoding="utf-8")
    files["snapshot_email_html"].write_text(html_text, encoding="utf-8")
    meta = {"generated_at": now_text(), "date_key": today_key(), "month_key": month_key(), "raw_rows": int(len(raw_df)), "summary_rows": int(len(summary_df)), "results": [r.__dict__ for r in results]}
    files["meta_json"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    files["snapshot_meta_json"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_df.head(200).to_csv(files["snapshot_df4_csv"], index=False, encoding="utf-8-sig")
    summary_df.to_csv(files["snapshot_daily_df_csv"], index=False, encoding="utf-8-sig")
    log(f"✅ 已輸出 dashboard_data/latest 與 snapshots：{ts}")
    return {k: str(v) for k, v in files.items()}


def send_email(html_text: str) -> None:
    apply_email_fallback_env()
    sender = get_secret(["email", "sender"], env_name="REPORT_EMAIL_SENDER", required=False) or os.getenv("NOTIFY_EMAIL")
    password = get_secret(["email", "app_password"], env_name="REPORT_EMAIL_APP_PASSWORD", required=False) or os.getenv("NOTIFY_PASSWORD")
    recipient = get_secret(["email", "recipient"], env_name="REPORT_EMAIL_RECIPIENT", required=False) or os.getenv("NOTIFY_TO")
    if not sender or not password or not recipient:
        log("⚠️ 未設定寄信資訊，略過寄信")
        return
    msg = MIMEText(html_text, "html", "utf-8")
    msg["Subject"] = f"Tools App 業績報表 {today_key()}"
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, [x.strip() for x in str(recipient).split(",") if x.strip()], msg.as_string())
    log(f"✅ 業績報表已寄出：{recipient}")


def generate_report(mode: str = "dashboard", auto_send: bool = False) -> dict[str, Any]:
    ensure_dirs()
    accounts = load_accounts_dict()
    results: list[CityResult] = []
    frames: list[pd.DataFrame] = []
    if accounts:
        for city in CITY_ORDER:
            account = accounts.get(city)
            if not account:
                results.append(CityResult(city=city, ok=False, rows=0, message="未設定帳號"))
                continue
            try:
                df, result = fetch_city_data(city, account)
                results.append(result)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                results.append(CityResult(city=city, ok=False, rows=0, message=str(exc)))
                log(f"⚠️ {city} 失敗：{exc}")
    if frames:
        raw_df = pd.concat(frames, ignore_index=True)
        summary_df = build_summary(raw_df)
    else:
        existing = load_existing_if_needed()
        if existing:
            log("⚠️ 本次沒有抓到新資料，使用既有 latest 資料")
            raw_df, summary_df = existing
            if not results:
                results = [CityResult(city="existing", ok=True, rows=len(raw_df), message="使用既有資料")]
        else:
            raise RuntimeError("沒有抓到資料，也沒有既有 latest 資料可用")
    html_text = build_email_html(summary_df, raw_df, results)
    paths = save_outputs(raw_df, summary_df, html_text, results)
    if auto_send:
        send_email(html_text)
    return {"mode": mode, "auto_send": auto_send, "paths": paths, "raw_rows": len(raw_df), "summary_rows": len(summary_df)}


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in ["1", "true", "yes", "y", "寄送", "send"]


def main(mode: str = "dashboard", auto_send: bool = False) -> dict[str, Any]:
    log(f"開始更新業績報表：mode={mode}, auto_send={auto_send}")
    result = generate_report(mode=mode, auto_send=auto_send)
    log("🎉 業績報表更新完成")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="dashboard")
    parser.add_argument("auto_send", nargs="?", default="false")
    args = parser.parse_args()
    main(mode=args.mode, auto_send=parse_bool(args.auto_send))
