import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from slack_sdk.errors import SlackApiError

from discord_forum_slack import assign_store, discord_bot, slack_list
from discord_forum_slack.config import AutoSolveConfig

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
        "type": "user",
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
        self.fail_after_creates = None
        self.created = 0
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for patcher in (
            patch.object(slack_list, "WebClient", return_value=self.client),
            patch.object(slack_list.time, "sleep"),
            patch.object(assign_store, "BACKUP_DIR", Path(temporary.name)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def api_call(self, method, *, json):
        if method == self.fail_method or (
            method == "slackLists.items.create" and self.created == self.fail_after_creates
        ):
            raise SlackApiError("API failed", {"ok": False, "error": "test_failure"})
        if method == "slackLists.items.list":
            if json.get("include_list"):
                return {
                    "items": copy.deepcopy(self.items[:1]),
                    "list": {
                        "list_metadata": {"schema": self.schema},
                    },
                }
            if self.pages is not None:
                page = self.pages.pop(0)
                if isinstance(page, Exception):
                    raise page
                return page
            return {"items": copy.deepcopy(self.items)}
        self.writes.append((method, copy.deepcopy(json)))
        if method == "slackLists.items.delete":
            # 첫 삭제 전에 실제 파일 백업이 저장되어 있어야 한다.
            if any(c["name"].strip().casefold() == "assign" for c in self.schema):
                self.assertTrue(assign_store._path("list").exists())
            self.items = [i for i in self.items if i["id"] != json["id"]]
        elif method == "slackLists.items.create":
            self.created += 1
            self.items.append(
                {"id": f"new{self.created}", "fields": copy.deepcopy(json["initial_fields"])}
            )
        return {"ok": True}

    def sync(self, threads=None):
        slack_list.sync_list(
            slack_bot_token="test",
            list_id="list",
            threads=[issue()] if threads is None else threads,
            status_counts=COUNTS,
        )

    def assign_values(self, kind="user"):
        return {
            slack_list._item_key(item, "link"): next(
                f[kind] for f in item["fields"] if f["column_id"] == "assign"
            )
            for item in self.items
        }

    def test_rebuild_restores_assign_and_updates_title_and_status(self):
        self.sync([issue(title="Renamed", status="Handling")])
        self.assertEqual(
            [m for m, _ in self.writes],
            [
                "slackLists.items.delete",
                "slackLists.items.create",
                "slackLists.update",
            ],
        )
        self.assertNotEqual(self.items[0]["id"], "row1")
        self.assertEqual(self.assign_values(), {("10", "20"): ["U-assignee"]})
        fields = {f["column_id"]: f for f in self.items[0]["fields"]}
        self.assertEqual(fields["title"]["rich_text"], rich_text("Renamed"))
        self.assertEqual(fields["status"]["select"], ["handling"])

    def test_newest_first_and_new_issue_assign_is_empty(self):
        newer = issue(url=SOLVED_URL, created_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.sync([issue(), newer])
        self.assertEqual(
            [slack_list._item_key(i, "link") for i in self.items], [("10", "30"), ("10", "20")]
        )
        self.assertEqual(self.assign_values(), {("10", "30"): [], ("10", "20"): ["U-assignee"]})

    def test_deleted_solved_and_unlinked_rows_are_removed(self):
        self.items += [
            row("solved", SOLVED_URL),
            row("deleted", URL + "9"),
            {"id": "manual", "fields": []},
        ]
        self.sync()
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.assign_values(), {("10", "20"): ["U-assignee"]})

    def test_all_issues_gone_leaves_empty_table_and_keeps_backup(self):
        self.sync([])
        self.assertEqual(self.items, [])
        self.assertEqual(assign_store.load("list", "assign", "user")["10/20"], ["U-assignee"])

    def test_select_and_text_assign_round_trip(self):
        for kind, field_type, value in (
            ("select", "select", ["unrelated-option"]),
            ("rich_text", "text", rich_text("담당자")),
        ):
            with self.subTest(kind=kind):
                assign_store._path("list").unlink(missing_ok=True)
                self.schema[-1]["type"] = field_type
                self.items = [row()]
                self.items[0]["fields"][-1] = {"column_id": "assign", kind: value}
                self.sync()
                self.assertEqual(self.assign_values(kind), {("10", "20"): value})

    def test_user_cleared_assign_overrides_old_backup(self):
        self.sync()
        self.items[0]["fields"] = [f for f in self.items[0]["fields"] if f["column_id"] != "assign"]
        self.sync()
        self.assertEqual(self.assign_values(), {("10", "20"): []})
        self.assertEqual(assign_store.load("list", "assign", "user")["10/20"], [])

    def test_restart_after_partial_creation_restores_absent_rows_from_disk(self):
        self.items.append(row("second", SOLVED_URL))
        self.items[1]["fields"][-1]["user"] = ["U-second"]
        threads = [issue(), issue(url=SOLVED_URL)]
        self.fail_after_creates = 1
        with self.assertRaises(SlackApiError):
            self.sync(threads)
        self.assertEqual(len(self.items), 1)
        # 새로운 WebClient로 재실행: 메모리 상태 없이 파일 백업에서 복구한다.
        self.fail_after_creates = None
        self.client = Mock()
        self.client.api_call.side_effect = self.api_call
        with patch.object(slack_list, "WebClient", return_value=self.client):
            self.sync(threads)
        self.assertEqual(
            self.assign_values(), {("10", "20"): ["U-assignee"], ("10", "30"): ["U-second"]}
        )

    def test_restart_after_all_deletions_restores_assign(self):
        self.fail_method = "slackLists.items.create"
        with self.assertRaises(SlackApiError):
            self.sync()
        self.assertEqual(self.items, [])
        self.fail_method = None
        self.sync()
        self.assertEqual(self.assign_values(), {("10", "20"): ["U-assignee"]})

    def test_delete_failure_stops_before_creation_and_keeps_backup(self):
        self.fail_method = "slackLists.items.delete"
        with self.assertRaises(SlackApiError):
            self.sync()
        self.assertEqual(self.writes, [])
        self.assertEqual(assign_store.load("list", "assign", "user")["10/20"], ["U-assignee"])

    def test_read_and_pagination_failure_prevent_mutations(self):
        self.fail_method = "slackLists.items.list"
        with self.assertRaises(SlackApiError):
            self.sync()
        self.fail_method = None
        self.pages = [
            {"items": [row()], "response_metadata": {"next_cursor": "next"}},
            SlackApiError("failed", {"ok": False, "error": "test_failure"}),
        ]
        with self.assertRaises(SlackApiError):
            self.sync()
        self.assertEqual(self.writes, [])

    def test_pagination_captures_all_assignments(self):
        self.items.append(row("second", SOLVED_URL))
        self.pages = [
            {"items": [copy.deepcopy(self.items[0])], "response_metadata": {"next_cursor": "next"}},
            {"items": [copy.deepcopy(self.items[1])]},
        ]
        self.sync([issue(), issue(url=SOLVED_URL)])
        self.assertEqual(len(self.assign_values()), 2)
        self.assertEqual(assign_store.load("list", "assign", "user")["10/30"], ["U-assignee"])

    def test_backup_write_failure_prevents_deletion_and_keeps_previous_file(self):
        self.sync()
        before = assign_store._path("list").read_bytes()
        self.writes.clear()
        with patch.object(assign_store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.sync()
        self.assertEqual(self.writes, [])
        self.assertEqual(assign_store._path("list").read_bytes(), before)

    def test_corrupt_backup_prevents_deletion(self):
        assign_store._path("list").write_text("broken JSON")
        with self.assertRaises(json.JSONDecodeError):
            self.sync()
        self.assertEqual(self.writes, [])

    def test_backup_is_scoped_to_list_and_column(self):
        self.sync()
        self.assertEqual(assign_store.load("other-list", "assign", "user"), {})
        self.schema[-1]["id"] = "new-assign-column"
        self.writes.clear()
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertEqual(self.writes, [])

    def test_conflicting_duplicates_and_unlinked_assignment_abort(self):
        for other in (row("duplicate"), row("unlinked", "https://example.com")):
            self.items = [row(), other]
            other["fields"][-1]["user"] = ["different-user"]
            with self.assertRaises(RuntimeError):
                self.sync()
            self.assertEqual(self.writes, [])

    def test_duplicate_same_assign_and_url_variant_rebuild_as_one(self):
        self.items.append(row("duplicate", URL + "/?x=1"))
        self.sync()
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.assign_values(), {("10", "20"): ["U-assignee"]})

    def test_schema_and_input_errors_prevent_deletion(self):
        for error in ("link", "status", "assign_type", "invalid_url", "duplicate"):
            with self.subTest(error=error):
                self.schema = copy.deepcopy(SCHEMA)
                threads = [issue()]
                if error == "link":
                    self.schema = [s for s in self.schema if s["name"] != "Link"]
                elif error == "status":
                    self.schema[1]["options"]["choices"] = []
                elif error == "assign_type":
                    self.schema[-1]["type"] = "number"
                elif error == "invalid_url":
                    threads = [issue(url="invalid")]
                else:
                    threads.append(issue())
                with self.assertRaises(RuntimeError):
                    self.sync(threads)
                self.assertEqual(self.writes, [])

    def test_empty_list_and_absent_assign_column_work(self):
        self.items = []
        self.schema = [s for s in self.schema if s["name"] != "assign"]
        self.sync()
        self.assertEqual(len(self.items), 1)
        self.assertFalse(assign_store._path("list").exists())


async def archived_result(threads=(), error=False):
    for thread in threads:
        yield thread
    if error:
        raise discord.DiscordException("Archive unavailable")


class DiscordSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            auto_solve=AutoSolveConfig(),
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

        async def snapshot(*args, **kwargs):
            snapshots.append(len(snapshots))
            return discord_bot.IssueSnapshot([], COUNTS)

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
