import asyncio
import itertools
import unittest
from unittest.mock import AsyncMock, Mock, patch

from discord_forum_slack import __main__ as main
from discord_forum_slack import discord_bot
from discord_forum_slack.config import config_to_dict, parse_config


class FeatureConfigTests(unittest.TestCase):
    def data(self):
        return {
            "discord": {"token": "discord-secret", "forum_channel_ids": [10, "30"]},
            "slack": {
                "bot_token": "bot-secret",
                "app_token": "app-secret",
                "user_token": "user-secret",
            },
            "notifications": {"enabled": True, "channel_id": "channel"},
            "table_sync": {"enabled": True, "list_id": "list"},
            "auto_solve": {"enabled": False},
        }

    def test_all_feature_combinations_and_required_credentials(self):
        for notify, table, solve in itertools.product((False, True), repeat=3):
            with self.subTest(notifications=notify, table_sync=table, auto_solve=solve):
                data = self.data()
                data["notifications"] = {
                    "enabled": notify,
                    "channel_id": "channel" if notify else "",
                }
                data["table_sync"] = {"enabled": table, "list_id": "list" if table else ""}
                data["auto_solve"]["enabled"] = solve
                data["slack"] = {
                    "bot_token": "bot" if notify or table else "",
                    "app_token": "app" if table else "",
                }
                config = parse_config(data)
                self.assertEqual(config.discord.forum_channel_ids, ["10", "30"])
                for section, key in (
                    ([("slack", "bot_token")] if notify or table else [])
                    + ([("notifications", "channel_id")] if notify else [])
                    + ([("table_sync", "list_id"), ("slack", "app_token")] if table else [])
                ):
                    value = data[section].pop(key)
                    with self.assertRaises(ValueError):
                        parse_config(data)
                    data[section][key] = value

    def test_legacy_nuc_config_migrates_without_changing_values_or_logging_secrets(self):
        data = {
            "discord_token": "discord-secret",
            "forum_channel_ids": [10, 30],
            "slack_bot_token": "bot-secret",
            "slack_app_token": "app-secret",
            "slack_user_token": "user-secret",
            "slack_channel_id": "channel",
            "list_id": "list",
            "trigger_webhook_url": "webhook-secret",
        }
        with self.assertLogs("discord_forum_slack.config", level="WARNING") as logs:
            config = parse_config(data)
        self.assertNotIn("secret", " ".join(logs.output))
        exported = config_to_dict(config)
        self.assertEqual(config, parse_config(exported))
        self.assertEqual(config, parse_config(self.data()))
        self.assertNotIn("trigger_webhook_url", exported)
        self.assertNotIn("forum_channel_id", exported["auto_solve"])

    def test_legacy_explicit_auto_solve_filter_survives_export(self):
        data = self.data()
        data["auto_solve"] = {"enabled": True, "forum_channel_id": "10", "solved_tag_id": "99"}
        config = parse_config(data)
        self.assertEqual(parse_config(config_to_dict(config)), config)
        self.assertEqual(config.auto_solve.forum_channel_id, "10")

    def test_bad_types_empty_forums_unknown_keys_and_conflicts_are_rejected(self):
        cases = [
            {"discord": []},
            {"slack": None},
            {"notifications": {"enabled": "false"}},
            {"table_sync": {"enabled": 1}},
            {"table_syn": {}},
            {"table_sync": {"list_id": "list", "enable": False}},
            {"discord": {"token": "test", "forum_channel_ids": "10"}},
            {"discord": {"token": "test", "forum_channel_ids": []}},
            {"discord": {"token": "test", "forum_channel_ids": [True]}},
            {"discord": {"token": "test", "forum_channel_ids": ["abc"]}},
            {"discord": {"token": [], "forum_channel_ids": [10]}},
            {"list_id": "conflicting-list"},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_config(self.data() | extra)
        for value in (None, [], "text"):
            with self.assertRaises(ValueError):
                parse_config(value)


class FeatureExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.data = FeatureConfigTests().data()
        self.config = parse_config(self.data)
        self.client = Mock()
        self.thread = Mock()
        self.thread.id = 20
        self.thread.parent.id = 10

    async def test_notification_failure_still_runs_sync(self):
        with (
            patch.object(discord_bot, "_is_forum_thread", return_value=True),
            patch.object(discord_bot, "_fetch_author", return_value="author"),
            patch.object(discord_bot, "_get_tags", return_value=[]),
            patch.object(discord_bot, "post_message", side_effect=RuntimeError("unavailable")),
            patch.object(discord_bot, "run_sync_list", new_callable=AsyncMock) as sync,
            self.assertLogs(discord_bot.logger, level="ERROR"),
        ):
            await discord_bot._handle_new_thread(self.thread, self.config, self.client)
        sync.assert_awaited_once()

    async def test_auto_solve_only_trigger_edits_discord_without_slack_calls(self):
        self.config.notifications.enabled = False
        self.config.table_sync.enabled = False
        self.config.auto_solve.enabled = True
        with (
            patch.object(discord_bot, "_sync_lock", asyncio.Lock()),
            patch.object(discord_bot, "_is_forum_thread", return_value=True),
            patch.object(discord_bot, "_fetch_threads", return_value=[]),
            patch.object(discord_bot, "auto_solve_threads", return_value=[]) as solve,
            patch.object(discord_bot, "post_message") as notify,
            patch.object(discord_bot, "sync_list") as sync,
        ):
            await discord_bot._handle_new_thread(self.thread, self.config, self.client)
        solve.assert_awaited_once()
        notify.assert_not_called()
        sync.assert_not_called()

    async def test_disabled_sync_and_auto_solve_do_not_fetch(self):
        self.config.table_sync.enabled = False
        with patch.object(discord_bot, "_fetch_threads") as fetch:
            self.assertEqual(await discord_bot.run_sync_list(self.config, self.client), 0)
        fetch.assert_not_called()

    async def test_socket_mode_only_starts_when_table_sync_is_enabled(self):
        for enabled in (False, True):
            self.config.table_sync.enabled = enabled
            client = Mock(start=AsyncMock())
            handler = Mock(start_async=AsyncMock())
            with (
                patch.object(main, "create_discord_bot", return_value=client),
                patch.object(main, "create_slack_bot", return_value=handler) as create,
            ):
                await main._run(self.config)
            client.start.assert_awaited_once_with("discord-secret")
            self.assertEqual(create.call_count, int(enabled))
            self.assertEqual(handler.start_async.await_count, int(enabled))
