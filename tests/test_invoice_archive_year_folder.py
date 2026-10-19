"""銷項發票根目錄：換年找不到年度資料夾時，依「{YYYY}發票折讓單相關-{地區}」命名新建。"""
from tools.invoice_center import invoice_archive as archive


class _Drive:
    def __init__(self, names):
        self.names = names

    def files(self):
        drive = self

        class Files:
            def list(self, **kwargs):
                class R:
                    def execute(_):
                        return {"files": [{"id": f"id-{n}", "name": n} for n in drive.names]}
                return R()
        return Files()


def test_existing_year_folder_is_reused(monkeypatch):
    monkeypatch.setattr(archive, "get_or_create_folder", lambda *a: (_ for _ in ()).throw(AssertionError))
    drive = _Drive(["2027發票折讓單相關-台北", "2026發票折讓單相關-台北"])
    assert archive.get_or_create_finance_year_folder(drive, "root", "2027", "台北") == "id-2027發票折讓單相關-台北"


def test_missing_year_folder_created_with_full_name(monkeypatch):
    created = []
    monkeypatch.setattr(archive, "get_or_create_folder", lambda drive, parent, name: created.append(name) or "new")
    assert archive.get_or_create_finance_year_folder(_Drive(["2026發票折讓單相關-台中"]), "root", "2027", "台中") == "new"
    assert created == ["2027發票折讓單相關-台中"]
