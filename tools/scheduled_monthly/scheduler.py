elif system_name == "月排程系統":
    import subprocess
    import sys

    monthly_map = {
        "上半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "1"],
        "下半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "2"],
        "已退款": ["tools/scheduled_monthly/refund_report.py"],
        "預收": ["tools/scheduled_monthly/prepaid_report.py"],
        "儲值金結算": ["tools/scheduled_monthly/stored_value_settlement.py"],
        "儲值金預收": ["tools/scheduled_monthly/stored_value_prepaid.py"],
    }

    if selected_function == "一鍵執行月排程":
        from tools.scheduled_monthly.scheduler import main as run_monthly_scheduler
        result = run_monthly_scheduler()
    else:
        cmd = [sys.executable, *monthly_map[selected_function]]
        subprocess.run(cmd, check=True)
        result = f"{selected_function} 執行完成"
