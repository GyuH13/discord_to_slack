"""Slack Socket Mode 봇."""

import logging

import discord
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from .config import Config
from .discord_bot import run_sync_list
from .progress import CommandProgress
from .weekly_report import ReportBusyError, send_manual_report

logger = logging.getLogger(__name__)


def create_slack_bot(config: Config, discord_client: discord.Client) -> AsyncSocketModeHandler:
    app = AsyncApp(token=config.slack.bot_token)

    async def handle_report(command, respond) -> None:
        if not config.weekly_report.enabled:
            await respond("주간 보고 기능이 꺼져 있습니다. weekly_report.enabled를 확인해주세요.")
            return
        if not config.table_sync.enabled:
            await respond("보고 전 테이블 갱신을 위해 table_sync.enabled를 켜주세요.")
            return
        if not discord_client.is_ready():
            await respond("Discord 봇이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.")
            return
        channel = config.weekly_report.channel_id or config.notifications.channel_id
        async with CommandProgress(respond, "테이블 갱신 및 보고") as status:
            try:
                request_id = command.get("trigger_id", "")
                await send_manual_report(config, discord_client, request_id)
            except ReportBusyError as exc:
                await status.finish(str(exc))
            except Exception:
                logger.exception("수동 주간 보고 실패")
                await status.finish(
                    "❌ 보고서 발송 중 오류가 발생했습니다. 일부 메시지가 전송됐을 수 있으니 "
                    "보고 채널과 봇 로그를 확인해주세요. 자동 발송 일정은 유지됩니다."
                )
            else:
                await status.finish(
                    f"✅ 보고서 발송 완료: <#{channel}> · 월요일 자동 발송 일정은 유지됩니다."
                )

    @app.command("/report")
    async def handle_weekly_report(ack, respond, command) -> None:
        await ack()
        if command.get("text", "").strip():
            await respond("사용법: /report")
            return
        await handle_report(command, respond)

    @app.command("/table_update")
    async def handle_sync(ack, respond, command) -> None:
        await ack()
        if command.get("text", "").strip():
            await respond("사용법: /table_update · 현황 보고는 /report를 사용해주세요.")
            return
        if not config.table_sync.enabled:
            await respond("테이블 동기화 기능이 꺼져 있습니다.")
            return
        if not discord_client.is_ready():
            await respond("Discord 봇이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.")
            return

        async with CommandProgress(respond, "리스트 동기화") as status:
            try:
                count = await run_sync_list(config, discord_client)
                await status.finish(f"✅ 동기화 완료: {count}개 이슈를 반영했습니다.")
            except Exception:
                logger.exception("리스트 동기화 실패")
                await status.finish("❌ 동기화 실패: 위 진행 단계와 봇 로그를 확인해주세요.")

    return AsyncSocketModeHandler(app, config.slack.app_token)
