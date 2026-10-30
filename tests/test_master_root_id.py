from types import SimpleNamespace

from tools.common import year_roots


def _rule(category, areas, root_id):
    return year_roots.Rule(row=1, category=category, areas=areas, root_id=root_id, year_folder="", sub_folder="",
                           file_names=[], since_year=2026, mode="比對")


def test_reads_root_from_master_sheet(monkeypatch):
    roots = SimpleNamespace(rules=[_rule("春酒專區", ["共用"], "ROOT-FROM-SHEET"),
                                   _rule("清潔用品", ["台北", "台中"], "SUPPLY-FROM-SHEET")])
    monkeypatch.setattr(year_roots, "get_year_roots", lambda: roots)
    assert year_roots.master_root_id("春酒專區", "DEFAULT") == "ROOT-FROM-SHEET"
    assert year_roots.master_root_id("清潔用品", "DEFAULT", "台北") == "SUPPLY-FROM-SHEET"
    assert year_roots.master_root_id("沒有這列", "DEFAULT") == "DEFAULT"


def test_falls_back_when_master_sheet_unreadable(monkeypatch):
    monkeypatch.setattr(year_roots, "get_year_roots", lambda: None)
    assert year_roots.master_root_id("春節禮品", "DEFAULT") == "DEFAULT"

    def boom():
        raise RuntimeError("no access")

    monkeypatch.setattr(year_roots, "get_year_roots", boom)
    assert year_roots.master_root_id("春節禮品", "DEFAULT") == "DEFAULT"
