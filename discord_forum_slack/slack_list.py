"""Slack List 동기화."""

import logging
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from slack_sdk import WebClient

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


def _get_schema(client: WebClient, list_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """Slack List 컬럼 ID와 select 옵션 값 매핑을 반환합니다."""
    response = client.api_call(
        "slackLists.items.list",
        json={"list_id": list_id, "limit": 1, "include_list": True},
    )
    schema = response.get("list", {}).get("list_metadata", {}).get("schema", [])
    if not schema:
        raise RuntimeError(
            "Slack List 스키마를 가져올 수 없어 동기화를 중단합니다.",
        )

    column_ids: dict[str, str] = {}
    select_ids: dict[str, str] = {}
    for col in schema:
        if key := COLUMN_NAMES.get(col.get("name", "")):
            column_ids[key] = col["id"]
            if key == "status":
                for choice in col.get("options", {}).get("choices", []):
                    select_ids[choice["label"]] = choice["value"]

    if "thread_link" not in column_ids:
        raise RuntimeError("Slack List에 글 식별에 필요한 Link 열이 없습니다.")

    return column_ids, select_ids


def _list_description_blocks(status_counts: dict[str, int]) -> list[dict]:
    """slackLists.update 의 description_blocks 형식 (Block Kit rich_text)."""
    text = (
        f"✅Solved(완료): {status_counts['solved']}개 · "
        f"🟡Handling: {status_counts['handling']}개 · "
        f"🟢New Issue: {status_counts['new_issue']}개"
    )
    return [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": text}],
                },
            ],
        },
    ]


def _as_rich_text_cell(content: str) -> list[dict]:
    return [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": content}],
                },
            ],
        },
    ]


def _make_fields(
    thread: dict, column_ids: dict[str, str], select_ids: dict[str, str]
) -> list[dict]:
    fields: list[dict] = []

    if cid := column_ids.get("title"):
        fields.append({"column_id": cid, "rich_text": _as_rich_text_cell(thread["title"])})

    if (cid := column_ids.get("status")) and (opt := select_ids.get(thread["status"])):
        fields.append({"column_id": cid, "select": [opt]})

    if (cid := column_ids.get("msg")) and (slack_url := thread.get("slack_msg_url")):
        fields.append({"column_id": cid, "message": [slack_url]})

    if cid := column_ids.get("thread_link"):
        fields.append(
            {
                "column_id": cid,
                "link": [{"original_url": thread["url"], "display_name": thread["url"]}],
            }
        )

    if cid := column_ids.get("date"):
        date_str = (
            thread["created_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M (KST)")
        )
        fields.append({"column_id": cid, "rich_text": _as_rich_text_cell(date_str)})

    return fields


def _thread_key(url: str) -> tuple[str, str] | None:
    """표시명, 쿼리, 끝 슬래시에 영향받지 않는 Discord 글 식별자."""
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.hostname in {"discord.com", "www.discord.com", "discordapp.com"}
        and len(parts) == 3
        and parts[0] == "channels"
        and parts[1].isdigit()
        and parts[2].isdigit()
    ):
        return parts[1], parts[2]
    return None


def _item_key(item: dict, link_column: str) -> tuple[str, str] | None:
    for field in item.get("fields", []):
        if (field.get("column_id") or field.get("key")) == link_column:
            links = field.get("link", [])
            if len(links) == 1:
                link = links[0]
                return _thread_key(link.get("originalUrl") or link.get("original_url") or "")
    return None


def _without_block_ids(value):
    if isinstance(value, list):
        return [_without_block_ids(v) for v in value]
    if isinstance(value, dict):
        return {k: _without_block_ids(v) for k, v in value.items() if k != "block_id"}
    return value


def _field_value(field: dict, kind: str):
    """Slack 응답과 요청의 필드 형식을 비교용으로 정규화한다."""
    values = field.get(kind, [])
    if kind == "rich_text":
        return _without_block_ids(values)
    if kind == "message":
        return [v.get("value") if isinstance(v, dict) else v for v in values]
    if kind == "link":
        return [
            (
                v.get("originalUrl") or v.get("original_url"),
                v.get("displayName") or v.get("display_name"),
            )
            for v in values
        ]
    return values


def _changed_cells(item: dict, desired: list[dict]) -> list[dict]:
    existing = {
        field.get("column_id") or field.get("key"): field for field in item.get("fields", [])
    }
    cells = []
    for field in desired:
        kind = next(k for k in field if k != "column_id")
        old = existing.get(field["column_id"], {})
        if _field_value(old, kind) != _field_value(field, kind):
            cells.append({"row_id": item["id"], **field})
    return cells


def sync_list(
    *,
    slack_bot_token: str,
    slack_user_token: str = "",
    list_id: str,
    threads: list[dict],
    status_counts: dict[str, int],
    solved_urls: set[str] | None = None,
) -> None:
    client = WebClient(token=slack_user_token or slack_bot_token)
    column_ids, select_ids = _get_schema(client, list_id)

    existing = _fetch_items(client, list_id)
    by_key: dict[tuple[str, str], list[dict]] = {}
    for item in existing:
        if key := _item_key(item, column_ids["thread_link"]):
            by_key.setdefault(key, []).append(item)

    # 모든 입력을 먼저 검증한다. 잘못된 스키마/입력으로 일부만 변경하지 않는다.
    desired_by_key = {}
    for thread in threads:
        key = _thread_key(thread["url"])
        if key is None or key in desired_by_key:
            raise RuntimeError("Discord 글 링크가 잘못되었거나 중복되었습니다.")
        if "status" in column_ids and thread["status"] not in select_ids:
            raise RuntimeError(f"Slack Status 열에 {thread['status']} 선택 옵션이 없습니다.")
        desired_by_key[key] = thread

    solved_keys = {_thread_key(url) for url in (solved_urls or set())} - {None}

    for thread in sorted(threads, key=lambda x: x["created_at"], reverse=True):
        key = _thread_key(thread["url"])
        fields = _make_fields(thread, column_ids, select_ids)
        matches = by_key.get(key, [])
        if matches:
            # 과거 중복 행도 삭제하지 않고 각 행의 수동 필드를 유지한다.
            for item in matches:
                if cells := _changed_cells(item, fields):
                    client.api_call(
                        "slackLists.items.update",
                        json={"list_id": list_id, "cells": cells},
                    )
                    time.sleep(0.3)
        else:
            client.api_call(
                "slackLists.items.create",
                json={
                    "list_id": list_id,
                    "initial_fields": fields,
                },
            )
            time.sleep(0.3)

    # 조회에서 빠진 글/수동 행은 보존하고, 명시적으로 해결된 글만 삭제한다.
    for key in solved_keys - desired_by_key.keys():
        for item in by_key.get(key, []):
            client.api_call(
                "slackLists.items.delete",
                json={"list_id": list_id, "id": item["id"]},
            )
            time.sleep(0.3)

    client.api_call(
        "slackLists.update",
        json={
            "id": list_id,
            "description_blocks": _list_description_blocks(status_counts),
        },
    )

    logger.info("리스트 동기화 완료: %s개", len(threads))
