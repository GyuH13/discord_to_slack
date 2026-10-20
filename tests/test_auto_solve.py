import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import yaml

from discord_forum_slack import auto_solve, discord_bot
from discord_forum_slack.config import AutoSolveConfig, Config, load_config

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


async def messages(values):
    for value in values:
        yield value


def message(ident=200, days=14, author=100, bot=False, system=False, webhook=None):
    return SimpleNamespace(
        id=ident,
        created_at=NOW - timedelta(days=days),
        author=SimpleNamespace(id=author, bot=bot),
        webhook_id=webhook,
        is_system=lambda: system,
    )


class AutoSolveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = AutoSolveConfig(enabled=True)
        self.product = SimpleNamespace(id=11, name="dynamixel")
        self.handling = SimpleNamespace(id=12, name="🟡Handling")
        self.solved = SimpleNamespace(id=13, name="✅Solved")
        self.role = SimpleNamespace(id=14, name="robotis")
        self.guild = SimpleNamespace(
            id=1,
            fetch_roles=AsyncMock(return_value=[self.role]),
            fetch_member=AsyncMock(return_value=SimpleNamespace(roles=[self.role])),
        )
        self.forum = Mock(spec=discord.ForumChannel)
        self.forum.id = 10
        self.forum.name = "support"
        self.forum.guild = self.guild
        self.forum.available_tags = [self.product, self.handling, self.solved]
        self.thread = Mock(spec=discord.Thread)
        self.thread.id = 20
        self.thread.parent_id = 10
        self.thread.parent = self.forum
        self.thread.guild = self.guild
        self.thread.name = "question"
        self.thread.created_at = NOW - timedelta(days=30)
        self.thread.archived = False
        self.thread.applied_tags = [self.product, self.handling]
        self.history = [message()]
        self.thread.history.side_effect = lambda **kwargs: messages(self.history)

        async def edit(**kwargs):
            for key, value in kwargs.items():
                if key != "reason":
                    setattr(self.thread, key, value)
            return self.thread

        self.thread.edit = AsyncMock(side_effect=edit)
        self.client = Mock()
        self.client.fetch_channel = AsyncMock(
            side_effect=lambda ident: self.forum if ident == 10 else self.thread
        )
        self.config = Config(
            discord_token="test",
            slack_channel_id="channel",
            forum_channel_ids=["10"],
            slack_bot_token="test",
            slack_app_token="test",
            slack_user_token="test",
            list_id="list",
            auto_solve=self.settings,
        )

    async def solve(self, threads=None):
        return await auto_solve.auto_solve_threads(
            self.client,
            [self.thread] if threads is None else threads,
            self.settings,
            set(discord_bot.STATUS_LABELS),
            ["10"],
            now=NOW,
        )

    async def test_exactly_fourteen_days_resolves_and_keeps_product_tags(self):
        await self.solve()
        self.assertEqual(self.thread.applied_tags, [self.product, self.solved])
        self.assertEqual(self.thread.edit.await_count, 1)
        self.guild.fetch_member.assert_awaited_with(100)

    async def test_recent_reply_and_original_post_are_not_resolved(self):
        for msg in (message(days=13.99), message(ident=20), message(ident=20, days=30)):
            self.history = [msg]
            await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_customer_last_reply_prevents_resolving_even_if_old(self):
        self.guild.fetch_member.return_value = SimpleNamespace(roles=[])
        await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_bot_webhook_and_system_messages_do_not_reset_human_timer(self):
        self.history = [
            message(201, 0, bot=True),
            message(202, 0, webhook=3),
            message(203, 0, system=True),
            message(),
        ]
        await self.solve()
        self.assertEqual(self.thread.applied_tags, [self.product, self.solved])

    async def test_empty_or_bot_only_thread_is_skipped(self):
        for history in ([], [message(bot=True)]):
            self.history = history
            await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_new_reply_during_recheck_is_not_resolved(self):
        self.thread.history.side_effect = iter(
            [
                messages([message()]),
                messages([message(201, 0)]),
            ]
        )
        await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_archived_thread_is_unarchived_tagged_and_restored(self):
        self.thread.archived = True
        await self.solve()
        calls = [c.kwargs for c in self.thread.edit.await_args_list]
        self.assertEqual(calls[0]["archived"], False)
        self.assertEqual(calls[1]["applied_tags"], [self.product, self.solved])
        self.assertEqual(calls[2]["archived"], True)
        self.assertTrue(self.thread.archived)

    async def test_new_reply_after_unarchive_stays_open_and_unsolved(self):
        self.thread.archived = True
        self.thread.history.side_effect = iter(
            [
                messages([message()]),
                messages([message()]),
                messages([message(201, 0)]),
            ]
        )
        await self.solve()
        self.assertFalse(self.thread.archived)
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])
        self.assertEqual(self.thread.edit.await_count, 1)

    async def test_role_removed_during_check_is_not_resolved(self):
        self.guild.fetch_member.side_effect = [
            SimpleNamespace(roles=[self.role]),
            SimpleNamespace(roles=[]),
        ]
        await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_read_failure_skips_thread(self):
        self.thread.history.side_effect = discord.DiscordException("history unavailable")
        with self.assertLogs(auto_solve.logger, level="ERROR"):
            await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_member_failure_skips_thread(self):
        self.guild.fetch_member.side_effect = discord.DiscordException("member unavailable")
        with self.assertLogs(auto_solve.logger, level="ERROR"):
            await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_failed_tag_edit_restores_archive(self):
        self.thread.archived = True
        normal_edit = self.thread.edit.side_effect

        async def edit(**kwargs):
            if "applied_tags" in kwargs:
                raise discord.DiscordException("forbidden")
            return await normal_edit(**kwargs)

        self.thread.edit.side_effect = edit
        with self.assertLogs(auto_solve.logger, level="ERROR"):
            await self.solve()
        self.assertTrue(self.thread.archived)
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])

    async def test_solved_other_forum_and_disabled_are_not_touched(self):
        self.thread.applied_tags = [self.solved]
        await self.solve()
        self.thread.applied_tags = [self.handling]
        self.thread.parent_id = 99
        await self.solve()
        self.settings.enabled = False
        self.client.fetch_channel.reset_mock()
        await self.solve()
        self.client.fetch_channel.assert_not_awaited()
        self.thread.history.assert_not_called()
        self.thread.edit.assert_not_awaited()

    async def test_missing_or_ambiguous_role_or_tag_is_skipped(self):
        for roles, tags in (
            ([], [self.solved]),
            ([self.role, self.role], [self.solved]),
            ([self.role], []),
            ([self.role], [self.solved, self.solved]),
        ):
            self.guild.fetch_roles.return_value = roles
            self.forum.available_tags = tags
            with self.assertLogs(auto_solve.logger, level="ERROR"):
                await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_explicit_ids_allow_renamed_entities(self):
        self.settings.forum_channel_id = "10"
        self.settings.robotis_role_id = "14"
        self.settings.solved_tag_id = "13"
        self.forum.name = "renamed"
        self.role.name = "staff"
        self.solved.name = "Resolved"
        await self.solve()
        self.assertEqual(self.thread.applied_tags, [self.product, self.solved])

    async def test_five_nonstatus_tags_are_not_discarded(self):
        self.thread.applied_tags = [
            SimpleNamespace(id=100 + i, name=f"product{i}") for i in range(5)
        ]
        with self.assertLogs(auto_solve.logger, level="WARNING"):
            await self.solve()
        self.thread.edit.assert_not_awaited()

    async def test_sync_sees_new_solved_status_and_rebuilds_slack_once(self):
        with (
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
            patch.object(discord_bot, "_fetch_threads", return_value=[self.thread]),
            patch.object(auto_solve, "datetime") as clock,
            patch.object(discord_bot, "sync_list") as sync,
        ):
            clock.now.return_value = NOW
            count = await discord_bot.run_sync_list(self.config, self.client)
        self.assertEqual(count, 0)
        sync.assert_called_once()
        self.assertEqual(sync.call_args.kwargs["threads"], [])
        self.assertEqual(sync.call_args.kwargs["status_counts"]["solved"], 1)

    async def test_read_only_snapshot_does_not_edit_tags(self):
        with (
            patch.object(discord_bot, "_fetch_threads", return_value=[self.thread]),
            patch.object(discord_bot.store, "get_link", return_value=None),
        ):
            result = await discord_bot.get_open_issues_and_counts(self.client, self.config)
        self.thread.edit.assert_not_awaited()
        self.assertEqual(len(result[0]), 1)

    async def test_new_post_triggers_common_sync_without_user_token(self):
        self.config.slack_bot_token = ""
        self.config.slack_user_token = ""
        with (
            patch.object(discord_bot, "_fetch_author", return_value="Author"),
            patch.object(discord_bot, "run_sync_list") as sync,
        ):
            await discord_bot._handle_new_thread(self.thread, self.config, self.client)
        sync.assert_awaited_once_with(self.config, self.client)

    async def test_ambiguous_forum_name_skips_all_edits(self):
        other = Mock(spec=discord.ForumChannel)
        other.name = "Support"
        self.client.fetch_channel.side_effect = lambda ident: self.forum if ident == 10 else other
        with self.assertLogs(auto_solve.logger, level="ERROR"):
            await auto_solve.auto_solve_threads(
                self.client,
                [self.thread],
                self.settings,
                set(discord_bot.STATUS_LABELS),
                ["10", "99"],
                now=NOW,
            )
        self.thread.edit.assert_not_awaited()


class ConfigTests(unittest.TestCase):
    def load(self, extra):
        data = dict(
            discord_token="test",
            slack_bot_token="test",
            slack_app_token="test",
            slack_channel_id="test",
            list_id="test",
            forum_channel_ids=["10"],
            **extra,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(data))
            return load_config(path)

    def test_existing_config_stays_compatible_and_name_lookup_needs_no_ids(self):
        self.assertFalse(self.load({}).auto_solve.enabled)
        cfg = self.load({"auto_solve": {"enabled": True}})
        self.assertEqual(cfg.auto_solve.forum_channel_name, "support")
        self.assertEqual(cfg.auto_solve.robotis_role_name, "ROBOTIS")
        self.assertEqual(cfg.auto_solve.inactive_days, 14)

    def test_invalid_config_fails_before_bot_starts(self):
        for extra in (
            "true",
            {"enabled": "false"},
            {"inactive_days": 0},
            {"inactive_days": True},
            {"enabled": True, "robotis_role_id": "abc"},
            {"enabled": True, "forum_channel_id": "99"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.load({"auto_solve": extra})


if __name__ == "__main__":
    unittest.main()
