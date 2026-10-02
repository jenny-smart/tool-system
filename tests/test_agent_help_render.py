import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize('owner', ['mac', 'old-hostname', ''])
@pytest.mark.parametrize('online,busy', [(False, False), (False, True), (True, False), (True, True)])
def test_agent_controls_and_cancel_remain_visible(online, busy, owner):
    tree = ast.parse(Path('toolapp.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'render_agent_help')
    st = MagicMock()
    st.columns.side_effect = lambda widths: [MagicMock() for _ in range(widths if isinstance(widths, int) else len(widths))]
    st.button.return_value = False
    namespace = {
        'st': st,
        'list_local_agent_status': lambda **kw: [{'agent_id': 'mac', 'online': online, 'actions': 'system.agent_restart,system.agent_status,system.agent_logs,system.git_pull_restart'}],
        'list_local_agent_tasks': lambda **kw: [{'task_id': '1', 'agent_id': owner, 'status': 'running'}] if busy else [],
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<agent-help>', 'exec'), namespace)
    namespace['render_agent_help']()
    buttons = {call.args[0]: call.kwargs for call in st.button.call_args_list}
    assert buttons['⏹️ 中止目前工作']['disabled'] == (not busy)
    assert buttons['📊 檢查狀態']['disabled'] == (not online)
    assert buttons['📋 查看 Agent Log']['disabled'] == (not online)
    assert bool(buttons['⬇️ Git Pull 更新程式']['disabled']) == (not online or busy)
    assert buttons['🔄 重啟 Agent']['disabled'] == (not online or busy)
    assert not buttons['← 返回主控台'].get('disabled', False)
    assert any('local_agent_service.sh restart' in str(call) for call in st.code.call_args_list) == (not online)
