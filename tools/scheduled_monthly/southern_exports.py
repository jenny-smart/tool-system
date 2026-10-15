"""南區報表：保存原始活頁簿，再將台南接在高雄後去重。"""
from pathlib import Path
import pandas as pd

from tools.scheduled_monthly.excel_preservation import merge_workbook_files, save_preserved_excel


def export_south_sources(download_content, temp_dir, tag, label):
    source_paths = []
    for region in ("台南", "高雄"):
        content = download_content(region)
        if not isinstance(content, bytes):
            raise RuntimeError(f"{label}原{region}不是 Excel 內容，停止合併")
        path = str(Path(temp_dir) / f"{tag}-{label}-原{region}.xlsx")
        save_preserved_excel(content, path)
        source_paths.append(path)
    final_path = str(Path(temp_dir) / f"{tag}-{label}-高雄.xlsx")
    merge_workbook_files([source_paths[1], source_paths[0]], final_path)
    merged = pd.read_excel(final_path, engine="openpyxl")
    return merged, source_paths, final_path
