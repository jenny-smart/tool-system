elif system_name == "日排程系統":
    import subprocess
    import sys

    daily_map = {
        "排班統計表": "tools/scheduled_daily/schedule_report.py",
        "專員班表": "tools/scheduled_daily/staff_schedule.py",
        "專員個資": "tools/scheduled_daily/staff_info.py",
        "當月次月訂單": "tools/scheduled_daily/orders_report.py",
        "業績報表": "tools/scheduled_daily/performance_report.py",
    }

    if selected_function == "一鍵執行日排程":
        from tools.scheduled_daily.scheduler import main as run_daily_scheduler
        result = run_daily_scheduler()
    else:
        script = daily_map[selected_function]
        subprocess.run([sys.executable, script], check=True)
        result = f"{selected_function} 執行完成"
