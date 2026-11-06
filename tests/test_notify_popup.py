"""2026-11-06 Jenny：盤點完 Mac 跳視窗提醒去確認及執行。"""
from tools import local_agent
from tools import notify_popup as np


def test_deep_link_and_script():
    url = np.deep_link("https://tools.example.app/", "財務管理", "【整合執行】清潔異動已備妥")
    assert url.startswith("https://tools.example.app/?system=") and "fn=" in url
    script = np.build_script('標題"', "第一行\n第二行", url)
    assert '"前往執行"' in script and 'with title "標題\\""' in script and "open " in script
    assert '"知道了"' in np.build_script("t", "m", "") and "open " not in np.build_script("t", "m", "")


def test_agent_builds_popup_command():
    cmd = local_agent.command_for({"action": "notify.popup",
                                   "params_json": '{"title":"【清潔異動】盤點","message":"可執行 1 筆","function":"x"}'})
    assert cmd[1:3] == ["-m", "tools.notify_popup"] and "【清潔異動】盤點" in cmd and "x" in cmd
