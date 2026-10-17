"""富邦／元大財報寫入位置、藍新／鯨躍下載上傳改用雲端設定（不讀本機設定檔）。"""
from unittest.mock import MagicMock

import pytest

from tools.bank_statement import google_config
from tools.common import contractor_upload, year_roots


def test_sheet_target_uses_year_root_and_tab_name(monkeypatch):
    calls = []
    monkeypatch.setattr(year_roots, "root_file_id",
                        lambda cat, area, year=None: calls.append((cat, area, year)) or f"FIN-{area}")
    fubon = google_config.load_sheet_target("台北", "fubon")
    yuanta = google_config.load_sheet_target("台中", "yuanta", 2027)
    assert (fubon.spreadsheet_id, fubon.worksheet_title) == ("FIN-台北", "富邦更新")
    assert (yuanta.spreadsheet_id, yuanta.worksheet_title) == ("FIN-台中", "元大更新")
    assert calls == [("財報", "台北", None), ("財報", "台中", 2027)]
    with pytest.raises(ValueError):
        google_config.load_sheet_target("台北", "other")


def test_credentials_prefer_cloud_secrets(monkeypatch, tmp_path):
    info = {"type": "service_account", "project_id": "p", "private_key": "k",
            "client_email": "e", "token_uri": "t"}
    from tools.common import config_loader
    monkeypatch.setattr(config_loader, "get_service_account_info", lambda: info)
    assert google_config.load_google_credentials(tmp_path / "missing.json") == info

    def boom():
        raise RuntimeError("no secrets")
    monkeypatch.setattr(config_loader, "get_service_account_info", boom)
    with pytest.raises(RuntimeError):
        google_config.load_google_credentials(tmp_path / "missing.json")


class _Drive:
    def __init__(self, folders):
        self.folders = folders  # name -> id under the year folder

    def files(self):
        drive = self

        class Files:
            def list(self, **kwargs):
                result = MagicMock()
                result.execute.return_value = {"files": [{"id": i, "name": n} for n, i in drive.folders.items()]}
                return result
        return Files()


def test_area_folder_matches_suffix_then_creates(monkeypatch):
    created = []
    monkeypatch.setattr(contractor_upload, "get_or_create_folder",
                        lambda drive, parent, name: created.append((parent, name)) or f"NEW-{name}")
    drive = _Drive({"03.桃園專員": "TY", "01.台北專員": "TP"})
    assert contractor_upload._area_folder(drive, "Y", "桃園") == "TY"
    assert contractor_upload._area_folder(drive, "Y", "家電") == "NEW-06.家電專員"
    assert created == [("Y", "06.家電專員")]
    with pytest.raises(RuntimeError):
        contractor_upload._area_folder(drive, "Y", "火星")
