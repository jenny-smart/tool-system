# ═══════════════════════════════════════════════════════════
# 補丁一：替換 export_kaohsiung()
# 空資料跳過不報錯，有資料才合併上傳
# ═══════════════════════════════════════════════════════════

def export_kaohsiung(session: requests.Session, start: str, end: str, temp_dir: str, tag: str) -> str | None:
    df_list: list[pd.DataFrame] = []

    for region in KAOHSIUNG_MERGE_REGIONS:
        try:
            log(f"👉 抓 {region}")
            content = download_single_export(session, start, end, region)
            df = read_excel_from_response(content)

            if df.empty:
                log(f"⚠️ {region} 無資料（空表格），略過")
                continue

            df_list.append(df)

            single_path = os.path.join(temp_dir, f"{tag}訂單-{region}.xlsx")
            df.to_excel(single_path, index=False)
            log(f"✅ 已下載：{single_path}")

        except Exception as exc:
            log(f"⚠️ {region} 失敗：{exc}")
            # 單一地區失敗不中斷，繼續抓下一個

    if not df_list:
        # 高雄和台南都沒資料，視為該區本期無訂單，回傳 None 不報錯
        log("ℹ️ 高雄 / 台南 本期無訂單，略過")
        return None

    merged_df = pd.concat(df_list, ignore_index=True).drop_duplicates()

    final_path = os.path.join(temp_dir, f"{tag}訂單-高雄.xlsx")
    merged_df.to_excel(final_path, index=False)

    log(f"✅ 高雄合併完成：{final_path}")
    return final_path


# ═══════════════════════════════════════════════════════════
# 補丁二：替換 process_city() 裡高雄的處理段落
# final_path 為 None 時直接 return，不上傳不報錯
# ═══════════════════════════════════════════════════════════

def process_city(
    city: str,
    args: RunArgs,
    accounts: dict[str, dict[str, str]],
    service,
    start: str,
    end: str,
    tag: str,
) -> None:
    acc = accounts[city]
    session = requests.Session()

    log(f"\n=== 處理 {city} ===")
    login(session, acc["email"], acc["password"])

    area_folder_id = resolve_area_folder(service, args.folder_id, city)
    tag_folder_id = get_or_create_child_folder(service, area_folder_id, tag)
    log(f"📁 期別資料夾：{tag} / {tag_folder_id}")

    with tempfile.TemporaryDirectory() as temp_dir:
        if city == "高雄":
            final_path = export_kaohsiung(session, start, end, temp_dir, tag)

            # ★ 無資料時直接結束，不上傳，不算失敗
            if final_path is None:
                return

        else:
            keyword = choose_keyword(city)
            content = download_single_export(session, start, end, keyword)

            final_path = os.path.join(temp_dir, f"{tag}訂單-{city}.xlsx")
            with open(final_path, "wb") as file:
                file.write(content)

            log(f"✅ 已下載：{final_path}")

        uploaded_id = upload_to_gdrive(service, final_path, tag_folder_id)

        if not args.skip_snapshot:
            save_snapshot(
                final_path,
                args.snapshot_dir,
                tag,
                city,
                {
                    "city": city,
                    "tag": tag,
                    "start": start,
                    "end": end,
                    "uploaded_file_id": uploaded_id,
                    "root_folder_id": args.folder_id,
                    "area_folder_id": area_folder_id,
                    "tag_folder_id": tag_folder_id,
                    "generated_at": tw_now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )


# ═══════════════════════════════════════════════════════════
# 補丁三：load_accounts() 刪掉新北
# 找到 load_accounts()，把新北那段直接刪掉
# ═══════════════════════════════════════════════════════════

def load_accounts() -> dict[str, dict[str, str]]:
    accounts = {
        "台北": {
            "email": secret_value(["accounts", "taipei", "email"], env_value("TAIPEI_EMAIL")),
            "password": secret_value(["accounts", "taipei", "password"], env_value("TAIPEI_PASSWORD")),
        },
        # ★ 新北已移除，不再執行
        "台中": {
            "email": secret_value(["accounts", "taichung", "email"], env_value("TAICHUNG_EMAIL")),
            "password": secret_value(["accounts", "taichung", "password"], env_value("TAICHUNG_PASSWORD")),
        },
        "桃園": {
            "email": secret_value(["accounts", "taoyuan", "email"], env_value("TAOYUAN_EMAIL")),
            "password": secret_value(["accounts", "taoyuan", "password"], env_value("TAOYUAN_PASSWORD")),
        },
        "新竹": {
            "email": secret_value(["accounts", "hsinchu", "email"], env_value("HSINCHU_EMAIL")),
            "password": secret_value(["accounts", "hsinchu", "password"], env_value("HSINCHU_PASSWORD")),
        },
        "高雄": {
            "email": secret_value(["accounts", "kaohsiung", "email"], env_value("KAOHSIUNG_EMAIL", env_value("HSINCHU_EMAIL"))),
            "password": secret_value(["accounts", "kaohsiung", "password"], env_value("KAOHSIUNG_PASSWORD", env_value("HSINCHU_PASSWORD"))),
        },
    }
    return accounts
