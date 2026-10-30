import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _agent_help_nodes(tree):
    """render_agent_help 與它呼叫的控制結果 fragment（拿掉 st.fragment 裝飾器，直接同步執行）。"""
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('render_agent_help', 'render_agent_control_results'):
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') in (
                'AGENT_CONTROL_LABELS', '_AGENT_CONTROL_DONE'):
            nodes.append(node)
    return nodes


@pytest.mark.parametrize('owner', ['mac', 'old-hostname', ''])
@pytest.mark.parametrize('online,busy', [(False, False), (False, True), (True, False), (True, True)])
def test_agent_controls_and_cancel_remain_visible(online, busy, owner):
    tree = ast.parse(Path('toolapp.py').read_text())
    function = _agent_help_nodes(tree)
    st = MagicMock()
    st.columns.side_effect = lambda widths: [MagicMock() for _ in range(widths if isinstance(widths, int) else len(widths))]
    st.button.return_value = False
    namespace = {
        'st': st,
        'list_local_agent_status': lambda **kw: [{'agent_id': 'mac', 'online': online, 'actions': 'system.agent_restart,system.agent_status,system.agent_logs,system.git_pull_restart'}],
        'list_local_agent_tasks': lambda **kw: [{'task_id': '1', 'agent_id': owner, 'status': 'running'}] if busy else [],
    }
    exec(compile(ast.Module(body=function, type_ignores=[]), '<agent-help>', 'exec'), namespace)
    namespace['render_agent_help']()
    buttons = {call.args[0]: call.kwargs for call in st.button.call_args_list}
    assert buttons['⏹️ 中止目前工作']['disabled'] == (not busy)
    assert buttons['📊 檢查狀態']['disabled'] == (not online)
    assert buttons['📋 查看 Agent Log']['disabled'] == (not online)
    assert bool(buttons['⬇️ Git Pull 更新程式']['disabled']) == (not online or busy)
    assert buttons['🔄 重啟 Agent']['disabled'] == (not online or busy)
    assert not buttons['← 返回主控台'].get('disabled', False)
    assert any('local_agent_service.sh restart' in str(call) for call in st.code.call_args_list) == (not online)


@pytest.mark.parametrize('session_ids,shown', [([], False), (['pull-now'], True)])
def test_control_results_only_show_tasks_sent_from_this_session(session_ids, shown):
    tree = ast.parse(Path('toolapp.py').read_text())
    function = _agent_help_nodes(tree)
    st = MagicMock()
    st.session_state = {'agent_control_task_ids': session_ids}
    st.columns.side_effect = lambda widths: [MagicMock() for _ in range(widths if isinstance(widths, int) else len(widths))]
    st.button.return_value = False
    old_task = {'task_id': 'pull-old', 'action': 'system.git_pull_restart', 'status': 'completed', 'agent_id': 'mac'}
    new_task = {'task_id': 'pull-now', 'action': 'system.git_pull_restart', 'status': 'completed', 'agent_id': 'mac'}
    namespace = {
        'st': st,
        'list_local_agent_status': lambda **kw: [{'agent_id': 'mac', 'online': True, 'actions': 'system.git_pull_restart'}],
        'list_local_agent_tasks': lambda **kw: [new_task, old_task] if shown else [old_task],
        'read_local_agent_task_log': lambda task_id: f'log of {task_id}',
    }
    exec(compile(ast.Module(body=function, type_ignores=[]), '<agent-help>', 'exec'), namespace)
    namespace['render_agent_help']()
    logs = [str(call) for call in st.code.call_args_list]
    assert not any('log of pull-old' in log for log in logs)
    assert any('log of pull-now' in log for log in logs) == shown


def test_control_results_rerun_page_when_task_finishes_or_running_job_ends():
    tree = ast.parse(Path('toolapp.py').read_text())
    function = [n for n in _agent_help_nodes(tree) if getattr(n, 'name', '') != 'render_agent_help']
    st = MagicMock()
    st.session_state = {'agent_control_task_ids': ['pull-now']}
    tasks = [{'task_id': 'pull-now', 'action': 'system.git_pull_restart', 'status': 'pending'},
             {'task_id': 'job', 'action': 'fubon.internal_payment', 'status': 'running'}]
    namespace = {'st': st, 'list_local_agent_tasks': lambda **kw: tasks,
                 'read_local_agent_task_log': lambda task_id: ''}
    exec(compile(ast.Module(body=function, type_ignores=[]), '<agent-control>', 'exec'), namespace)
    render = namespace['render_agent_control_results']
    actions = tuple(namespace['AGENT_CONTROL_LABELS'])

    render(actions, ('job',))
    st.rerun.assert_not_called()  # 還在等待／執行中：只刷新卡片
    tasks[0]['status'] = 'completed'
    render(actions, ('job',))
    st.rerun.assert_called_once_with(scope='app')  # 更新＋重啟完成：整頁重跑
    st.rerun.reset_mock()
    render(actions, ('job',))
    st.rerun.assert_not_called()  # 已處理過的完成不重複重跑
    tasks[1]['status'] = 'cancelled'
    render(actions, ('job',))
    st.rerun.assert_called_once_with(scope='app')  # 執行中的工作結束：按鈕要解鎖


@pytest.mark.parametrize('status,locked', [('running', True), ('cancel_requested', False)])
def test_update_buttons_unlock_once_cancel_is_requested(status, locked):
    tree = ast.parse(Path('toolapp.py').read_text())
    st = MagicMock()
    st.session_state = {}
    st.columns.side_effect = lambda widths: [MagicMock() for _ in range(widths if isinstance(widths, int) else len(widths))]
    st.button.return_value = False
    namespace = {
        'st': st,
        'list_local_agent_status': lambda **kw: [{'agent_id': 'mac', 'online': True,
                                                  'actions': 'system.agent_restart,system.git_pull_restart'}],
        'list_local_agent_tasks': lambda **kw: [{'task_id': '1', 'agent_id': 'mac', 'status': status}],
    }
    exec(compile(ast.Module(body=_agent_help_nodes(tree), type_ignores=[]), '<agent-help>', 'exec'), namespace)
    namespace['render_agent_help']()
    buttons = {call.args[0]: call.kwargs for call in st.button.call_args_list}
    assert bool(buttons['⬇️ 更新程式＋重啟 Agent']['disabled']) == locked
    assert bool(buttons['🔄 重啟 Agent']['disabled']) == locked
    assert bool(buttons['⬇️ Git Pull 更新程式']['disabled']) == locked
    assert buttons['⏹️ 中止目前工作']['disabled'] == (not locked)  # 已送出中止就不能再按
