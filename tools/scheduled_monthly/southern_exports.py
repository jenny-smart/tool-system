"""南區報表：先下載台南，再下載高雄，保存來源並將台南接在高雄後去重。"""
from pathlib import Path
import pandas as pd


def export_south_sources(download_frame, temp_dir, tag, label):
    frames = {}
    source_paths = []
    for region in ("台南", "高雄"):
        frame = download_frame(region)
        if not isinstance(frame, pd.DataFrame):
            raise RuntimeError(f"{label}原{region}無法解析，停止合併")
        path = str(Path(temp_dir) / f"{tag}-{label}-原{region}.xlsx")
        frame.to_excel(path, index=False)
        frames[region] = frame
        source_paths.append(path)
    nonempty = [frames[region] for region in ("高雄", "台南") if not frames[region].empty]
    merged = pd.concat(nonempty, ignore_index=True).drop_duplicates().reset_index(drop=True) if nonempty else frames["高雄"].copy()
    final_path = str(Path(temp_dir) / f"{tag}-{label}-高雄.xlsx")
    merged.to_excel(final_path, index=False)
    return merged, source_paths, final_path
