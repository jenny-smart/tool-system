from auth import get_account, login
from reports.schedule_stats import run_schedule_stats


def run_city_bundle(city: str) -> list[dict]:
    acc = get_account(city)
    session = login(acc["email"], acc["password"])

    results = []
    results.extend(run_schedule_stats(session, city))

    return results


def main():
    all_results = []

    for city in ["台北", "台中"]:
        print(f"=== 開始 {city} ===")
        city_results = run_city_bundle(city)
        all_results.extend(city_results)

        for item in city_results:
            print(f"✅ {item['filename']} 上傳成功，file_id={item['file_id']}")

        print(f"=== 結束 {city} ===")

    return all_results


if __name__ == "__main__":
    main()
