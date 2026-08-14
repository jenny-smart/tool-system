# CLAUDE.md — tool-system

Internal operations platform for **Lemon Clean** (檸檬先生清潔服務).
Built with Streamlit, deployed on Streamlit Cloud, automated via GitHub Actions.

## What this system does

A multi-subsystem scheduler and reporting console that:
- Downloads reports from a backend API (`backend.lemonclean.com.tw`) per city
- Uploads files to Google Drive (with upsert logic: trash existing same-name file, then upload)
- Writes structured execution logs to a master Google Sheet
- Exposes a Streamlit UI (`toolapp.py`) for manual triggering and monitoring

Cities/areas in scope: 台北、台中、桃園、新竹、高雄

---

## Repository layout

```
config/
  roles.yaml          # Role → allowed systems/pages/log groups
  systems.yaml        # System definitions with Drive folder/spreadsheet IDs
  users.yaml          # Username/password/role (plaintext — internal use only)
  op_paths.py         # Path constants; detects local Mac vs cloud env
  vip_config.py       # Constants for VIP stored-value workflow

services/
  google_auth.py      # get_credentials() / get_gspread_client() / get_drive_service()
  google_drive.py     # DriveService class: find/create/copy/trash files and folders
  google_sheets.py    # SheetsService class + MasterLog + FormulaSettings helpers
  google_api_retry.py # Monkey-patches gspread to add exponential-backoff retry
  backend.py          # ACCOUNTS dict loaded from Streamlit secrets or env vars
  backend_auth.py     # login_backend() — scrapes CSRF token then POSTs credentials
  vip_workflow.py     # VipStoredValueWorkflow: 5-step monthly VIP settlement flow

utils/
  auth.py             # authenticate(username, password) against config/users.yaml
  permissions.py      # can_access_system/page/log(), get_allowed_log_jobs()

tools/
  scheduled_daily/    # Daily jobs: schedule_report, staff_schedule, orders_report, staff_info
    scheduler.py      # Orchestrator: punch() logging + subprocess execution
  scheduled_monthly/  # Monthly jobs: half_month_orders (×2), refund, prepaid, stored-value ×2
    scheduler.py      # Same pattern as daily; resolves year folder in Drive
  field_management/   # Field ops: schedule_stats, staff_schedule, orders, staff_profile
    scheduler.py      # Runs jobs as python -m <module>

scheduler_console/
  opapp.py            # Streamlit entry-point (sidebar nav, session auth)
  dashboard_main.py   # All page render functions; reads GitHub Actions API for status

toolapp.py            # run_streaming() helper used by the main Streamlit app

logs/YYYYMMDD/        # Per-run log files committed by GitHub Actions
dashboard_data/latest/ # CSV + JSON outputs from performance report (committed by Actions)

.github/workflows/
  scheduled_daily.yml     # Cron triggers for daily tasks + manual dispatch
  performance_report.yml  # Cron for performance report (4×/day)
```

---

## Subsystems

| Key (`type` in systems.yaml) | Chinese name | Scheduler |
|---|---|---|
| `vip` | 儲值金管理 | `vip_workflow.py` |
| `daily_scheduler` | 日排程系統 | `tools/scheduled_daily/scheduler.py` |
| `monthly_scheduler` | 月排程系統 | `tools/scheduled_monthly/scheduler.py` |
| `field_daily_schedule` | 外場排程系統 | `tools/field_management/scheduler.py` |
| `finance_management` | 財務管理 | — |
| `orders_memo_system` | 訂單系統 | — |

### Daily scheduler jobs

| `job_name` | Label | Script |
|---|---|---|
| `schedule_report` | 排班統計表 | `tools/scheduled_daily/schedule_report.py` |
| `staff_schedule` | 專員班表 | `tools/scheduled_daily/staff_schedule.py` |
| `orders_report` | 當月次月訂單 | `tools/scheduled_daily/orders_report.py` |
| `staff_info` | 專員個資 | `tools/scheduled_daily/staff_info.py` |

### Monthly scheduler jobs

| `job_name` | Label |
|---|---|
| `half_month_orders_1` | 上半月訂單 |
| `half_month_orders_2` | 下半月訂單 |
| `refund_report` | 已退款 |
| `prepaid_report` | 預收 |
| `stored_value_settlement` | 儲值金結算 |
| `stored_value_prepaid` | 儲值金預收 |

### Field management jobs

| `job_name` | Module |
|---|---|
| `schedule_stats` | `tools.field_management.schedule_stats` |
| `staff_schedule` | `tools.field_management.staff_schedule` |
| `orders` | `tools.field_management.orders` |
| `staff_profile` | `tools.field_management.staff_profile` |

---

## Authentication & Credentials

### App login
`utils/auth.py` → `authenticate()` reads `config/users.yaml`. Roles: `admin`, `field_only`, `cs`.
`utils/permissions.py` checks role against `config/roles.yaml` for system/page/log access.

### Google credentials (two separate identities)

| Purpose | Auth method | Source |
|---|---|---|
| Sheets/Drive API in Streamlit UI | Service Account | `st.secrets["gcp_service_account"]` |
| Daily/monthly Drive ops in GitHub Actions | Jenny OAuth (refresh token) | `GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN` secrets |
| Execution log writes | Service Account | `GOOGLE_SERVICE_ACCOUNT` or `GOOGLE_SERVICE_ACCOUNT_JSON` |

The schedulers in Actions call `build_child_env()` which injects both sets of credentials into
subprocess environment. Child processes bootstrap via `sitecustomize` reload to ensure Jenny OAuth
overrides Service Account for Drive file creation/copy/trash.

### Backend (lemonclean.com.tw) accounts
`services/backend.py` builds `ACCOUNTS` dict from Streamlit secrets or env vars:
`TAIPEI_EMAIL` / `TAIPEI_PASSWORD`, `TAICHUNG_EMAIL` / `TAICHUNG_PASSWORD`, etc.
`services/backend_auth.py`:`login_backend()` scrapes `_token` CSRF from login page then POSTs.

---

## Key conventions

### Scheduler pattern (`punch` + subprocess)
Every scheduler follows this flow:
1. Validate args and resolve folder IDs
2. Call `punch(..., status="running")` to write "執行中" to log sheet
3. Build child command (`build_child_command`) — always bootstraps `sitecustomize` first
4. `subprocess.run(cmd, capture_output=True)` — full stdout/stderr captured
5. On success: `punch(..., status="success")`
6. On failure: `punch(..., status="failed", traceback_text=...)` then `raise RuntimeError`

Never call scheduler job scripts directly from Streamlit — always go through the relevant
`scheduler.py:run_job()` so logging is guaranteed.

### Google Drive upsert
When uploading a file to Drive, always use `DriveService.replace_google_sheet_from_source()`:
1. Trash all existing Google Sheets with the same base name in the target folder
2. Convert the source xlsx/xls/csv to a new Google Sheet
3. Never delete xlsx source files; only trash the converted Google Sheet duplicates.

`services/google_drive.py:is_source_spreadsheet_file()` guards against re-processing already-
converted Google Sheets.

### Google API retry
`services/google_api_retry.py:install_gspread_retry()` is a one-time monkey-patch that wraps
`gspread.HTTPClient.request` with exponential backoff (base 6s, max 60s, 6 attempts).
Call this once at app startup. Use `api_pause()` in tight loops to reduce burst quota usage.

### Period format
Monthly operations use `YYYYMM` strings (e.g. `"202604"` for April 2026).
Daily operations use `YYYYMMDD` strings. Always use Taiwan time (`Asia/Taipei`, UTC+8).

### Path handling (`config/op_paths.py`)
`IS_LOCAL_MAC` is `True` when running on Jenny's Mac with Google Drive mounted.
When `False` (Streamlit Cloud / GitHub Actions), all paths use `/tmp/lemon_data/` as base.
Import paths from `op_paths.py`; never hardcode `/Users/jenny/`.

---

## GitHub Actions schedules

### `scheduled_daily.yml`
| UTC cron | Taipei time | Task |
|---|---|---|
| `0 17 * * *` | 01:00 | `schedule_report` |
| `10 17 * * *` | 01:10 | `staff_schedule` |
| `20 17 * * *` | 01:20 | `orders_report` |
| `30 17 * * *` | 01:30 | `staff_info` |
| `0 23 * * *` | 07:00 | `notify` (send_daily_result.py) |
| `0 14 * * *` | 22:00 | `month_end_cleanup` |

Manual dispatch via `workflow_dispatch` supports all individual tasks plus `all`.

### `performance_report.yml`
Runs at UTC 16:00 / 00:00 / 04:00 / 10:00 (Taipei 00:00, 08:00, 12:00, 18:00).
Commits `dashboard_data/latest/` (CSV+JSON) and `logs/` back to the branch.

### Log commits
Both workflows commit logs under `logs/YYYYMMDD/` using `github-actions[bot]`.
The `.gitignore` explicitly allows `*.log`, `*.exit`, and `*.json` inside `logs/`.

---

## Required GitHub Secrets

| Secret | Used by |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | Daily/monthly Drive ops |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Daily/monthly Drive ops |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Daily/monthly Drive ops |
| `GOOGLE_SERVICE_ACCOUNT` | Log sheet writes |
| `TOOLS_APP_LOG_SPREADSHEET_ID` | Log destination (falls back to `MASTER_SPREADSHEET_ID`) |
| `MASTER_SPREADSHEET_ID` | Master control spreadsheet |
| `TAIPEI_EMAIL` / `TAIPEI_PASSWORD` | Backend scraping |
| `TAICHUNG_EMAIL` / `TAICHUNG_PASSWORD` | Backend scraping |
| `NOTIFY_EMAIL` / `NOTIFY_PASSWORD` / `NOTIFY_TO` | Daily result email |

Performance report also uses: `TAOYUAN_*`, `HSINCHU_*`, `KAOHSIUNG_*`.

---

## Streamlit Secrets structure

```toml
[gcp_service_account]
type = "service_account"
project_id = "..."
# ... full service account JSON fields

[accounts.taipei]
email = "..."
password = "..."

[accounts.taichung]
email = "..."
password = "..."
# ... taoyuan, hsinchu, kaohsiung

[github]
owner = "jenny-smart"
repo  = "tool-system"
token = "ghp_..."
branch = "main"

GOOGLE_OAUTH_CLIENT_ID     = "..."
GOOGLE_OAUTH_CLIENT_SECRET = "..."
GOOGLE_OAUTH_REFRESH_TOKEN = "..."
TOOLS_APP_LOG_SPREADSHEET_ID = "..."
MASTER_SPREADSHEET_ID      = "..."
```

---

## Development notes

- Python 3.11. All modules use `from __future__ import annotations`.
- No test suite exists. Validate changes by running individual scheduler scripts locally.
- When adding a new daily/monthly job, register it in the relevant `scheduler.py`'s `JOBS` dict
  AND in the GitHub Actions workflow's `Decide task` step and `Run daily task` step.
- When adding a new system type, add it to `config/systems.yaml`, `config/roles.yaml` (under
  relevant roles), and `utils/permissions.py`'s `LOG_GROUPS` if it produces logs.
- `config/users.yaml` stores plaintext passwords — do not add real user data to this file without
  confirming the deployment environment is private.
- The `dashboard_data/` and `logs/` directories have `.gitkeep` files; the actual contents are
  committed by GitHub Actions, not developers.
