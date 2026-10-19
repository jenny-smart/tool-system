from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.invoice_center import invoice_archive as archive


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
