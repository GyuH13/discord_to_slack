import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import discord
from slack_sdk.errors import SlackApiError

from discord_forum_slack import discord_bot
from discord_forum_slack import weekly_report as report
from discord_forum_slack.config import (
    Config,
    DiscordConfig,
    NotificationsConfig,
    SlackConfig,
    TableSyncConfig,
    WeeklyReportConfig,
    parse_config,
)

KST = ZoneInfo("Asia/Seoul")
DUE = datetime(2026, 10, 26, 10, tzinfo=KST)


def config():
    return Config(
        discord=DiscordConfig(token="discord", forum_channel_ids=["1"]),
        slack=SlackConfig(bot_token="bot", app_token="app", user_token="user"),
        notifications=NotificationsConfig(enabled=False, channel_id="channel"),
        table_sync=TableSyncConfig(enabled=True, list_id="list"),
        weekly_report=WeeklyReportConfig(enabled=True),
    )


def issue(ident, status="New Issue", title="문의"):
    return {
        "title": title,
        "url": f"https://discord.com/channels/1/{ident}",
        "created_at": DUE - timedelta(days=ident),
        "status": status,
    }


def api_error(error="ratelimited", status=429):
    class Response(dict):
        status_code = status

    return SlackApiError(error, Response(error=error, ok=False))


class WeeklyConfigTests(unittest.TestCase):
    def test_weekly_requires_table_sync_and_its_credentials(self):
        config().validate()
        for section, key in (
            ("slack", "bot_token"),
            ("slack", "app_token"),
            ("table_sync", "list_id"),
            ("notifications", "channel_id"),
        ):
            c = config()
            setattr(getattr(c, section), key, "")
            with self.assertRaises(ValueError):
                c.validate()
        c = config()
        c.notifications.channel_id = ""
        c.weekly_report.channel_id = "weekly-channel"
        c.validate()
        c.table_sync.enabled = False
        with self.assertRaisesRegex(ValueError, "table_sync.enabled"):
            c.validate()

    def test_bad_schedule_is_rejected(self):
        for key, value in (
            ("enabled", "true"),
            ("weekday", "Monday"),
            ("time", "25:00"),
            ("time", "9:00"),
            ("time", "10:00:00"),
            ("timezone", "bad/zone"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                parse_config(
                    {
                        "discord": {"token": "test", "forum_channel_ids": [1]},
                        "notifications": {"enabled": False},
                        "table_sync": {"enabled": False},
                        "weekly_report": {key: value},
                    }
                )

    def test_existing_config_does_not_start_reporting(self):
        c = parse_config(
            {
                "discord": {"token": "test", "forum_channel_ids": [1]},
                "notifications": {"enabled": False},
                "table_sync": {"enabled": False},
            }
        )
        self.assertFalse(c.weekly_report.enabled)


class ReportContentTests(unittest.TestCase):
    def test_counts_only_current_open_issues_and_counts_each_coassignee_once(self):
        issues = [issue(1), issue(2), issue(3), issue(4)]
        assignments = {
            ("1", "1"): ["U1", "U1", "U2"],
            ("1", "2"): ["U1"],
            ("1", "3"): [],
            ("1", "99"): ["U3"],
        }
        people, assigned, unassigned, unknown = report.assignment_counts(issues, assignments)
        self.assertEqual(dict(people), {"U1": 2, "U2": 1})
        self.assertEqual((assigned, unassigned, unknown), (2, 1, 1))

    def test_message_includes_counts_names_all_new_titles_dates_and_links(self):
        snapshot = discord_bot.IssueSnapshot(
            [issue(1, title="문의 <@everyone> & 링크|테스트"), issue(2, "Handling")],
            {"solved": 421, "handling": 1, "new_issue": 1},
        )
        text = "\n".join(
            report.build_messages(
                snapshot,
                {("1", "1"): ["U1"], ("1", "2"): []},
                {"U1": "담당자"},
                DUE.astimezone(timezone.utc),
                "Asia/Seoul",
            )
        )
        self.assertIn("✅Solved(완료): 421개 · 🟡Handling: 1개 · 🟢New Issue: 1개", text)
        self.assertIn("담당자: 1개", text)
        self.assertIn("미배정 1개", text)
        self.assertIn("2026-10-25 · 답변 상태 확인 불가", text)
        self.assertNotIn("10:00", text)
        self.assertIn("문의 &lt;@everyone&gt; &amp; 링크｜테스트", text)
        self.assertNotIn("https://discord.com/channels/1/2", text)

    def test_reply_age_uses_last_message_and_whole_elapsed_days(self):
        issues = [issue(n) for n in range(1, 5)]
        snapshot = discord_bot.IssueSnapshot(issues, {"solved": 0, "handling": 0, "new_issue": 4})
        activity = {
            issues[0]["url"]: (DUE - timedelta(days=3, hours=23), False),
            issues[1]["url"]: (DUE - timedelta(days=2), True),
            issues[2]["url"]: (DUE - timedelta(hours=23), False),
            issues[3]["url"]: (DUE + timedelta(seconds=1), True),
        }
        text = "\n".join(report.build_messages(snapshot, {}, {}, DUE, "Asia/Seoul", activity))
        self.assertIn("2026-10-25 · 미답변 3일 경과", text)
        self.assertIn("2026-10-24 · 직원 답변 후 2일 경과", text)
        self.assertIn("2026-10-23 · 미답변 1일 미만", text)
        self.assertIn("2026-10-22 · 직원 답변 후 1일 미만", text)
        self.assertNotIn("10:00", text)

    def test_large_new_list_is_split_without_losing_any_issue(self):
        issues = [issue(n, title=f"고객문의 {n} " + "상세" * 40) for n in range(1, 151)]
        snapshot = discord_bot.IssueSnapshot(issues, {"solved": 0, "handling": 0, "new_issue": 150})
        parts = report.build_messages(snapshot, {}, {}, DUE, "Asia/Seoul")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) < 4000 for part in parts))
        text = "\n".join(parts)
        for item in issues:
            self.assertEqual(text.count(item["url"] + "|"), 1)
        self.assertIn("테이블 미반영 150개", text)

    def test_names_use_both_tokens_and_fall_back_to_slack_user_display(self):
        bot = Mock()
        user = Mock()
        bot.users_info.side_effect = api_error("missing_scope", 403)
        user.users_info.side_effect = [
            {"user": {"profile": {"display_name": "담당 & 이름"}}},
            api_error("missing_scope", 403),
        ]
        with patch.object(report, "_client", side_effect=[bot, user]):
            names = report.resolve_names(config(), ["U1", "U2", "U3"])
        self.assertEqual(names, {"U1": "담당 &amp; 이름", "U2": "<@U2>", "U3": "<@U3>"})
        self.assertEqual(bot.users_info.call_count, 1)
        self.assertEqual(user.users_info.call_count, 2)

    def test_read_assignments_deduplicates_rows_and_honors_cleared_cells(self):
        rows = [
            {
                "fields": [
                    {"column_id": "link", "link": [{"originalUrl": issue(1)["url"]}]},
                    {"column_id": "assign", "user": ["U1", "U1"]},
                ]
            },
            {"fields": [{"key": "link", "link": [{"original_url": issue(2)["url"]}]}]},
        ]
        with (
            patch.object(report, "_client"),
            patch.object(
                report,
                "_get_schema",
                return_value=({"thread_link": "link"}, {}, {"id": "assign", "type": "user"}),
            ),
            patch.object(report, "_fetch_items", return_value=rows + rows[:1]),
        ):
            self.assertEqual(
                report.read_assignments(config()), {("1", "1"): ["U1"], ("1", "2"): []}
            )
            rows[0]["fields"][1] = {"column_id": "assign", "value": "unreadable"}
            with self.assertRaises(RuntimeError):
                report.read_assignments(config())

    def test_non_people_assign_is_not_misreported_as_zero(self):
        with (
            patch.object(report, "_client"),
            patch.object(
                report,
                "_get_schema",
                return_value=({"thread_link": "link"}, {}, {"id": "assign", "type": "select"}),
            ),
        ):
            with self.assertRaises(RuntimeError):
                report.read_assignments(config())


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "report.json"
        self.config = config()
        self.client = Mock()
        self.sender = Mock()
        self.sender.chat_postMessage.return_value = {"ts": "100.1"}
        self.client_patch = patch.object(report, "_client", return_value=self.sender)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.collect = patch.object(report, "collect_messages", new_callable=AsyncMock).start()
        self.addCleanup(patch.stopall)
        self.collect.return_value = ["summary", "all new issues"]
        self.reporter = report.WeeklyReporter(self.config, self.client, self.path)
        self.reporter.initialize(DUE - timedelta(days=3))

    def test_next_slot_in_korea_and_no_immediate_midweek_send(self):
        self.assertEqual(
            report.next_slot(DUE - timedelta(seconds=1), self.config.weekly_report), DUE
        )
        self.assertEqual(report.next_slot(DUE, self.config.weekly_report), DUE)
        self.assertEqual(
            report.next_slot(DUE + timedelta(seconds=1), self.config.weekly_report),
            DUE + timedelta(days=7),
        )
        self.assertEqual(DUE.astimezone(timezone.utc).hour, 1)

    async def test_sends_at_due_time_and_does_not_repeat_after_restart(self):
        await self.reporter.tick(DUE - timedelta(seconds=1))
        self.collect.assert_not_awaited()
        await self.reporter.tick(DUE)
        self.assertEqual(self.sender.chat_postMessage.call_count, 2)
        self.assertIsNone(self.sender.chat_postMessage.call_args_list[0].kwargs["thread_ts"])
        self.assertEqual(
            self.sender.chat_postMessage.call_args_list[1].kwargs["thread_ts"], "100.1"
        )
        restarted = report.WeeklyReporter(self.config, self.client, self.path)
        await restarted.tick(DUE + timedelta(hours=1))
        self.assertEqual(self.sender.chat_postMessage.call_count, 2)
        self.assertEqual(
            json.loads(self.path.read_text())["next_due"], (DUE + timedelta(days=7)).isoformat()
        )

    async def test_downtime_catches_up_only_latest_week(self):
        await self.reporter.tick(DUE + timedelta(days=16))
        self.collect.assert_awaited_once()
        self.assertEqual(self.sender.chat_postMessage.call_count, 2)

    async def test_partial_definite_failure_resumes_without_reposting_summary(self):
        self.sender.chat_postMessage.side_effect = [{"ts": "100.1"}, api_error(), {"ts": "100.2"}]
        with self.assertRaises(SlackApiError):
            await self.reporter.tick(DUE)
        restarted = report.WeeklyReporter(self.config, self.client, self.path)
        await restarted.tick(DUE + timedelta(minutes=5))
        texts = [call.kwargs["text"] for call in self.sender.chat_postMessage.call_args_list]
        self.assertEqual(texts, ["summary", "all new issues", "all new issues"])
        self.collect.assert_awaited_once()
        calls = self.sender.chat_postMessage.call_args_list
        self.assertEqual(calls[1].kwargs["client_msg_id"], calls[2].kwargs["client_msg_id"])

    async def test_ambiguous_network_failure_is_not_blindly_retried(self):
        self.sender.chat_postMessage.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.reporter.tick(DUE)
        restarted = report.WeeklyReporter(self.config, self.client, self.path)
        with self.assertRaisesRegex(RuntimeError, "발송 결과 확인 필요"):
            await restarted.tick(DUE + timedelta(minutes=5))
        self.assertEqual(self.sender.chat_postMessage.call_count, 1)

    async def test_failed_collection_does_not_send_or_mark_as_sent(self):
        self.collect.side_effect = RuntimeError("Discord incomplete")
        with self.assertRaises(RuntimeError):
            await self.reporter.tick(DUE)
        self.sender.chat_postMessage.assert_not_called()
        self.assertIsNone(json.loads(self.path.read_text())["pending"])

    async def test_state_write_failure_prevents_posting(self):
        with patch.object(report, "save_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.reporter.tick(DUE)
        self.sender.chat_postMessage.assert_not_called()

    def test_corrupt_state_is_not_reset_and_resent(self):
        self.path.write_text("not json")
        with self.assertRaises(ValueError):
            report.WeeklyReporter(self.config, self.client, self.path).initialize(DUE)


class ReplyActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.staff_role = SimpleNamespace(id=7, name="ROBOTIS")
        self.guild = SimpleNamespace(
            id=1,
            fetch_roles=AsyncMock(return_value=[self.staff_role]),
            fetch_member=AsyncMock(return_value=SimpleNamespace(roles=[])),
        )

    def thread(self, ident, messages):
        async def history(**kwargs):
            for message in messages:
                yield message

        return SimpleNamespace(id=ident, guild=self.guild, history=history)

    def message(self, *, author=10, bot=False, webhook=None, system=False, days=3):
        return SimpleNamespace(
            author=SimpleNamespace(id=author, bot=bot),
            webhook_id=webhook,
            is_system=lambda: system,
            created_at=DUE - timedelta(days=days),
        )

    async def test_ignores_automated_messages_and_caches_role_and_member_reads(self):
        messages = [
            self.message(bot=True, days=0),
            self.message(webhook=4, days=0),
            self.message(system=True, days=0),
            self.message(),
        ]
        snapshot = discord_bot.IssueSnapshot(
            [issue(1), issue(2), issue(3, "Handling")],
            {},
            [self.thread(1, messages), self.thread(2, messages), self.thread(3, messages)],
        )
        result = await report.read_reply_activity(snapshot, config())
        self.assertEqual(
            result, {issue(n)["url"]: (DUE - timedelta(days=3), False) for n in (1, 2)}
        )
        self.guild.fetch_roles.assert_awaited_once()
        self.guild.fetch_member.assert_awaited_once_with(10)

    async def test_explicit_role_id_and_staff_reply_with_auto_solve_disabled(self):
        c = config()
        c.auto_solve.robotis_role_id = "7"
        c.auto_solve.robotis_role_name = "unused name"
        self.guild.fetch_member.return_value = SimpleNamespace(roles=[self.staff_role])
        snapshot = discord_bot.IssueSnapshot([issue(1)], {}, [self.thread(1, [self.message()])])
        result = await report.read_reply_activity(snapshot, c)
        self.assertEqual(result[issue(1)["url"]], (DUE - timedelta(days=3), True))
        self.assertFalse(c.auto_solve.enabled)

    async def test_failed_member_read_does_not_claim_unanswered_and_other_issues_continue(self):
        self.guild.fetch_member.side_effect = [
            discord.DiscordException("unavailable"),
            SimpleNamespace(roles=[]),
        ]
        snapshot = discord_bot.IssueSnapshot(
            [issue(1), issue(2)],
            {},
            [self.thread(1, [self.message()]), self.thread(2, [self.message(author=11)])],
        )
        result = await report.read_reply_activity(snapshot, config())
        self.assertNotIn(issue(1)["url"], result)
        self.assertIn(issue(2)["url"], result)

    async def test_missing_role_or_no_human_message_is_unknown(self):
        snapshot = discord_bot.IssueSnapshot([issue(1)], {}, [self.thread(1, [])])
        self.assertEqual(await report.read_reply_activity(snapshot, config()), {})
        self.guild.fetch_roles.return_value = []
        snapshot.threads = [self.thread(1, [self.message()])]
        self.assertEqual(await report.read_reply_activity(snapshot, config()), {})


class SnapshotSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_waits_then_updates_table_and_uses_same_snapshot(self):
        lock = asyncio.Lock()
        await lock.acquire()
        snapshot = discord_bot.IssueSnapshot([], {"solved": 3, "handling": 0, "new_issue": 0})
        with (
            patch.object(discord_bot, "_sync_lock", lock),
            patch.object(discord_bot, "_get_issue_snapshot", return_value=snapshot) as read,
            patch.object(discord_bot, "sync_list") as sync,
            patch.object(report, "read_assignments", return_value={}),
            patch.object(report, "resolve_names", return_value={}),
        ):
            task = asyncio.create_task(report.collect_messages(config(), Mock(), DUE))
            await asyncio.sleep(0)
            read.assert_not_awaited()
            lock.release()
            await task
            read.assert_awaited_once()
            self.assertEqual(
                read.call_args.kwargs,
                {
                    "apply_auto_solve": True,
                    "apply_handling": True,
                },
            )
            sync.assert_called_once()
            self.assertIs(sync.call_args.kwargs["threads"], snapshot.issues)
            self.assertIs(sync.call_args.kwargs["status_counts"], snapshot.counts)

    async def test_assignment_and_activity_reads_happen_after_sync_under_lock(self):
        lock = asyncio.Lock()
        snapshot = discord_bot.IssueSnapshot(
            [issue(1)],
            {
                "solved": 421,
                "handling": 0,
                "new_issue": 1,
            },
        )
        events = []

        def sync(**kwargs):
            self.assertTrue(lock.locked())
            events.append("sync")

        def assignments(c):
            self.assertTrue(lock.locked())
            self.assertEqual(events, ["sync"])
            events.append("assignments")
            return {("1", "1"): ["U1"]}

        async def activity(value, c):
            self.assertIs(value, snapshot)
            self.assertTrue(lock.locked())
            self.assertEqual(events, ["sync", "assignments"])
            return {issue(1)["url"]: (DUE - timedelta(days=1), False)}

        with (
            patch.object(discord_bot, "_sync_lock", lock),
            patch.object(discord_bot, "_get_issue_snapshot", return_value=snapshot) as read,
            patch.object(discord_bot, "sync_list", side_effect=sync),
            patch.object(report, "read_assignments", side_effect=assignments),
            patch.object(report, "read_reply_activity", side_effect=activity),
            patch.object(report, "resolve_names", return_value={"U1": "담당자"}),
        ):
            text = "\n".join(await report.collect_messages(config(), Mock(), DUE))
        read.assert_awaited_once()
        self.assertIn("담당자: 1개", text)
        self.assertIn("미답변 1일 경과", text)

    async def test_failed_sync_stops_before_report_reads(self):
        with (
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
            patch.object(
                discord_bot,
                "_get_issue_snapshot",
                return_value=discord_bot.IssueSnapshot(
                    [], {"solved": 0, "handling": 0, "new_issue": 0}
                ),
            ),
            patch.object(discord_bot, "sync_list", side_effect=RuntimeError("sync failed")),
            patch.object(report, "read_assignments") as assignments,
            patch.object(report, "read_reply_activity") as activity,
        ):
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                await report.collect_messages(config(), Mock(), DUE)
        assignments.assert_not_called()
        activity.assert_not_awaited()

    async def test_disabled_table_sync_rejects_report_before_discord_edits(self):
        c = config()
        c.table_sync.enabled = False
        with patch.object(discord_bot, "_sync_list_locked") as sync:
            with self.assertRaisesRegex(ValueError, "table_sync.enabled"):
                await report.collect_messages(c, Mock(), DUE)
        sync.assert_not_awaited()

    async def test_weekly_runner_is_wired_into_startup_only_when_enabled(self):
        from discord_forum_slack import __main__ as main

        c = config()
        client = SimpleNamespace(start=AsyncMock())
        with (
            patch.object(main, "create_discord_bot", return_value=client),
            patch.object(main, "create_slack_bot", return_value=Mock(start_async=AsyncMock())),
            patch.object(main, "run_weekly_report", new_callable=AsyncMock) as runner,
        ):
            await main._run(c)
            runner.assert_awaited_once_with(c, client)
