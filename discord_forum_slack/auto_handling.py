"""대화 중 직원 답변이 있는 신규 글을 Handling으로 전환한다."""

import logging

import discord

from .auto_solve import _last_human_message, has_solved_tag
from .config import AutoSolveConfig

logger = logging.getLogger(__name__)
NEW_TAGS = {"New", "🟢New"}
HANDLING_TAGS = {"Handling", "🟡Handling"}


def is_new(thread, settings: AutoSolveConfig) -> bool:
    return not has_solved_tag(thread, settings) and not any(
        tag.name in HANDLING_TAGS | {"Solved", "✅Solved"} for tag in thread.applied_tags
    )


def _is_human_reply(message, thread_id):
    return (
        message.id != thread_id
        and not message.author.bot
        and not message.webhook_id
        and not message.is_system()
    )


async def _find_staff_reply(thread, role_id):
    # 전체 이력을 검색하되 같은 작성자의 역할은 한 번만 조회한다.
    staff = {}
    async for message in thread.history(limit=None, oldest_first=False):
        if not _is_human_reply(message, thread.id):
            continue
        author_id = message.author.id
        if author_id not in staff:
            try:
                member = await thread.guild.fetch_member(author_id)
            except discord.NotFound:
                # 탈퇴한 작성자는 현재 역할을 확인할 수 없으므로 다음 답변을 확인한다.
                staff[author_id] = False
                continue
            staff[author_id] = any(role.id == role_id for role in member.roles)
        if staff[author_id]:
            return message
    return None


async def _reply_still_exists(thread, reply):
    try:
        current = await thread.fetch_message(reply.id)
    except discord.NotFound:
        return False
    return _is_human_reply(current, thread.id) and current.author.id == reply.author.id


async def _handle_one(client, thread, forum, role_id, handling, settings):
    reply = await _find_staff_reply(thread, role_id)
    if reply is None:
        return thread
    current = await client.fetch_channel(thread.id)
    if not isinstance(current, discord.Thread) or current.parent_id != forum.id:
        return thread
    if not is_new(current, settings):
        return current
    if not await _reply_still_exists(current, reply):
        return current
    tags = [tag for tag in current.applied_tags if tag.name not in NEW_TAGS]
    tags.append(handling)
    if len(tags) > 5:
        logger.warning("자동 Handling 제외: 글 %s의 기존 태그를 유지하면 5개 초과", thread.id)
        return current
    reason = "ROBOTIS 직원 답변에 따른 자동 Handling 전환"
    restore_archive = current.archived
    last_before = None
    if restore_archive:
        last_before = await _last_human_message(current)
        current = await current.edit(archived=False, reason=reason)
    try:
        member = await forum.guild.fetch_member(reply.author.id)
        if restore_archive:
            latest = await _last_human_message(current)
            if latest is not None and (last_before is None or latest.id != last_before.id):
                # 새 고객 답변이 와도 Handling으로 전환하되 대화는 열린 상태로 둔다.
                restore_archive = False
        if not await _reply_still_exists(current, reply):
            return current
        if not any(role.id == role_id for role in member.roles):
            return current
        current = await current.edit(applied_tags=tags, reason=reason)
        logger.info("자동 Handling 처리: 글 %s, 직원 답변 %s", thread.id, reply.id)
        return current
    finally:
        if restore_archive:
            try:
                await current.edit(archived=True, reason=reason)
            except discord.DiscordException:
                logger.exception("글 %s의 보관 상태 복원 실패", thread.id)


async def auto_handle_threads(client, threads, settings, forum_channel_ids):
    """설정된 모든 포럼에 적용. 자동 Solved의 활성화/단일 포럼 필터와 독립적이다."""
    resolved = {}
    result = []
    targets = set(forum_channel_ids)
    for thread in threads:
        if str(thread.parent_id) not in targets or not is_new(thread, settings):
            result.append(thread)
            continue
        try:
            if thread.parent_id not in resolved:
                resolved[thread.parent_id] = None
                forum = await client.fetch_channel(thread.parent_id)
                if not isinstance(forum, discord.ForumChannel):
                    raise ValueError("대상 채널이 포럼이 아닙니다.")
                roles = [
                    role
                    for role in await forum.guild.fetch_roles()
                    if (
                        str(role.id) == settings.robotis_role_id
                        if settings.robotis_role_id
                        else role.name.casefold() == settings.robotis_role_name.casefold()
                    )
                ]
                tags = [tag for tag in forum.available_tags if tag.name in HANDLING_TAGS]
                if len(roles) != 1 or len(tags) != 1:
                    raise ValueError("직원 역할 또는 Handling 태그가 없거나 중복됩니다.")
                resolved[thread.parent_id] = (forum, roles[0].id, tags[0])
            if target := resolved[thread.parent_id]:
                thread = await _handle_one(client, thread, *target, settings)
        except (discord.DiscordException, ValueError):
            logger.exception("자동 Handling 실패: 글 %s — 기존 상태 유지", thread.id)
        result.append(thread)
    return result
