import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from slack_sdk.errors import SlackApiError

from discord_forum_slack import discord_bot, slack_list

URL = "https://discord.com/channels/10/20"
SOLVED_URL = "https://discord.com/channels/10/30"
COUNTS = {"new_issue": 1, "handling": 0, "solved": 0}
SCHEMA = [
    {"id": "title", "name": "Issue Title"},
    {
        "id": "status",
        "name": "Status",
        "options": {
            "choices": [
                {"label": "New Issue", "value": "new"},
                {"label": "Handling", "value": "handling"},
            ]
        },
    },
    {"id": "msg", "name": "Msg Thread"},
    {"id": "link", "name": "Link"},
    {"id": "date", "name": "Posted Date"},
    {
        "id": "assign",
        "name": "assign",
        "options": {
            "choices": [
                {"label": "Handling", "value": "unrelated-option"},
            ]
        },
    },
]


def rich_text(text):
    return [
        {
            "type": "rich_text",
            "elements": [
                {"type": "rich_text_section", "elements": [{"type": "text", "text": text}]},
            ],
        }
    ]


def issue(**changes):
    return {
        "title": "Issue",
        "status": "New Issue",
        "url": URL,
        "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
        "slack_msg_url": "https://example.slack.com/archives/C1/p123",
        **changes,
    }


def row(row_id="row1", url=URL):
    title = rich_text("Issue")
    title[0]["block_id"] = "server-generated"
    return {
        "id": row_id,
        "fields": [
            {"column_id": "title", "rich_text": title},
            {"column_id": "status", "select": ["new"]},
            {
                "column_id": "msg",
                "message": [
                    {"value": "https://example.slack.com/archives/C1/p123", "channel_id": "C1"},
                ],
            },
            {"column_id": "link", "link": [{"originalUrl": url, "displayName": url}]},
            {"column_id": "date", "rich_text": rich_text("2026-10-14 09:00 (KST)")},
            {"column_id": "assign", "user": ["U-assignee"]},
        ],
    }


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.items = [row()]
        self.schema = copy.deepcopy(SCHEMA)
        self.client = Mock()
        self.client.api_call.side_effect = self.api_call
        self.writes = []
        self.pages = None
        self.fail_method = None
        factory = patch.object(slack_list, "WebClient", return_value=self.client)
        factory.start()
        self.addCleanup(factory.stop)
        sleep = patch.object(slack_list.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def api_call(self, method, *, json):
        if method == self.fail_method:
            raise SlackApiError("API failed", {"ok": False, "error": "test_failure"})
        if method == "slackLists.items.list":
            if json.get("include_list"):
                return {
                    "items": self.items[:1],
                    "list": {
                        "list_metadata": {"schema": self.schema},
                    },
                }
            if self.pages is not None:
                return self.pages.pop(0)
            return {"items": self.items}
        self.writes.append((method, json))
        return {"ok": True}

    def sync(self, threads=None, solved_urls=None):
        slack_list.sync_list(
            slack_bot_token="test",
            list_id="list",
            threads=[issue()] if threads is None else threads,
            status_counts=COUNTS,
            solved_urls=solved_urls,
        )

    def item_writes(self):
        return [(m, p) for m, p in self.writes if m.startswith("slackLists.items.")]

    def test_unchanged_row_is_not_rewritten(self):
        self.sync()
        self.assertEqual(self.item_writes(), [])

    def test_only_changed_cells_are_updated_and_manual_values_survive(self):
        for manual in ({"user": ["U-assignee"]}, {"select": ["owner-choice"]}):
            with self.subTest(manual=manual):
                self.items[0]["fields"][-1] = {"column_id": "assign", **manual}
                before = copy.deepcopy(self.items)
                self.writes.clear()
                self.sync([issue(title="Renamed", status="Handling")])
                method, payload = self.item_writes()[0]
                self.assertEqual(method, "slackLists.items.update")
                self.assertEqual(
                    payload["cells"],
                    [
                        {"row_id": "row1", "column_id": "title", "rich_text": rich_text("Renamed")},
                        {"row_id": "row1", "column_id": "status", "select": ["handling"]},
                    ],
                )
                self.assertEqual(len(self.item_writes()), 1)
                self.assertEqual(self.items, before)

    def test_empty_list_can_create_row_without_assign(self):
        self.items = []
        self.sync()
        method, payload = self.item_writes()[0]
        self.assertEqual(method, "slackLists.items.create")
        self.assertNotIn("assign", [f["column_id"] for f in payload["initial_fields"]])

    def test_only_explicitly_solved_rows_are_deleted(self):
        self.items += [
            row("solved", SOLVED_URL),
            row("missing", URL + "9"),
            {"id": "manual", "fields": []},
        ]
        self.sync(solved_urls={SOLVED_URL})
        self.assertEqual(
            self.item_writes(),
            [
                ("slackLists.items.delete", {"list_id": "list", "id": "solved"}),
            ],
        )

    def test_all_solved_removes_only_confirmed_rows(self):
        self.sync(threads=[], solved_urls={URL})
        self.assertEqual(
            self.item_writes(),
            [
                ("slackLists.items.delete", {"list_id": "list", "id": "row1"}),
            ],
        )

    def test_missing_slack_message_mapping_preserves_existing_message(self):
        self.sync([issue(slack_msg_url=None)])
        self.assertEqual(self.item_writes(), [])

    def test_url_variants_match_and_duplicate_rows_are_preserved(self):
        self.items = [row("a", URL + "/?x=1"), row("b", URL)]
        self.sync([issue(title="Changed")])
        self.assertTrue(all(m == "slackLists.items.update" for m, _ in self.item_writes()))
        self.assertEqual({p["cells"][0]["row_id"] for _, p in self.item_writes()}, {"a", "b"})

    def test_pagination_finds_existing_row_on_later_page(self):
        self.pages = [
            {"items": [], "response_metadata": {"next_cursor": "next"}},
            {"items": [row()]},
        ]
        self.sync()
        self.assertEqual(self.item_writes(), [])
        self.assertEqual(self.client.api_call.call_args_list[2].kwargs["json"]["cursor"], "next")

    def test_read_failure_prevents_all_mutations(self):
        self.fail_method = "slackLists.items.list"
        with self.assertRaises(SlackApiError):
            self.sync(solved_urls={SOLVED_URL})
        self.assertEqual(self.writes, [])

    def test_update_failure_is_reported_and_deletion_is_not_started(self):
        self.items.append(row("solved", SOLVED_URL))
        self.fail_method = "slackLists.items.update"
        with self.assertRaises(SlackApiError):
            self.sync([issue(title="Changed")], {SOLVED_URL})
        self.assertEqual(self.writes, [])

    def test_missing_link_or_status_option_prevents_all_mutations(self):
        for name in ("Link", "Status option"):
            with self.subTest(name=name):
                self.schema = copy.deepcopy(SCHEMA)
                if name == "Link":
                    self.schema = [s for s in self.schema if s["name"] != "Link"]
                else:
                    self.schema[1]["options"]["choices"] = []
                with self.assertRaises(RuntimeError):
                    self.sync()
                self.assertEqual(self.writes, [])


async def archived_result(threads=(), error=False):
    for thread in threads:
        yield thread
    if error:
        raise discord.DiscordException("Archive unavailable")


class DiscordSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            forum_channel_ids=["1"],
            slack_bot_token="test",
            slack_user_token="",
            list_id="list",
        )
        self.channel = Mock(spec=discord.ForumChannel)
        self.channel.id = 1
        self.channel.guild = SimpleNamespace(id=10, active_threads=AsyncMock(return_value=[]))
        self.channel.archived_threads.return_value = archived_result()
        self.client = Mock()
        self.client.get_channel.return_value = self.channel
        self.client.fetch_channel = AsyncMock()
        lock = patch.object(discord_bot, "_sync_lock", asyncio.Lock())
        lock.start()
        self.addCleanup(lock.stop)

    def thread(self, ident, tag):
        return SimpleNamespace(
            id=ident,
            parent=self.channel,
            parent_id=1,
            guild=self.channel.guild,
            name="Issue",
            applied_tags=[SimpleNamespace(name=tag)],
            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        )

    async def test_snapshot_uses_fresh_active_and_archived_threads_without_duplicates(self):
        opened, solved = self.thread(20, "Handling"), self.thread(30, "Solved")
        self.channel.guild.active_threads.return_value = [opened]
        self.channel.archived_threads.return_value = archived_result([opened, solved])
        with patch.object(discord_bot.store, "get_link", return_value=None):
            snapshot = await discord_bot._get_issue_snapshot(self.client, self.config)
        self.assertEqual([i["url"] for i in snapshot.issues], [URL])
        self.assertEqual(snapshot.solved_urls, {SOLVED_URL})
        self.assertEqual(snapshot.counts, {"new_issue": 0, "handling": 1, "solved": 1})

    async def test_partial_archive_failure_never_calls_slack(self):
        self.channel.archived_threads.return_value = archived_result(
            [self.thread(30, "Solved")], True
        )
        with patch.object(discord_bot, "sync_list") as sync:
            with self.assertRaises(RuntimeError):
                await discord_bot.run_sync_list(self.config, self.client)
            sync.assert_not_called()

    async def test_active_fetch_failure_never_calls_slack(self):
        self.channel.guild.active_threads.side_effect = discord.DiscordException("Unavailable")
        with patch.object(discord_bot, "sync_list") as sync:
            with self.assertRaises(RuntimeError):
                await discord_bot.run_sync_list(self.config, self.client)
            sync.assert_not_called()

    async def test_channel_failure_and_empty_config_never_call_slack(self):
        for ids in (["1"], []):
            self.config.forum_channel_ids = ids
            self.client.get_channel.return_value = None
            self.client.fetch_channel.side_effect = discord.DiscordException("Forbidden")
            with patch.object(discord_bot, "sync_list") as sync:
                with self.assertRaises(RuntimeError):
                    await discord_bot.run_sync_list(self.config, self.client)
                sync.assert_not_called()

    async def test_concurrent_sync_waits_before_taking_second_snapshot(self):
        first_started, release = asyncio.Event(), asyncio.Event()
        snapshots = []

        async def snapshot(*args):
            snapshots.append(len(snapshots))
            return discord_bot.IssueSnapshot([], COUNTS, set())

        async def to_thread(*args, **kwargs):
            if len(snapshots) == 1:
                first_started.set()
                await release.wait()

        with (
            patch.object(discord_bot, "_get_issue_snapshot", side_effect=snapshot),
            patch.object(discord_bot.asyncio, "to_thread", side_effect=to_thread),
        ):
            first = asyncio.create_task(discord_bot.run_sync_list(self.config, self.client))
            await first_started.wait()
            second = asyncio.create_task(discord_bot.run_sync_list(self.config, self.client))
            await asyncio.sleep(0)
            self.assertEqual(len(snapshots), 1)
            release.set()
            self.assertEqual(await asyncio.gather(first, second), [0, 0])
            self.assertEqual(len(snapshots), 2)


if __name__ == "__main__":
    unittest.main()
