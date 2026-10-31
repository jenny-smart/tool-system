import importlib

import pytest


@pytest.mark.parametrize("module_name", ["tools.scheduled_monthly.stored_value_settlement",
                                         "tools.scheduled_monthly.stored_value_prepaid"])
def test_year_folder_uses_year_stored_value_name(monkeypatch, module_name):
    mod = importlib.import_module(module_name)
    existing = {"2026": ["wrong-2026"], "2026儲值金": ["right-2026"], "2025": ["old-2025"]}
    created = []
    monkeypatch.setattr(mod, "list_child_folders", lambda service, root, name: [{"id": i} for i in existing.get(name, [])])
    monkeypatch.setattr(mod, "get_or_create_single_child_folder",
                        lambda service, root, name: (existing.get(name) or [created.append(name) or f"new-{name}"])[0])
    assert mod.get_stored_value_year_folder(None, "root", 2026) == "right-2026"   # 不用誤建的「2026」
    assert mod.get_stored_value_year_folder(None, "root", 2025) == "old-2025"     # 舊年度沿用舊名
    assert mod.get_stored_value_year_folder(None, "root", 2027) == "new-2027儲值金"
    assert created == ["2027儲值金"]


def test_vip_workflow_period_folder(monkeypatch):
    from services.vip_workflow import VipStoredValueWorkflow as VipWorkflow

    folders = {("root", "2026儲值金"): {"id": "y2026"}, ("root", "2025"): {"id": "y2025"}}
    made = []

    class Drive:
        def find_folder(self, parent, name):
            return folders.get((parent, name))

        def get_or_create_folder(self, parent, name):
            made.append((parent, name))
            return folders.get((parent, name)) or {"id": f"{parent}/{name}"}

    wf = VipWorkflow.__new__(VipWorkflow)
    wf.drive, wf.root_folder_id = Drive(), "root"
    assert wf.get_period_folder("202610")["id"] == "y2026/202610"
    assert wf.get_period_folder("202512")["id"] == "y2025/202512"
    assert wf.get_period_folder("202701")["id"] == "root/2027儲值金/202701"
