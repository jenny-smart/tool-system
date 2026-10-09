import re

import pytest

from tools.common import year_files
from tools.common.year_files import FOLDER_MIME, file_for_year

SHEET = "application/vnd.google-apps.spreadsheet"


class _Req:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeDrive:
    def __init__(self, items):
        self.items = {i["id"]: i for i in items}

    def files(self):
        return self

    def get(self, fileId, **_):
        i = self.items[fileId]
        return _Req({**i, "parents": [i["parent"]] if i.get("parent") else []})

    def list(self, q, **_):
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        return _Req({"files": [i for i in self.items.values() if i.get("parent") == parent]})


@pytest.fixture
def drive():
    year_files._CACHE.clear()
    return FakeDrive([
        {"id": "top", "name": "專員承攬服務費", "mimeType": FOLDER_MIME},
        {"id": "y26", "name": "2026專員承攬服務費", "mimeType": FOLDER_MIME, "parent": "top"},
        {"id": "y27", "name": "2027專員承攬服務費", "mimeType": FOLDER_MIME, "parent": "top"},
        {"id": "ty26", "name": "03.桃園專員", "mimeType": FOLDER_MIME, "parent": "y26"},
        {"id": "ty27", "name": "03.桃園專員", "mimeType": FOLDER_MIME, "parent": "y27"},
        {"id": "f26", "name": "2026專員名冊薪資_桃園", "mimeType": SHEET, "parent": "ty26"},
        {"id": "f27", "name": "2027專員名冊薪資-桃園", "mimeType": SHEET, "parent": "ty27"},
        {"id": "same26", "name": "2026外場排程-台北", "mimeType": SHEET, "parent": "ty26"},
        {"id": "same27", "name": "2027外場排程-台北", "mimeType": SHEET, "parent": "ty26"},
    ])


def test_same_year_returns_original(drive):
    assert file_for_year(drive, "f26", 2026) == "f26"


def test_year_folder_path_both_directions(drive):
    assert file_for_year(drive, "f26", 2027) == "f27"
    assert file_for_year(drive, "f27", 2026) == "f26"


def test_same_folder_sibling(drive):
    assert file_for_year(drive, "same26", 2027) == "same27"


def test_missing_year(drive):
    with pytest.raises(FileNotFoundError):
        file_for_year(drive, "f26", 2028)
