import unittest
from unittest.mock import Mock, patch

import pandas as pd
import cloud_batch_booking as cloud
import cloud_batch_booking_ui as ui
import selected_row_status_guard as guard


class CloudCandidatesTest(unittest.TestCase):
    def setUp(self):
        base = dict(zip(cloud.batch_opt.REQUIRED_COLUMNS, [''] * len(cloud.batch_opt.REQUIRED_COLUMNS)))
        base.update({'姓名': '測試', '電話': '0900000000', '地址': '台北市大安區測試路1號',
                     '日期': '2026-10-04', '開始時間': '09:00', '結束時間': '13:00',
                     '服務人時': '8', '狀態': '未安排', '原因': '無班表', '結果': ''})
        self.df = pd.DataFrame([
            {**base, '__sheet_row__': 2},
            {**base, '__sheet_row__': 3, '結果': ' 失敗 '},
            {**base, '__sheet_row__': 4, '結果': '失敗', '訂單編號': 'TT123', '狀態': '已安排'},
            {**base, '__sheet_row__': 5, '結果': '失敗', '狀態': '已安排'},
        ])
        loader = patch.object(guard, '_load_worksheet_unique', return_value=(Mock(), self.df))
        loader.start()
        self.addCleanup(loader.stop)

    def test_failed_rows_skip_and_clear_result_reenables(self):
        self.assertEqual([x[0] for x in cloud.load_pending('sheet')], [2])
        self.df.loc[self.df['__sheet_row__'].eq(3), '結果'] = ''
        self.assertEqual([x[0] for x in cloud.load_pending('sheet')], [2, 3])

    def test_auto_lemon_exception_applies_to_all_filter_modes(self):
        self.df.loc[self.df['__sheet_row__'].eq(3), '原因'] = '無班表；找不到訂單編號'
        for mode in ('all', 'no_schedule', 'missing_order', 'both'):
            with self.subTest(mode=mode):
                self.assertIn(3, [x[0] for x in cloud.load_pending('sheet', filter_mode=mode, allow_auto_lemon=True)])
                self.assertNotIn(3, [x[0] for x in cloud.load_pending('sheet', filter_mode=mode)])

    def test_existing_order_retained_for_followup_but_not_pending(self):
        self.assertIn(4, guard._safe_load_candidates(cloud.batch_opt, 'sheet')['__sheet_row__'].tolist())
        self.assertNotIn(4, [x[0] for x in cloud.load_pending('sheet', allow_auto_lemon=True)])
        self.assertNotIn(5, [x[0] for x in cloud.load_pending('sheet', allow_auto_lemon=True)])

    def test_runner_forwards_exception_to_pending_and_processing(self):
        account = {'email': 'test', 'password': 'test'}
        with patch.object(cloud, 'load_pending', side_effect=[[(3, '台北', '')], [], []]) as pending, \
             patch.dict(cloud.ACCOUNTS, {'台北': account}), \
             patch.object(cloud, 'run_process_web_hybrid', return_value={'success_count': 1}) as runner:
            self.assertEqual(cloud.run('sheet', pause_seconds=0, allow_auto_lemon=True), 0)
        self.assertTrue(all(call.args[-1] is True for call in pending.call_args_list))
        self.assertTrue(runner.call_args.kwargs['allow_auto_lemon_shift'])
        self.assertEqual(runner.call_args.kwargs['selected_rows'], [3])


class CloudStatusTest(unittest.TestCase):
    def setUp(self):
        self.st = Mock()
        self.st.session_state = {}
        self.run = {'id': 1, 'run_number': 1, 'run_attempt': 1, 'html_url': 'https://example.invalid/log',
                    'status': 'completed', 'conclusion': 'failure'}
        for patcher in (patch.object(ui, 'st', self.st), patch.object(ui, '_token', return_value='token'),
                        patch.object(ui, '_latest_run', return_value=self.run)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self):
        ui._render_cloud_status.__wrapped__()

    def test_partial_success_summary_cached_per_attempt(self):
        with patch.object(ui, '_run_summary', return_value=(10, 8, 2, 3)) as summary:
            self.render()
            self.render()
            summary.assert_called_once_with(1)
            self.run['run_attempt'] = 2
            self.render()
            self.assertEqual(summary.call_count, 2)
        self.st.warning.assert_called_with('已執行完成：處理 10 列，成功 8 列，失敗 2 列，尚待處理 3 列。')

    def test_startup_failure_does_not_invent_counts(self):
        with patch.object(ui, '_run_summary', return_value=None):
            self.render()
        self.st.error.assert_called_once()
        self.assertNotIn('列', self.st.error.call_args.args[0])
        self.assertFalse(self.st.session_state)

    def test_cancelled_summary_preserves_cancelled_status(self):
        self.run['conclusion'] = 'cancelled'
        with patch.object(ui, '_run_summary', return_value=(10, 10, 0, 2)):
            self.render()
        self.assertIn('已取消', self.st.warning.call_args.args[0])
        self.st.success.assert_not_called()

    def test_active_states_do_not_fetch_finish(self):
        for status in ('queued', 'in_progress'):
            self.run['status'] = status
            with patch.object(ui, '_run_summary') as summary, patch.object(ui, '_render_running_steps') as steps:
                self.render()
                summary.assert_not_called()
                if status == 'in_progress':
                    steps.assert_called_once_with(1)
            self.assertIn('15 秒', self.st.info.call_args.args[0])

    def test_pending_dispatch_does_not_show_previous_completion(self):
        self.st.session_state['optimized_cloud_dispatch'] = {'previous_run_id': 1, 'requested_at': '2026-10-04T00:00:00+00:00'}
        with patch.object(ui, '_run_summary') as summary:
            self.render()
            summary.assert_not_called()
        self.assertIn('等待雲端建立本次', self.st.info.call_args.args[0])
        self.st.error.assert_not_called()

    def test_new_run_replaces_pending_dispatch(self):
        self.st.session_state['optimized_cloud_dispatch'] = {'previous_run_id': 0, 'requested_at': '2026-10-04T00:00:00+00:00'}
        self.run.update(status='queued', created_at='2026-10-04T00:00:01Z')
        self.render()
        self.assertNotIn('optimized_cloud_dispatch', self.st.session_state)
        self.assertIn('排隊', self.st.info.call_args.args[0])

    def test_missing_token_keeps_status_and_logs_visible(self):
        with patch.object(ui, '_token', return_value=''):
            self.render()
        self.st.warning.assert_called_once()
        self.assertIn('GitHub Token', self.st.warning.call_args.args[0])
        self.assertTrue(any('開啟雲端執行紀錄' in call.args[0] for call in self.st.markdown.call_args_list))

    def test_running_steps_show_actual_stage_without_order_counts(self):
        reply = Mock()
        reply.json.return_value = {'jobs': [{'steps': [
            {'name': 'Set up job', 'status': 'completed'},
            {'name': 'Run optimized cloud batch booking', 'status': 'in_progress'},
        ]}]}
        with patch.object(ui, '_github_get', return_value=reply):
            ui._render_running_steps(1)
        self.st.write.assert_called_once_with('目前步驟：批次成單、回填、寄確認信及同步日曆')
        self.assertTrue(any('1／2' in call.args[0] for call in self.st.caption.call_args_list))

    def test_finish_parser_uses_last_summary(self):
        jobs = Mock()
        jobs.json.return_value = {'jobs': [{'name': 'booking', 'id': 7}]}
        logs = Mock(text='FINISH attempted=2 success=1 fail=1 remaining=5\nFINISH attempted=6 success=5 fail=1 remaining=1')
        with patch.object(ui, '_github_get', side_effect=[jobs, logs]):
            self.assertEqual(ui._run_summary(1), (6, 5, 1, 1))


if __name__ == '__main__':
    unittest.main()
