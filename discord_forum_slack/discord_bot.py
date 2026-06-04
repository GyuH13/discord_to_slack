"""Discord 봇."""

import asyncio
import logging

import discord
from discord import Thread

from .config import Config
from .msg_sender import post_message
from .slack_list import sync_list
from . import store

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
    threads: list[Thread] = []
    for cid in config.forum_channel_ids:
        try:
            channel = client.get_channel(int(cid)) or await client.fetch_channel(int(cid))
        except (ValueError, discord.DiscordException):
            continue
        if not isinstance(channel, discord.ForumChannel):
            continue
        threads.extend(channel.threads)
        try:
            async for thread in channel.archived_threads(limit=None):
                threads.append(thread)
        except discord.DiscordException:
            pass
    return threads


async def get_open_issues_and_counts(
    client: discord.Client,
    config: Config,
) -> tuple[list[dict], dict[str, int]]:
    """대상 포럼 전체 스레드 기준 상태 개수와, Slack List에 올릴 미해결 행을 한 번에 계산합니다."""
    threads = await _fetch_threads(client, config)
    counts = {"solved": 0, "handling": 0, "new_issue": 0}
    result: list[dict] = []
    for thread in threads:
        parent = thread.parent
        if not _is_forum_thread(parent) or not _is_target_channel(parent, config):
            continue
        _, status_tags = _split_tags(_get_tags(thread))
        if "Complete" in status_tags:
            counts["solved"] += 1
            continue
        if "Handling" in status_tags:
            counts["handling"] += 1
        else:
            counts["new_issue"] += 1
        result.append({
            "title": thread.name,
            "status": status_tags[0] if status_tags else "New Issue",
            "url": _discord_url(thread),
            "created_at": thread.created_at,
            "slack_msg_url": store.get_link(thread.id),
        })
    return result, counts


async def get_open_issues(client: discord.Client, config: Config) -> list[dict]:
    """미해결 스레드만 필요할 때 (개수는 버림)."""
    issues, _ = await get_open_issues_and_counts(client, config)
    return issues


async def run_sync_list(
    config: Config,
    issues: list[dict],
    status_counts: dict[str, int],
) -> None:
    """sync_list를 lock으로 감싸 동시 실행을 방지합니다."""
    if _sync_lock.locked():
        logger.info("sync_list 이미 실행 중 — 완료 후 재실행")
    async with _sync_lock:
        await asyncio.to_thread(
            sync_list,
            slack_bot_token=config.slack_bot_token,
            slack_user_token=config.slack_user_token,
            list_id=config.list_id,
            threads=issues,
            status_counts=status_counts,
        )


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
        issues, counts = await get_open_issues_and_counts(client, config)
        await run_sync_list(config, issues, counts)


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
