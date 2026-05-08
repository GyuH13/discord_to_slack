"""Slack List 동기화."""

import logging
import time
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

COLUMN_NAMES = {
    "Issue Title": "title",
    "Status": "status",
    "Msg Thread": "msg",
    "Link": "thread_link",
    "Posted Date": "date",
}


def _fetch_items(client: WebClient, list_id: str) -> list[dict]:
    items: list[dict] = []
    cursor: str | None = None
    while True:
        payload: dict = {"list_id": list_id, "limit": 100}
        if cursor:
            payload["cursor"] = cursor
        resp = client.api_call("slackLists.items.list", json=payload)
        items.extend(resp.get("items", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return items


def _get_schema(client: WebClient, list_id: str) -> tuple[dict, dict]:
    """컬럼 ID와 select 옵션 ID를 반환."""
    items = client.api_call("slackLists.items.list", json={"list_id": list_id, "limit": 1}).get("items", [])
    if not items:
        logger.warning("리스트가 비어있어 스키마를 가져올 수 없습니다.")
        return {}, {}

    schema = (
        client.api_call("slackLists.items.info", json={"list_id": list_id, "id": items[0]["id"]})
        .get("list", {})
        .get("list_metadata", {})
        .get("schema", [])
    )

    column_ids: dict = {}
    select_ids: dict = {}
    for col in schema:
        if key := COLUMN_NAMES.get(col.get("name", "")):
            column_ids[key] = col["id"]
        for choice in col.get("options", {}).get("choices", []):
            select_ids[choice["label"]] = choice["value"]

    return column_ids, select_ids


def _as_text(content: str) -> list[dict]:
    return [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": content}]}]}]


def _make_fields(thread: dict, column_ids: dict, select_ids: dict) -> list[dict]:
    fields: list[dict] = []

    if cid := column_ids.get("title"):
        fields.append({"column_id": cid, "rich_text": _as_text(thread["title"])})

    if (cid := column_ids.get("status")) and (opt := select_ids.get(thread["status"])):
        fields.append({"column_id": cid, "select": [opt]})

    if (cid := column_ids.get("msg")) and (slack_url := thread.get("slack_msg_url")):
        fields.append({"column_id": cid, "link": [{"originalUrl": slack_url, "displayName": "메시지"}]})

    if cid := column_ids.get("thread_link"):
        fields.append({"column_id": cid, "link": [{"originalUrl": thread["url"], "displayName": "스레드"}]})

    if cid := column_ids.get("date"):
        date_str = thread["created_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        fields.append({"column_id": cid, "rich_text": _as_text(date_str)})

    return fields


def sync_list(*, slack_bot_token: str, list_id: str, threads: list[dict]) -> None:
    client = WebClient(token=slack_bot_token)
    column_ids, select_ids = _get_schema(client, list_id)

    existing = _fetch_items(client, list_id)
    if existing:
        ids = [item["id"] for item in existing]
        try:
            client.api_call("slackLists.items.deleteMultiple", json={"list_id": list_id, "ids": ids})
        except SlackApiError:
            for item_id in ids:
                try:
                    client.api_call("slackLists.items.delete", json={"list_id": list_id, "id": item_id})
                except SlackApiError as e:
                    logger.warning("항목 삭제 실패 (%s): %s", item_id, e)

    for thread in sorted(threads, key=lambda x: x["created_at"]):
        try:
            client.api_call("slackLists.items.create", json={
                "list_id": list_id,
                "initial_fields": _make_fields(thread, column_ids, select_ids),
            })
        except SlackApiError as e:
            logger.warning("항목 생성 실패 (%s): %s", thread["title"], e)
        time.sleep(0.3)

    logger.info("리스트 동기화 완료: %s개", len(threads))
