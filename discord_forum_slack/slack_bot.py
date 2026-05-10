"""Slack Socket Mode 봇."""

import asyncio
import logging

import discord
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .config import Config
from .discord_bot import get_open_issues
from .slack_list import sync_list

logger = logging.getLogger(__name__)


def create_slack_bot(config: Config, discord_client: discord.Client) -> AsyncSocketModeHandler:
    app = AsyncApp(token=config.slack_bot_token)

    @app.command("/table_update")
    async def handle_sync(ack, respond) -> None:
        await ack()

        if not discord_client.is_ready():
            await respond("Discord 봇이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.")
            return

        await respond("리스트 동기화를 시작합니다...")
        try:
            issues = await get_open_issues(discord_client, config)
            await asyncio.to_thread(
                sync_list,
                slack_bot_token=config.slack_bot_token,
                slack_user_token=config.slack_user_token,
                list_id=config.list_id,
                threads=issues,
            )
            await respond(f"동기화 완료: {len(issues)}개 이슈를 반영했습니다.")
        except Exception as e:
            logger.exception("리스트 동기화 실패")
            await respond(f"동기화 실패: {e}")

    return AsyncSocketModeHandler(app, config.slack_app_token)
