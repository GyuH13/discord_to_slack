"""Discord 봇."""

import asyncio
import logging
from dataclasses import dataclass, field

import discord
from discord import Thread

from . import progress, store
from .auto_handling import auto_handle_threads, is_new
from .auto_solve import auto_solve_threads, has_solved_tag, is_target_forum
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
    return str(parent.id) in config.discord.forum_channel_ids


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
    if not config.discord.forum_channel_ids:
        raise RuntimeError("대상 포럼이 없어 동기화를 중단합니다.")
    threads: dict[int, Thread] = {}
    active_by_guild: dict[int, list[Thread]] = {}
    for cid in dict.fromkeys(config.discord.forum_channel_ids):
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
    threads: list[Thread] = field(default_factory=list)


async def _get_issue_snapshot(
    client: discord.Client,
    config: Config,
    *,
    apply_auto_solve: bool = False,
    apply_handling: bool = False,
) -> IssueSnapshot:
    """대상 포럼 전체 스레드 기준 상태 개수와, Slack List에 올릴 미해결 행을 한 번에 계산합니다."""
    progress.emit("fetch", "🔎 Discord 글 목록 조회 중")
    threads = await _fetch_threads(client, config)
    progress.emit("fetch", f"✅ Discord 글 {len(threads)}개 조회 완료")
    if apply_auto_solve or apply_handling:
        progress.emit("status", "⏳ 직원 답변 이력·장기 무응답 검사 및 상태 처리 중")
    if apply_auto_solve:
        progress.emit("solved", "⏳ 마지막 직원 답변 후 장기 무응답 글 검사 중")
        threads = await auto_solve_threads(
            client,
            threads,
            config.auto_solve,
            set(STATUS_LABELS),
            config.discord.forum_channel_ids,
        )
        progress.emit(
            "solved",
            "✅ 자동 Solved 검사 완료" if config.auto_solve.enabled else "➖ 자동 Solved 기능 꺼짐",
        )
    if apply_handling:
        progress.emit("handling", "⏳ 직원 답변 이력 확인 및 Handling 처리 중")
        threads = await auto_handle_threads(
            client, threads, config.auto_solve, config.discord.forum_channel_ids
        )
        progress.emit("handling", "✅ Handling 검사 완료")
    counts = {"solved": 0, "handling": 0, "new_issue": 0}
    result: list[dict] = []
    for thread in threads:
        parent = thread.parent
        if not _is_forum_thread(parent) or not _is_target_channel(parent, config):
            raise RuntimeError("글의 대상 포럼을 확인할 수 없어 동기화를 중단합니다.")
        _, status_tags = _split_tags(_get_tags(thread))
        if config.auto_solve.enabled and is_target_forum(parent, config.auto_solve):
            if has_solved_tag(thread, config.auto_solve):
                status_tags = ["Complete"]
        if "Complete" in status_tags:
            counts["solved"] += 1
            continue
        if "Handling" in status_tags:
            counts["handling"] += 1
        else:
            counts["new_issue"] += 1
        result.append(
            {
                "title": thread.name,
                "status": "Handling" if "Handling" in status_tags else "New Issue",
                "url": _discord_url(thread),
                "created_at": thread.created_at,
                "slack_msg_url": store.get_link(thread.id),
            }
        )
    return IssueSnapshot(result, counts, threads)


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
    if not config.table_sync.enabled and not config.auto_solve.enabled:
        return 0
    if _sync_lock.locked():
        logger.info("sync_list 이미 실행 중 — 완료 후 재실행")
        progress.emit("queue", "⏳ 다른 테이블 동기화가 끝나기를 기다리는 중")
    async with _sync_lock:
        progress.emit("queue", "✅ 동기화 작업 시작")
        snapshot = await _sync_list_locked(config, client)
        return len(snapshot.issues)


async def _sync_list_locked(config: Config, client: discord.Client) -> IssueSnapshot:
    """호출자가 _sync_lock을 보유해야 한다. 갱신에 사용한 스냅샷을 그대로 반환한다."""
    snapshot = await _get_issue_snapshot(client, config, apply_auto_solve=True, apply_handling=True)
    counts = snapshot.counts
    progress.emit(
        "status",
        f"✅ 상태 집계: Solved {counts['solved']} · "
        f"Handling {counts['handling']} · New {counts['new_issue']}",
    )
    if config.table_sync.enabled:
        await asyncio.to_thread(
            sync_list,
            slack_bot_token=config.slack.bot_token,
            slack_user_token=config.slack.user_token,
            list_id=config.table_sync.list_id,
            threads=snapshot.issues,
            status_counts=snapshot.counts,
        )
    return snapshot


async def _handle_new_thread(thread: Thread, config: Config, client: discord.Client) -> None:
    parent = thread.parent
    if not _is_forum_thread(parent) or not _is_target_channel(parent, config):
        return

    if config.notifications.enabled:
        try:
            author = await _fetch_author(thread)
            permalink = await asyncio.to_thread(
                post_message,
                slack_bot_token=config.slack.bot_token,
                channel_id=config.notifications.channel_id,
                title=thread.name,
                author=author,
                url=_discord_url(thread),
                forum_name=parent.name,
                tags=_get_tags(thread),
            )
            store.set_link(thread.id, permalink)
        except Exception:
            logger.exception("새 글 Slack 알림 실패: 스레드 %s", thread.id)

    if config.table_sync.enabled or config.auto_solve.enabled:
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

    @client.event
    async def on_message(message: discord.Message) -> None:
        try:
            await _handle_reply(message, cfg, client)
        except Exception:
            logger.exception("답변 처리 실패: 메시지 %s", message.id)

    return client


async def _handle_reply(message: discord.Message, config: Config, client: discord.Client) -> None:
    thread = message.channel
    if (
        message.author.bot
        or message.webhook_id
        or message.is_system()
        or not isinstance(thread, discord.Thread)
        or message.id == thread.id
        or str(thread.parent_id) not in config.discord.forum_channel_ids
        or not is_new(thread, config.auto_solve)
    ):
        return
    async with _sync_lock:
        updated = await auto_handle_threads(
            client, [thread], config.auto_solve, config.discord.forum_channel_ids
        )
        changed = any(tag.name in {"Handling", "🟡Handling"} for tag in updated[0].applied_tags)
    if changed and config.table_sync.enabled:
        await run_sync_list(config, client)
