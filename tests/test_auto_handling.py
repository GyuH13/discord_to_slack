import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from discord_forum_slack import auto_handling, discord_bot, weekly_report
from discord_forum_slack.config import Config, DiscordConfig


async def messages(values):
    for value in values:
        yield value


class AutoHandlingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Config(discord=DiscordConfig(token="test", forum_channel_ids=["10"]))
        self.new = SimpleNamespace(id=1, name="🟢New")
        self.handling = SimpleNamespace(id=2, name="🟡Handling")
        self.product = SimpleNamespace(id=3, name="dynamixel")
        self.solved = SimpleNamespace(id=4, name="✅Solved")
        self.role = SimpleNamespace(id=5, name="ROBOTIS")
        self.guild = SimpleNamespace(
            id=100,
            fetch_roles=AsyncMock(return_value=[self.role]),
            fetch_member=AsyncMock(return_value=SimpleNamespace(roles=[self.role])),
        )
        self.forum = Mock(spec=discord.ForumChannel)
        self.forum.id = 10
        self.forum.guild = self.guild
        self.forum.available_tags = [self.new, self.handling, self.product, self.solved]
        self.thread = Mock(spec=discord.Thread)
        self.thread.id = 20
        self.thread.name = "staff answered"
        self.thread.parent_id = 10
        self.thread.parent = self.forum
        self.thread.guild = self.guild
        self.thread.archived = False
        self.thread.created_at = datetime.now(timezone.utc)
        self.thread.applied_tags = [self.new, self.product]
        self.reply = SimpleNamespace(
            id=21,
            author=SimpleNamespace(id=30, bot=False),
            webhook_id=None,
            is_system=lambda: False,
            channel=self.thread,
        )
        self.history = [self.reply]
        self.thread.history.side_effect = lambda **kw: messages(self.history)
        self.thread.fetch_message = AsyncMock(return_value=self.reply)

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

    async def handle(self):
        return await auto_handling.auto_handle_threads(
            self.client, [self.thread], self.config.auto_solve, ["10"]
        )

    async def test_staff_reply_replaces_new_preserves_product_and_is_idempotent(self):
        await self.handle()
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])
        await self.handle()
        self.assertEqual(self.thread.edit.await_count, 1)

    async def test_customer_reply_starter_and_bot_only_do_not_change_tag(self):
        self.guild.fetch_member.return_value = SimpleNamespace(roles=[])
        await self.handle()
        self.guild.fetch_member.return_value = SimpleNamespace(roles=[self.role])
        self.reply.id = self.thread.id
        await self.handle()
        self.reply.id = 21
        self.reply.author.bot = True
        await self.handle()
        self.thread.edit.assert_not_awaited()

    async def test_solved_handling_and_other_forum_are_untouched(self):
        for tags in ([self.solved], [self.handling]):
            self.thread.applied_tags = tags
            await self.handle()
        self.thread.applied_tags = [self.new]
        self.thread.parent_id = 99
        await self.handle()
        self.thread.edit.assert_not_awaited()
        self.thread.history.assert_not_called()

    async def test_archived_thread_restored_even_if_tag_write_fails(self):
        self.thread.archived = True
        edit = self.thread.edit.side_effect

        async def fail_tag(**kwargs):
            if "applied_tags" in kwargs:
                raise discord.DiscordException("forbidden")
            return await edit(**kwargs)

        self.thread.edit.side_effect = fail_tag
        with self.assertLogs(auto_handling.logger, level="ERROR"):
            await self.handle()
        self.assertTrue(self.thread.archived)
        self.assertEqual(self.thread.applied_tags, [self.new, self.product])

    async def test_archived_thread_success_keeps_archive(self):
        self.thread.archived = True
        await self.handle()
        self.assertTrue(self.thread.archived)
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])

    async def test_removed_role_prevents_change(self):
        self.guild.fetch_member.side_effect = [
            SimpleNamespace(roles=[self.role]),
            SimpleNamespace(roles=[]),
        ]
        await self.handle()
        self.thread.edit.assert_not_awaited()

    async def test_deleted_staff_reply_prevents_change(self):
        missing = discord.NotFound(Mock(status=404, reason="Not Found"), "deleted")
        for responses in ([missing], [self.reply, missing]):
            self.thread.fetch_message.side_effect = responses
            await self.handle()
        self.thread.edit.assert_not_awaited()

    def customer_last(self):
        customer = SimpleNamespace(
            id=22,
            author=SimpleNamespace(id=31, bot=False),
            webhook_id=None,
            is_system=lambda: False,
            channel=self.thread,
        )
        self.guild.fetch_member.side_effect = lambda author_id: SimpleNamespace(
            roles=[self.role] if author_id == self.reply.author.id else []
        )
        self.history = [customer, self.reply]
        return customer

    async def test_customer_last_with_older_staff_reply_becomes_handling(self):
        self.customer_last()
        await self.handle()
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])

    async def test_scans_beyond_recent_messages_and_caches_customer_role_lookup(self):
        customer = self.customer_last()
        self.history = [customer] * 150 + [self.reply]
        await self.handle()
        self.thread.history.assert_called_once_with(limit=None, oldest_first=False)
        queried = [call.args[0] for call in self.guild.fetch_member.await_args_list]
        self.assertEqual(queried.count(customer.author.id), 1)
        self.assertIn(self.handling, self.thread.applied_tags)

    async def test_staff_starter_and_automated_messages_do_not_count_as_answers(self):
        customer = self.customer_last()
        starter = SimpleNamespace(**{**vars(self.reply), "id": self.thread.id})
        bot = SimpleNamespace(**{**vars(self.reply), "author": SimpleNamespace(id=30, bot=True)})
        webhook = SimpleNamespace(**{**vars(self.reply), "webhook_id": 9})
        system = SimpleNamespace(**{**vars(self.reply), "is_system": lambda: True})
        self.history = [customer, bot, webhook, system, starter]
        await self.handle()
        self.thread.edit.assert_not_awaited()

    async def test_departed_customer_does_not_hide_an_older_staff_reply(self):
        self.customer_last()
        self.guild.fetch_member.side_effect = [
            discord.NotFound(Mock(status=404, reason="Not Found"), "left guild"),
            SimpleNamespace(roles=[self.role]),
            SimpleNamespace(roles=[self.role]),
        ]
        await self.handle()
        self.assertIn(self.handling, self.thread.applied_tags)

    async def test_missing_handling_or_failed_role_read_keeps_new(self):
        self.forum.available_tags = []
        with self.assertLogs(auto_handling.logger, level="ERROR"):
            await self.handle()
        self.forum.available_tags = [self.handling]
        self.guild.fetch_member.side_effect = discord.DiscordException("no access")
        with self.assertLogs(auto_handling.logger, level="ERROR"):
            await self.handle()
        self.thread.edit.assert_not_awaited()

    async def test_explicit_role_id_and_no_solved_tag_required(self):
        self.config.auto_solve.robotis_role_id = str(self.role.id)
        self.role.name = "renamed"
        self.forum.available_tags = [self.handling]
        await self.handle()
        self.assertIn(self.handling, self.thread.applied_tags)

    async def test_new_customer_message_after_unarchive_still_handles_and_leaves_thread_open(self):
        self.thread.archived = True
        customer = self.customer_last()
        self.thread.history.side_effect = [
            messages([self.reply]),
            messages([self.reply]),
            messages([customer, self.reply]),
        ]
        await self.handle()
        self.assertFalse(self.thread.archived)
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])

    async def test_five_other_tags_are_not_discarded(self):
        self.thread.applied_tags = [SimpleNamespace(id=i, name=f"tag{i}") for i in range(5)]
        with self.assertLogs(auto_handling.logger, level="WARNING"):
            await self.handle()
        self.thread.edit.assert_not_awaited()

    async def test_other_configured_forum_uses_its_own_handling_tag(self):
        self.thread.parent_id = self.forum.id = 40
        self.handling = SimpleNamespace(id=42, name="Handling")
        self.forum.available_tags = [self.handling]
        self.client.fetch_channel.side_effect = lambda ident: (
            self.forum if ident == 40 else self.thread
        )
        await auto_handling.auto_handle_threads(
            self.client, [self.thread], self.config.auto_solve, ["10", "40"]
        )
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])

    async def test_report_updates_counts_and_excludes_staff_answered_issue(self):
        self.customer_last()
        with (
            patch.object(discord_bot, "_fetch_threads", return_value=[self.thread]),
            patch.object(discord_bot.store, "get_link", return_value=None),
            patch.object(weekly_report, "read_assignments", return_value={}),
            patch.object(weekly_report, "resolve_names", return_value={}),
            patch.object(discord_bot, "sync_list") as sync,
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
        ):
            parts = await weekly_report.collect_messages(
                self.config, self.client, datetime.now(timezone.utc)
            )
        text = "\n".join(parts)
        self.assertIn("Handling: 1개", text)
        self.assertIn("New Issue: 0개", text)
        self.assertNotIn(self.thread.name, text)
        self.assertEqual(self.thread.applied_tags, [self.product, self.handling])
        self.assertEqual(sync.call_args.kwargs["threads"][0]["status"], "Handling")

    async def test_reply_event_updates_tag_and_requests_table_sync(self):
        customer = self.customer_last()
        with (
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
            patch.object(discord_bot, "run_sync_list") as sync,
        ):
            await discord_bot._handle_reply(customer, self.config, self.client)
        sync.assert_awaited_once_with(self.config, self.client)
        self.assertIn(self.handling, self.thread.applied_tags)

    async def test_sync_updates_handling_before_building_slack_rows(self):
        self.customer_last()
        with (
            patch.object(discord_bot, "_fetch_threads", return_value=[self.thread]),
            patch.object(discord_bot.store, "get_link", return_value=None),
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
            patch.object(discord_bot, "sync_list") as sync,
        ):
            await discord_bot.run_sync_list(self.config, self.client)
        self.assertEqual(sync.call_args.kwargs["threads"][0]["status"], "Handling")
        self.assertEqual(sync.call_args.kwargs["status_counts"]["new_issue"], 0)
