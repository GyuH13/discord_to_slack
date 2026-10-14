"""Discord 봇."""

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import Thread

from . import store
from .config import Config
from .msg_sender import post_message
from .slack_list import sync_list

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()

PRODUCT_TAGS: set[str] = {"dynamixel", "ai-worker", "omy", "omx", "hand", "turtlebot", "others"}
STATUS_LABELS: dict[str, str] = {
    "🟢New": "New Issue",
    "🟡Handling": "Handling",
    "✅Solved": "Complete",
    "New": "New Issue",
    "Handling": "Handling",
    "Solved": "Complete",
}


def _create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    return discord.Client(intents=intents)


def _get_tags(thread: discord.Thread) -> list[str]:
    applied = getattr(thread, "applied_tags", None) or []
    return [name for tag in applied if (name := getattr(tag, "name", None))]


def _split_tags(tag_names: list[str]) -> tuple[list[str], list[str]]:
    product_tags = [t for t in tag_names if t in PRODUCT_TAGS]
    status_tags = [STATUS_LABELS[t] for t in tag_names if t in STATUS_LABELS]
    return product_tags, status_tags


def _is_forum_thread(parent: discord.abc.GuildChannel | None) -> bool:
    return isinstance(parent, discord.ForumChannel)


def _is_target_channel(parent: discord.ForumChannel, config: Config) -> bool:
    return bool(config.forum_channel_ids) and str(parent.id) in config.forum_channel_ids


def _discord_url(thread: Thread) -> str:
    return f"https://discord.com/channels/{thread.guild.id}/{thread.id}"


async def _fetch_author(thread: Thread) -> str:
    try:
        async for msg in thread.history(limit=1, oldest_first=True):
            u = msg.author
            return f"{getattr(u, 'display_name', None)} ({u})"
    except discord.DiscordException:
        pass
    if thread.owner_id:
        return f"Unknown ({thread.owner_id})"
    return "unknown"


async def _fetch_threads(client: discord.Client, config: Config) -> list[Thread]:
    if not config.forum_channel_ids:
        raise RuntimeError("대상 포럼이 없어 동기화를 중단합니다.")
    threads: dict[int, Thread] = {}
    active_by_guild: dict[int, list[Thread]] = {}
    for cid in dict.fromkeys(config.forum_channel_ids):
        try:
            channel = client.get_channel(int(cid)) or await client.fetch_channel(int(cid))
            if not isinstance(channel, discord.ForumChannel):
                raise ValueError("대상 채널이 포럼이 아닙니다.")
            if channel.guild.id not in active_by_guild:
                active_by_guild[channel.guild.id] = await channel.guild.active_threads()
            for thread in active_by_guild[channel.guild.id]:
                if thread.parent_id == channel.id:
                    threads[thread.id] = thread
            async for thread in channel.archived_threads(limit=None):
                threads[thread.id] = thread
        except (ValueError, discord.DiscordException) as e:
            raise RuntimeError(
                f"포럼 {cid} 조회가 불완전하여 동기화를 중단합니다. Slack 행은 유지됩니다.",
            ) from e
    return list(threads.values())


@dataclass
class IssueSnapshot:
    issues: list[dict]
    counts: dict[str, int]
    solved_urls: set[str]


async def _get_issue_snapshot(
    client: discord.Client,
    config: Config,
) -> IssueSnapshot:
    """대상 포럼 전체 스레드 기준 상태 개수와, Slack List에 올릴 미해결 행을 한 번에 계산합니다."""
    threads = await _fetch_threads(client, config)
    counts = {"solved": 0, "handling": 0, "new_issue": 0}
    result: list[dict] = []
    solved_urls: set[str] = set()
    for thread in threads:
        parent = thread.parent
        if not _is_forum_thread(parent) or not _is_target_channel(parent, config):
            raise RuntimeError("글의 대상 포럼을 확인할 수 없어 동기화를 중단합니다.")
        _, status_tags = _split_tags(_get_tags(thread))
        if "Complete" in status_tags:
            counts["solved"] += 1
            solved_urls.add(_discord_url(thread))
            continue
        if "Handling" in status_tags:
            counts["handling"] += 1
        else:
            counts["new_issue"] += 1
        result.append(
            {
                "title": thread.name,
                "status": status_tags[0] if status_tags else "New Issue",
                "url": _discord_url(thread),
                "created_at": thread.created_at,
                "slack_msg_url": store.get_link(thread.id),
            }
        )
    return IssueSnapshot(result, counts, solved_urls)


async def get_open_issues_and_counts(
    client: discord.Client,
    config: Config,
) -> tuple[list[dict], dict[str, int]]:
    snapshot = await _get_issue_snapshot(client, config)
    return snapshot.issues, snapshot.counts


async def get_open_issues(client: discord.Client, config: Config) -> list[dict]:
    """미해결 스레드만 필요할 때 (개수는 버림)."""
    issues, _ = await get_open_issues_and_counts(client, config)
    return issues


async def run_sync_list(
    config: Config,
    client: discord.Client,
) -> int:
    """조회부터 동기화까지 직렬화하여 오래된 스냅샷의 덮어쓰기를 방지합니다."""
    if _sync_lock.locked():
        logger.info("sync_list 이미 실행 중 — 완료 후 재실행")
    async with _sync_lock:
        snapshot = await _get_issue_snapshot(client, config)
        await asyncio.to_thread(
            sync_list,
            slack_bot_token=config.slack_bot_token,
            slack_user_token=config.slack_user_token,
            list_id=config.list_id,
            threads=snapshot.issues,
            status_counts=snapshot.counts,
            solved_urls=snapshot.solved_urls,
        )
        return len(snapshot.issues)


async def _handle_new_thread(thread: Thread, config: Config, client: discord.Client) -> None:
    parent = thread.parent
    if not _is_forum_thread(parent) or not _is_target_channel(parent, config):
        return

    author = await _fetch_author(thread)
    url = _discord_url(thread)
    tags = _get_tags(thread)

    if config.slack_bot_token and config.slack_channel_id:
        permalink = await asyncio.to_thread(
            post_message,
            slack_bot_token=config.slack_bot_token,
            channel_id=config.slack_channel_id,
            title=thread.name,
            author=author,
            url=url,
            forum_name=parent.name,
            tags=tags,
        )
        store.set_link(thread.id, permalink)

    if config.slack_user_token and config.list_id:
        await run_sync_list(config, client)


def create_discord_bot(config: Config) -> discord.Client:
    cfg = config
    client = _create_client()

    @client.event
    async def on_ready() -> None:
        logger.info("Discord 봇 로그인: %s", client.user)

    @client.event
    async def on_thread_create(thread: Thread) -> None:
        try:
            await _handle_new_thread(thread, cfg, client)
        except Exception:
            logger.exception("처리 실패: 스레드 %s (%s)", thread.id, thread.name)

    return client
