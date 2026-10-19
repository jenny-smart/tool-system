from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.invoice_center import invoice_archive as archive
from tools.newebpay import download_reports as reports


def test_reuses_existing_area_despite_different_number():
    drive = MagicMock()
    drive.files().list().execute.return_value = {
        "files": [{"id": "hsinchu", "name": "04.新竹專員"}]
    }
    assert archive.get_or_create_area_folder(drive, "year", "新竹") == "hsinchu"
    drive.files().create.assert_not_called()


def test_ambiguous_area_is_not_silently_selected():
    drive = MagicMock()
    drive.files().list().execute.return_value = {"files": [
        {"id": "a", "name": "04.新竹專員"},
        {"id": "b", "name": "05.新竹專員"},
    ]}
    with pytest.raises(RuntimeError, match="多個"):
        archive.get_or_create_area_folder(drive, "year", "新竹")


def test_report_upload_uses_fixed_root_and_selected_period(monkeypatch):
    folders = []
    def folder(drive, parent, name):
        folders.append((parent, name))
        return name
    monkeypatch.setattr(reports, "get_or_create_folder", folder)
    monkeypatch.setattr(reports, "get_or_create_area_folder", lambda *a: "existing-area")
    uploaded = []
    monkeypatch.setattr(reports, "upload_replacing", lambda d, p, f: uploaded.append((p, f)) or "file-id")
    paths = [Path("payment.csv"), Path("refund.csv")]
    links = reports.archive_reports(object(), "新竹", "202609-2", paths)
    assert folders == [(archive.MONTHLY_ROOT_FOLDER_ID, "2026專員承攬服務費"), ("existing-area", "202609-2")]
    assert uploaded == [(p, "202609-2") for p in paths]
    assert all("file-id" in link for link in links)


def test_invoice_defaults_route_all_downloads_to_same_period(monkeypatch):
    monkeypatch.setattr(archive, "get_or_create_folder", lambda d, p, n: p + "/" + n)
    monkeypatch.setattr(archive, "get_or_create_area_folder", lambda d, p, a: p + "/" + a)
    for area, finance, root, enabled, note in archive.DEFAULT_CONFIG:
        result = archive.resolve_archive_folders(object(), archive.AreaRootConfig(area, finance, root), "202609")
        assert result.full_invoice == result.paper_invoice == result.prize_invoice
        assert result.full_invoice.startswith(archive.MONTHLY_ROOT_FOLDER_ID + "/")
        assert result.full_invoice.endswith("/202609-2")
