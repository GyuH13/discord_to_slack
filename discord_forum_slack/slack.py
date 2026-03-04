"""Send message to slack."""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests


def _slack_escape(text: str) -> str:
    """escape <, >, & in Slack mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_to_slack_message(
    *,
    webhook_url: str,
    title: str,
    content: str,
    author: str,
    url: str,
    forum_name: str,
    tags: list[str] | None = None,
    attachment_urls: list[str] | None = None,
) -> None:
    """send forum post to slack. attachment_urls: image URLs to show as image blocks."""
    if not content and (attachment_urls or []):
        content_escaped = "_Attachments only_"
    else:
        content = content or "(no content)"
        content_escaped = _slack_escape(content)[:2900]
        if len(content) > 2900:
            content_escaped += "…"

    tag_list = tags or []
    tags_text = ", ".join(_slack_escape(t) for t in tag_list) if tag_list else "—"
    urls = attachment_urls or []

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
                {"type": "mrkdwn", "text": f"*📂포럼:*\n{_slack_escape(forum_name)}"},
                {"type": "mrkdwn", "text": f"*👤작성자:*\n{_slack_escape(author)}"},
                {"type": "mrkdwn", "text": f"*🏷️태그:*\n{tags_text}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📝제목*\n{_slack_escape(title)}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*💬본문*\n{content_escaped}"},
        },
    ]
    for img_url in urls[:5]:
        blocks.append({"type": "image", "image_url": img_url, "alt_text": "attachment"})

    payload = {
        "text": "*디스코드 support 채널에 새로운 도움 요청 스레드가 올라왔습니다!*",
        "blocks": blocks,
    }

    resp = requests.post(
        webhook_url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()


def send_to_trigger_webhook(
    *,
    webhook_url: str,
    title: str,
    url: str,
    field_tag: list[str] | None = None,
    status_tag: list[str] | None = None,
    created_at: datetime,
) -> None:
    """send to trigger webhook."""
    created_readable = created_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M (KST)")
    tags = field_tag or []
    payload = {
        "title": title,
        "url": url,
        "field_tag": ", ".join(tags),
        "status_tag": status_tag,
        "created_at": created_readable,
    }

    resp = requests.post(
        webhook_url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
