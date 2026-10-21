"""Slack List 동기화."""

import logging
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from . import assign_store
from .progress import emit

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


def _get_schema(
    client: WebClient, list_id: str
) -> tuple[dict[str, str], dict[str, str], dict | None]:
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
    assign_column = None
    for col in schema:
        if col.get("name", "").strip().casefold() == "assign":
            if assign_column is not None:
                raise RuntimeError("assign 열이 중복되어 재작성을 중단합니다.")
            assign_column = col
        if key := COLUMN_NAMES.get(col.get("name", "")):
            column_ids[key] = col["id"]
            if key == "status":
                for choice in col.get("options", {}).get("choices", []):
                    select_ids[choice["label"]] = choice["value"]

    if "thread_link" not in column_ids:
        raise RuntimeError("Slack List에 글 식별에 필요한 Link 열이 없습니다.")

    return column_ids, select_ids, assign_column


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


def _capture_assignments(
    list_id: str,
    column: dict,
    items: list[dict],
    link_column: str,
) -> tuple[str, dict[str, list]]:
    kind = {"user": "user", "todo_assignee": "user", "select": "select", "text": "rich_text"}.get(
        column.get("type")
    )
    if kind is None:
        raise RuntimeError("assign 열은 사람, 선택 또는 텍스트 유형이어야 합니다.")
    assignments = assign_store.load(list_id, column["id"], kind)
    current = {}
    for item in items:
        field = next(
            (
                f
                for f in item.get("fields", [])
                if (f.get("column_id") or f.get("key")) == column["id"]
            ),
            {},
        )
        values = field.get(kind, [])
        if kind not in field and field.get("value") not in (None, "", "[]"):
            raise RuntimeError("assign 값을 읽을 수 없어 재작성을 중단합니다.")
        if not isinstance(values, list) or not all(
            isinstance(v, dict if kind == "rich_text" else str) for v in values
        ):
            raise RuntimeError("assign 값의 형식이 잘못되어 재작성을 중단합니다.")
        values = _without_block_ids(values)
        key = _item_key(item, link_column)
        if key is None:
            if values:
                raise RuntimeError("assign이 지정된 행의 Discord Link를 확인해주세요.")
            continue
        backup_key = "/".join(key)
        if backup_key in current and current[backup_key] != values:
            raise RuntimeError("같은 Discord 글의 중복 행에 서로 다른 assign 값이 있습니다.")
        # 현재 행의 빈칸도 반영한다. 사용자가 지운 담당자를 옛 백업에서 되살리지 않는다.
        current[backup_key] = values
    # 실패로 아직 재생성되지 않은 행은 파일의 이전 값을 유지한다.
    assignments.update(current)
    return kind, assignments


def sync_list(
    *,
    slack_bot_token: str,
    slack_user_token: str = "",
    list_id: str,
    threads: list[dict],
    status_counts: dict[str, int],
) -> None:
    emit("assign", "⏳ 기존 테이블과 assign 조회·검증 중")
    client = WebClient(token=slack_user_token or slack_bot_token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))
    column_ids, select_ids, assign_column = _get_schema(client, list_id)
    existing = _fetch_items(client, list_id)

    assignments = {}
    assign_kind = None
    if assign_column:
        assign_kind, assignments = _capture_assignments(
            list_id,
            assign_column,
            existing,
            column_ids["thread_link"],
        )

    # 삭제 전에 모든 재생성 데이터를 검증하고 구성한다.
    planned = []
    seen = set()
    for thread in sorted(threads, key=lambda x: x["created_at"], reverse=True):
        key = _thread_key(thread["url"])
        if key is None or key in seen:
            raise RuntimeError("Discord 글 링크가 잘못되었거나 중복되었습니다.")
        seen.add(key)
        if "status" in column_ids and thread["status"] not in select_ids:
            raise RuntimeError(f"Slack Status 열에 {thread['status']} 선택 옵션이 없습니다.")
        fields = _make_fields(thread, column_ids, select_ids)
        if assign_column:
            values = assignments.get("/".join(key), [])
            if assign_kind == "select":
                choices = {c["value"] for c in assign_column.get("options", {}).get("choices", [])}
                if any(value not in choices for value in values):
                    raise RuntimeError("백업된 assign 선택 옵션이 없어 재작성을 중단합니다.")
            fields.append({"column_id": assign_column["id"], assign_kind: values})
        planned.append(fields)
    description = _list_description_blocks(status_counts)

    # 백업을 디스크에 완전히 저장한 뒤에만 첫 삭제를 실행한다.
    if assign_column:
        assign_store.save(list_id, assign_column["id"], assign_kind, assignments)
    emit("assign", "✅ assign 백업 완료" if assign_column else "➖ assign 열 없음")

    logger.info(
        "전체 재작성 시작: 기존 %s행 삭제 → %s행 생성 (assign 백업: %s)",
        len(existing),
        len(planned),
        "완료" if assign_column else "열 없음",
    )
    emit("delete", f"⏳ 기존 행 삭제: 0/{len(existing)}")
    for index, item in enumerate(existing, 1):
        client.api_call("slackLists.items.delete", json={"list_id": list_id, "id": item["id"]})
        emit("delete", f"⏳ 기존 행 삭제: {index}/{len(existing)}")
        time.sleep(0.3)
    emit("delete", f"✅ 기존 행 {len(existing)}개 삭제 완료")
    if not planned:
        emit("write_mid", "⏳ 새 행 작성: 0/0")
    for index, fields in enumerate(planned, 1):
        client.api_call(
            "slackLists.items.create",
            json={"list_id": list_id, "initial_fields": fields},
        )
        emit("write", f"⏳ 새 행 작성 및 assign 복원: {index}/{len(planned)}")
        if index == max(1, len(planned) // 2):
            emit("write_mid", "⏳ 테이블 재작성 진행 중")
        time.sleep(0.3)

    emit("write", f"✅ 새 행 {len(planned)}개 작성 완료")
    emit("write_mid", "✅ 테이블 행 재작성 완료")

    client.api_call(
        "slackLists.update",
        json={"id": list_id, "description_blocks": description},
    )
    logger.info("전체 재작성 완료: %s개 이슈, assign 복원", len(threads))
    emit("table_done", "✅ 테이블 상태 집계 갱신 완료")
