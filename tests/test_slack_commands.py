import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from discord_forum_slack import __main__ as main
from discord_forum_slack import slack_bot
from discord_forum_slack import weekly_report as report
from discord_forum_slack.config import Config, SlackConfig, TableSyncConfig, WeeklyReportConfig


def config():
    return Config(
        slack=SlackConfig(bot_token="bot", app_token="app"),
        table_sync=TableSyncConfig(list_id="list"),
        weekly_report=WeeklyReportConfig(enabled=True, channel_id="reports"),
    )


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handlers = {}
        self.app = Mock()

        def register(name):
            def decorate(handler):
                self.handlers[name] = handler
                return handler

            return decorate

        self.app.command.side_effect = register
        self.config = config()
        self.client = Mock()
        self.client.is_ready.return_value = True
        self.ack, self.respond = AsyncMock(), AsyncMock()
        with (
            patch.object(slack_bot, "AsyncApp", return_value=self.app),
            patch.object(slack_bot, "AsyncSocketModeHandler"),
        ):
            slack_bot.create_slack_bot(self.config, self.client)
        self.send_patch = patch.object(slack_bot, "send_manual_report", new_callable=AsyncMock)
        self.send = self.send_patch.start()
        self.sync_patch = patch.object(slack_bot, "run_sync_list", new_callable=AsyncMock)
        self.sync = self.sync_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.addCleanup(self.sync_patch.stop)

    async def invoke(self, name, text=""):
        await self.handlers[name](
            ack=self.ack,
            respond=self.respond,
            command={"text": text, "trigger_id": "request-1", "channel_id": "invocation-channel"},
        )

    async def test_report_command_acks_first_and_delegates_sync_to_report_pipeline(self):
        async def send(*args):
            self.ack.assert_awaited_once()
            return 1

        self.send.side_effect = send
        await self.invoke("/report")
        self.send.assert_awaited_once_with(self.config, self.client, "request-1")
        self.assertIn("<#reports>", self.respond.call_args.args[0])
        self.assertEqual(self.respond.call_args.kwargs["response_type"], "ephemeral")
        self.assertTrue(self.respond.call_args.kwargs["replace_original"])
        self.sync.assert_not_awaited()

    async def test_original_table_update_still_syncs(self):
        self.sync.return_value = 39
        await self.invoke("/table_update")
        self.sync.assert_awaited_once_with(self.config, self.client)
        self.send.assert_not_awaited()
        self.assertEqual(self.respond.call_args.kwargs["response_type"], "ephemeral")
        self.assertTrue(self.respond.call_args.kwargs["replace_original"])

    async def test_not_ready_or_reporting_disabled_never_sends(self):
        self.client.is_ready.return_value = False
        await self.invoke("/report")
        self.client.is_ready.return_value = True
        self.config.weekly_report.enabled = False
        await self.invoke("/report")
        self.send.assert_not_awaited()

    async def test_unknown_arguments_do_not_trigger_sync_or_report(self):
        await self.invoke("/table_update", "typo")
        await self.invoke("/table_update", " report ")
        self.assertIn("/report를 사용", self.respond.call_args.args[0])
        await self.invoke("/report", "unexpected")
        self.send.assert_not_awaited()
        self.sync.assert_not_awaited()

    async def test_report_rejects_disabled_table_sync(self):
        self.config.table_sync.enabled = False
        await self.invoke("/report")
        self.send.assert_not_awaited()
        self.assertIn("table_sync.enabled", self.respond.call_args.args[0])
        await self.invoke("/table_update")
        self.sync.assert_not_awaited()

    async def test_busy_and_partial_failure_are_reported(self):
        self.send.side_effect = report.ReportBusyError("이미 발송 중")
        await self.invoke("/report")
        self.assertIn("발송 중", self.respond.call_args.args[0])
        self.send.side_effect = RuntimeError("test")
        with self.assertLogs(slack_bot.logger, level="ERROR"):
            await self.invoke("/report")
        self.assertIn("일부 메시지가 전송됐을 수", self.respond.call_args.args[0])

    async def test_socket_mode_for_weekly_only_when_app_token_exists(self):
        c = config()
        c.table_sync.enabled = False
        client = Mock(start=AsyncMock())
        handler = Mock(start_async=AsyncMock())
        with (
            patch.object(main, "create_discord_bot", return_value=client),
            patch.object(main, "create_slack_bot", return_value=handler) as create,
            patch.object(main, "run_weekly_report", new_callable=AsyncMock),
        ):
            await main._run(c)
            create.assert_called_once_with(c, client)
            c.slack.app_token = ""
            await main._run(c)
            self.assertEqual(create.call_count, 1)


class ManualDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "weekly_report.json"
        self.path.write_text('{"existing": "automatic schedule is unchanged"}')
        self.original = self.path.read_bytes()
        self.config = config()
        self.sender = Mock()
        self.sender.chat_postMessage.side_effect = lambda **kwargs: {"ts": "1.1"}
        patches = [
            patch.object(report, "STATE_PATH", self.path),
            patch.object(report, "_manual_lock", asyncio.Lock()),
            patch.object(report, "_client", return_value=self.sender),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.collect_patch = patch.object(report, "collect_messages", new_callable=AsyncMock)
        self.collect = self.collect_patch.start()
        self.collect.return_value = ["summary", "new issues"]
        self.addCleanup(self.collect_patch.stop)

    async def test_retried_command_does_not_duplicate_and_preserves_weekly_schedule(self):
        for _ in range(2):
            self.assertEqual(await report.send_manual_report(self.config, Mock(), "req1"), 2)
        self.assertEqual(self.sender.chat_postMessage.call_count, 2)
        calls = self.sender.chat_postMessage.call_args_list
        self.assertIsNone(calls[0].kwargs["thread_ts"])
        self.assertEqual(calls[1].kwargs["thread_ts"], "1.1")
        self.assertTrue(all(call.kwargs["channel"] == "reports" for call in calls))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.collect.assert_awaited_once()

    async def test_new_explicit_command_can_send_another_report(self):
        await report.send_manual_report(self.config, Mock(), "req1")
        await report.send_manual_report(self.config, Mock(), "req2")
        self.assertEqual(self.sender.chat_postMessage.call_count, 4)

    async def test_unknown_delivery_result_is_not_retried(self):
        self.sender.chat_postMessage.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await report.send_manual_report(self.config, Mock(), "req1")
        with self.assertRaisesRegex(RuntimeError, "발송 결과 확인 필요"):
            await report.send_manual_report(self.config, Mock(), "req1")
        self.assertEqual(self.sender.chat_postMessage.call_count, 1)
        self.assertEqual(self.path.read_bytes(), self.original)

    async def test_concurrent_manual_request_is_rejected_while_first_collects(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def collect(*args):
            started.set()
            await finish.wait()
            return ["summary"]

        self.collect.side_effect = collect
        task = asyncio.create_task(report.send_manual_report(self.config, Mock(), "req1"))
        await started.wait()
        try:
            with self.assertRaises(report.ReportBusyError):
                await report.send_manual_report(self.config, Mock(), "req2")
        finally:
            finish.set()
            await task
        self.assertEqual(self.sender.chat_postMessage.call_count, 1)

    async def test_missing_request_id_does_not_send(self):
        with self.assertRaises(ValueError):
            await report.send_manual_report(self.config, Mock(), "")
        self.sender.chat_postMessage.assert_not_called()
