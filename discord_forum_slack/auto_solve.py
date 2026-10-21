"""동기화 트리거에서 ROBOTIS의 마지막 답변 후 무응답 글을 완료 처리한다."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import discord

from .config import AutoSolveConfig

logger = logging.getLogger(__name__)


def is_target_forum(forum: discord.ForumChannel, settings: AutoSolveConfig) -> bool:
    """기본은 기존 동기화 대상 전체. 명시된 단일 포럼 설정은 필터로 유지한다."""
    if settings.forum_channel_id:
        return str(forum.id) == settings.forum_channel_id
    return not settings.forum_channel_name or (
        forum.name.casefold() == settings.forum_channel_name.casefold()
    )


async def _resolve_settings(
    client: discord.Client,
    settings: AutoSolveConfig,
    channel_id: str,
) -> tuple[discord.ForumChannel, AutoSolveConfig] | None:
    forum = await client.fetch_channel(int(channel_id))
    if not isinstance(forum, discord.ForumChannel):
        raise ValueError("자동 완료 대상이 포럼 채널이 아닙니다.")
    if not is_target_forum(forum, settings):
        return None
    roles = [
        role
        for role in await forum.guild.fetch_roles()
        if (
            str(role.id) == settings.robotis_role_id
            if settings.robotis_role_id
            else role.name.casefold() == settings.robotis_role_name.casefold()
        )
    ]
    tags = [
        tag
        for tag in forum.available_tags
        if (
            str(tag.id) == settings.solved_tag_id
            if settings.solved_tag_id
            else tag.name.removeprefix("✅").strip().casefold()
            == settings.solved_tag_name.removeprefix("✅").strip().casefold()
        )
    ]
    if len(roles) != 1 or len(tags) != 1:
        raise ValueError("ROBOTIS 역할 또는 Solved 태그가 없거나 중복됩니다. ID로 지정해주세요.")
    return forum, replace(
        settings,
        forum_channel_id=str(forum.id),
        robotis_role_id=str(roles[0].id),
        solved_tag_id=str(tags[0].id),
    )


async def _last_human_message(thread: discord.Thread) -> discord.Message | None:
    async for message in thread.history(limit=None, oldest_first=False):
        if message.author.bot or message.webhook_id or message.is_system():
            continue
        return message
    return None


def _expired_reply(message: discord.Message | None, thread_id: int, cutoff: datetime) -> bool:
    # 최초 게시글만 있는 경우는 직원이 답변한 것으로 간주하지 않는다.
    return message is not None and message.id != thread_id and message.created_at <= cutoff


def has_solved_tag(thread: discord.Thread, settings: AutoSolveConfig) -> bool:
    return any(
        str(tag.id) == settings.solved_tag_id
        or tag.name.removeprefix("✅").strip().casefold()
        == settings.solved_tag_name.removeprefix("✅").strip().casefold()
        for tag in thread.applied_tags
    )


async def _solve_one(
    client: discord.Client,
    thread: discord.Thread,
    forum: discord.ForumChannel,
    settings: AutoSolveConfig,
    status_names: set[str],
    cutoff: datetime,
) -> discord.Thread:
    solved_id = int(settings.solved_tag_id)
    if has_solved_tag(thread, settings):
        return thread
    last = await _last_human_message(thread)
    if not _expired_reply(last, thread.id, cutoff):
        return thread
    member = await forum.guild.fetch_member(last.author.id)
    if not any(role.id == int(settings.robotis_role_id) for role in member.roles):
        return thread

    # 캐시 대신 최신 스레드 상태를 사용해 제품 태그와 보관 상태를 유지한다.
    current = await client.fetch_channel(thread.id)
    if not isinstance(current, discord.Thread) or current.parent_id != forum.id:
        return thread
    if has_solved_tag(current, settings):
        return current
    available = {tag.id: tag for tag in forum.available_tags}
    tags = [
        tag
        for tag in current.applied_tags
        if available.get(tag.id, tag).name not in status_names and tag.id != solved_id
    ]
    tags.append(available[solved_id])
    if len(tags) > 5:
        logger.warning(
            "자동 완료 제외: 글 %s의 제품/기타 태그를 유지하면 5개를 초과합니다.", thread.id
        )
        return current

    # 보관 해제 전에 재확인해서 최근 답변이 달린 글은 건드리지 않는다.
    latest = await _last_human_message(current)
    if not _expired_reply(latest, thread.id, cutoff) or latest.id != last.id:
        return current
    was_archived = current.archived
    restore_archive = was_archived
    reason = f"ROBOTIS 마지막 답변 이후 {settings.inactive_days}일 무응답 자동 완료"
    if was_archived:
        current = await current.edit(archived=False, reason=reason)
    try:
        # 보관 해제나 역할 조회 중 새 답변이 온 경우도 제외한다.
        member = await forum.guild.fetch_member(last.author.id)
        latest = await _last_human_message(current)
        if not _expired_reply(latest, thread.id, cutoff) or latest.id != last.id:
            # 새 대화가 시작됐다면 다시 보관해 숨기지 않는다.
            restore_archive = False
            return current
        if not any(role.id == int(settings.robotis_role_id) for role in member.roles):
            return current
        current = await current.edit(applied_tags=tags, reason=reason)
        logger.info("자동 Solved 처리: 글 %s, 마지막 답변 %s", thread.id, last.id)
        return current
    finally:
        if restore_archive:
            try:
                await current.edit(archived=True, reason=reason)
            except discord.DiscordException:
                logger.exception("글 %s의 보관 상태 복원 실패", thread.id)


async def auto_solve_threads(
    client: discord.Client,
    threads: list[discord.Thread],
    settings: AutoSolveConfig,
    status_names: set[str],
    forum_channel_ids: list[str],
    *,
    now: datetime | None = None,
) -> list[discord.Thread]:
    if not settings.enabled:
        return threads
    resolved = {}
    for channel_id in dict.fromkeys(forum_channel_ids):
        if settings.forum_channel_id and channel_id != settings.forum_channel_id:
            continue
        try:
            target = await _resolve_settings(client, settings, channel_id)
            if target is not None:
                resolved[target[0].id] = target
        except (discord.DiscordException, ValueError):
            logger.exception("포럼 %s 자동 완료 설정/조회 실패 — 해당 포럼 건너뜀", channel_id)

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=settings.inactive_days)
    result = []
    for thread in threads:
        if target := resolved.get(thread.parent_id):
            forum, forum_settings = target
            try:
                thread = await _solve_one(
                    client, thread, forum, forum_settings, status_names, cutoff
                )
            except discord.DiscordException:
                logger.exception(
                    "자동 완료 제외: 글 %s의 메시지·역할 조회 또는 태그 변경 실패", thread.id
                )
        result.append(thread)
    return result
