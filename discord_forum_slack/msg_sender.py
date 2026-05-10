"""Slack 메시지 전송."""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)
_RETRY_COUNT = 3
_RETRY_DELAY = 2.0


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _post(url: str, payload: dict) -> None:
    headers = {"Content-Type": "application/json"}
    for attempt in range(_RETRY_COUNT):
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.ok:
            return
        if attempt < _RETRY_COUNT - 1:
            logger.warning(
                "webhook 시도 %s/%s 실패 (%s): %s",
                attempt + 1,
                _RETRY_COUNT,
                resp.status_code,
                resp.text,
            )
            time.sleep(_RETRY_DELAY)
        else:
            resp.raise_for_status()


def post_message(
    *,
    slack_bot_token: str,
    channel_id: str,
    title: str,
    author: str,
    url: str,
    forum_name: str,
    tags: list[str] | None = None,
) -> str:
    """Slack 채널에 메시지를 전송하고 permalink를 반환."""
    tags_text = ", ".join(_escape(t) for t in tags) if tags else "—"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*디스코드 support 채널에 새로운 도움 요청 스레드가 올라왔습니다!*\n<{url}|해당 스레드를 Discord에서 확인하기>",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*📂포럼:*\n{_escape(forum_name)}"},
                {"type": "mrkdwn", "text": f"*👤작성자:*\n{_escape(author)}"},
                {"type": "mrkdwn", "text": f"*🏷️태그:*\n{tags_text}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📝제목*\n{_escape(title)}"},
        },
    ]

    client = WebClient(token=slack_bot_token)
    last_error: Exception | None = None
    for attempt in range(_RETRY_COUNT):
        try:
            resp = client.chat_postMessage(
                channel=channel_id,
                text="*디스코드 support 채널에 새로운 도움 요청 스레드가 올라왔습니다!*",
                blocks=blocks,
            )
            return client.chat_getPermalink(channel=resp["channel"], message_ts=resp["ts"])["permalink"]
        except SlackApiError as e:
            last_error = e
            if attempt < _RETRY_COUNT - 1:
                logger.warning("post_message 시도 %s/%s 실패: %s", attempt + 1, _RETRY_COUNT, e)
                time.sleep(_RETRY_DELAY)
    raise last_error


def post_webhook(
    *,
    webhook_url: str,
    title: str,
    url: str,
    field_tag: list[str] | None = None,
    status_tag: list[str] | None = None,
    created_at: datetime,
) -> None:
    payload = {
        "title": title,
        "url": url,
        "field_tag": ", ".join(field_tag or []),
        "status_tag": status_tag,
        "created_at": created_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M (KST)"),
    }
    _post(webhook_url, payload)
