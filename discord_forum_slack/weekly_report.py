"""주간 현황 보고: 공통 테이블 동기화 결과 집계, 긴 목록 분할, 발송 진행 상태 보관."""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import discord
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from . import discord_bot, progress
from .auto_solve import _last_human_message
from .config import WEEKDAYS, Config, WeeklyReportConfig
from .msg_sender import _escape
from .slack_list import _fetch_items, _get_schema, _item_key, _thread_key

logger = logging.getLogger(__name__)
STATE_PATH = Path("/app/data/weekly_report.json")
_manual_lock = asyncio.Lock()


def next_slot(now: datetime, settings: WeeklyReportConfig) -> datetime:
    """now 이상인 다음 예약 시각. 최초 활성화 때 과거 주의 보고를 보내지 않는다."""
    local = now.astimezone(ZoneInfo(settings.timezone))
    hour, minute = map(int, settings.time.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate += timedelta(days=(WEEKDAYS.index(settings.weekday) - local.weekday()) % 7)
    if candidate < local:
        candidate += timedelta(days=7)
    return candidate


def latest_slot(now: datetime, settings: WeeklyReportConfig) -> datetime:
    upcoming = next_slot(now, settings)
    return upcoming if upcoming == now else upcoming - timedelta(days=7)


def _client(token: str, *, posting: bool = False) -> WebClient:
    # 전송 중 연결이 끊기면 성공 여부를 알 수 없으므로 POST 자동 재시도는 429에만 허용.
    client = WebClient(token=token, retry_handlers=[] if posting else None)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))
    return client


def read_assignments(config: Config) -> dict[tuple[str, str], list[str]]:
    client = _client(config.slack.user_token or config.slack.bot_token)
    columns, _, assign = _get_schema(client, config.table_sync.list_id)
    if not assign or assign.get("type") not in {"user", "todo_assignee"}:
        raise RuntimeError("주간 담당자 집계에는 사람 유형의 assign 열이 필요합니다.")
    result = {}
    for item in _fetch_items(client, config.table_sync.list_id):
        key = _item_key(item, columns["thread_link"])
        field = next(
            (
                f
                for f in item.get("fields", [])
                if (f.get("column_id") or f.get("key")) == assign["id"]
            ),
            {},
        )
        users = field.get("user", [])
        if (
            not isinstance(users, list)
            or any(not isinstance(user, str) or not user for user in users)
            or ("user" not in field and field.get("value") not in (None, "", "[]"))
        ):
            raise RuntimeError("assign 값을 읽을 수 없어 주간 보고를 중단합니다.")
        users = sorted(set(users))
        if key is None:
            if users:
                raise RuntimeError("담당자가 있는 행의 Discord Link를 확인해주세요.")
            continue
        if key in result and result[key] != users:
            raise RuntimeError("중복된 글의 assign 값이 달라 주간 보고를 중단합니다.")
        result[key] = users
    return result


def assignment_counts(issues: list[dict], assignments: dict) -> tuple[Counter, int, int, int]:
    counts = Counter()
    assigned = unassigned = unknown = 0
    seen = set()
    for issue in issues:
        key = _thread_key(issue["url"])
        if key is None or key in seen:
            raise RuntimeError("중복 또는 잘못된 Discord 글 링크입니다.")
        seen.add(key)
        if key not in assignments:
            unknown += 1
        elif not assignments[key]:
            unassigned += 1
        else:
            assigned += 1
            counts.update(set(assignments[key]))
    return counts, assigned, unassigned, unknown


def resolve_names(config: Config, users) -> dict[str, str]:
    """이름은 일반 텍스트로 표시. 권한이 없으면 Slack이 해석하는 사용자 멘션 사용."""
    clients = [
        _client(token)
        for token in dict.fromkeys((config.slack.bot_token, config.slack.user_token))
        if token
    ]
    names = {}
    for user_id in users:
        for client in list(clients):
            try:
                user = client.users_info(user=user_id)["user"]
                profile = user.get("profile", {})
                name = profile.get("display_name") or profile.get("real_name") or user.get("name")
                if name:
                    names[user_id] = _escape(name.replace("\n", " "))
                    break
            except SlackApiError as exc:
                error = exc.response.get("error")
                logger.warning("주간 보고 이름 조회 실패: %s", error)
                if error in {"missing_scope", "invalid_auth", "token_revoked"}:
                    clients.remove(client)
        names.setdefault(user_id, f"<@{user_id}>")
    return names


def _pack_lines(lines: list[str], limit: int = 3500) -> list[str]:
    parts, current = [], ""
    for line in lines:
        if len(line) > limit:
            raise RuntimeError("주간 보고의 한 항목이 Slack 메시지 길이 제한을 초과합니다.")
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


async def read_reply_activity(snapshot, config: Config) -> dict[str, tuple[datetime, bool]]:
    """New 글의 마지막 사람 메시지와 현재 직원 역할 여부를 조회한다."""
    threads = {discord_bot._discord_url(thread): thread for thread in snapshot.threads}
    role_ids = {}
    staff_members = {}
    activity = {}
    settings = config.auto_solve
    for issue in snapshot.issues:
        if issue["status"] != "New Issue":
            continue
        thread = threads.get(issue["url"])
        if thread is None:
            continue
        guild = thread.guild
        try:
            if guild.id not in role_ids:
                role_ids[guild.id] = None
                roles = [
                    role
                    for role in await guild.fetch_roles()
                    if (
                        str(role.id) == settings.robotis_role_id
                        if settings.robotis_role_id
                        else role.name.casefold() == settings.robotis_role_name.casefold()
                    )
                ]
                if len(roles) != 1:
                    raise ValueError("직원 역할을 하나로 확인할 수 없습니다.")
                role_ids[guild.id] = roles[0].id
            role_id = role_ids[guild.id]
            if role_id is None:
                continue
            last = await _last_human_message(thread)
            if last is None:
                continue
            key = (guild.id, last.author.id)
            if key not in staff_members:
                staff_members[key] = None
                member = await guild.fetch_member(last.author.id)
                staff_members[key] = any(role.id == role_id for role in member.roles)
            is_staff = staff_members[key]
            if is_staff is not None:
                activity[issue["url"]] = (last.created_at, is_staff)
        except (discord.DiscordException, ValueError) as exc:
            logger.warning("보고 답변 상태 조회 실패: 글 %s (%s)", thread.id, type(exc).__name__)
    return activity


def build_messages(
    snapshot, assignments: dict, names: dict, now: datetime, zone: str, activity=None
) -> list[str]:
    counts = snapshot.counts
    people, assigned, unassigned, unknown = assignment_counts(snapshot.issues, assignments)
    local = now.astimezone(ZoneInfo(zone))
    lines = [
        f"*📊 주간 지원 현황 · {local:%Y-%m-%d} ({zone})*",
        f"✅Solved(완료): {counts['solved']}개 · 🟡Handling: {counts['handling']}개 · "
        f"🟢New Issue: {counts['new_issue']}개",
        "",
        "*담당자별 배정 현황 — 현재 미해결 글 기준*",
        f"담당자 {len(people)}명 · 배정 {assigned}개 · 미배정 {unassigned}개",
    ]
    if unknown:
        lines.append(f"테이블 미반영 {unknown}개: 배정 여부 확인 불가 (미배정 집계에서 제외)")
    for user_id, count in sorted(people.items(), key=lambda pair: (-pair[1], names[pair[0]])):
        lines.append(f"• {names[user_id]}: {count}개")
    if sum(people.values()) > assigned:
        lines.append("공동 담당 글은 각 담당자에게 1개씩 집계합니다.")
    new = sorted(
        (issue for issue in snapshot.issues if issue["status"] == "New Issue"),
        key=lambda issue: issue["created_at"],
        reverse=True,
    )
    lines += ["", f"*🟢 현재 New Issue 목록 ({len(new)}개)*"]
    if not new:
        lines.append("현재 New Issue가 없습니다.")
    for issue in new:
        date = issue["created_at"].astimezone(ZoneInfo(zone))
        title = _escape(issue["title"].replace("\n", " ").replace("|", "｜"))
        reply = (activity or {}).get(issue["url"])
        elapsed = "답변 상태 확인 불가"
        if reply is not None:
            last_at, is_staff = reply
            days = max(0, (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).days)
            duration = f"{days}일 경과" if days else "1일 미만"
            elapsed = f"{'직원 답변 후' if is_staff else '미답변'} {duration}"
        lines.append(f"• <{issue['url']}|{title}> — {date:%Y-%m-%d} · {elapsed}")
    parts = _pack_lines(lines)
    if len(parts) > 1:
        parts = [f"({i}/{len(parts)})\n{part}" for i, part in enumerate(parts, 1)]
    return parts


async def collect_messages(config: Config, client, now: datetime) -> list[str]:
    if not config.table_sync.enabled:
        raise ValueError("보고 전 테이블 갱신을 위해 table_sync.enabled를 켜주세요.")
    # 갱신과 보고 자료 수집 사이에 다른 동기화가 끼어들지 못하도록 같은 락을 사용한다.
    if discord_bot._sync_lock.locked():
        progress.emit("queue", "⏳ 다른 테이블 동기화가 끝나기를 기다리는 중")
    async with discord_bot._sync_lock:
        progress.emit("queue", "✅ 동기화 작업 시작")
        snapshot = await discord_bot._sync_list_locked(config, client)
        assignments = await asyncio.to_thread(read_assignments, config)
        progress.emit("report", "⏳ 담당자 집계·New Issue 대기 일수 확인 중")
        activity = await read_reply_activity(snapshot, config)
    people, _, _, _ = assignment_counts(snapshot.issues, assignments)
    names = await asyncio.to_thread(resolve_names, config, people)
    progress.emit("report", "✅ 담당자 집계·보고 내용 준비 완료")
    return build_messages(
        snapshot, assignments, names, now, config.weekly_report.timezone, activity
    )


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def post_parts(client, channel: str, pending: dict, save_progress) -> None:
    if pending["sending"]:
        raise RuntimeError("발송 결과 확인 필요: 중복 방지를 위해 재전송을 보류합니다.")
    while pending["index"] < len(pending["parts"]):
        index = pending["index"]
        pending["sending"] = True
        save_progress()
        try:
            response = client.chat_postMessage(
                channel=channel,
                text=pending["parts"][index],
                thread_ts=pending["thread_ts"],
                client_msg_id=str(uuid5(NAMESPACE_URL, f"{channel}/{pending['slot']}/{index}")),
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackApiError as exc:
            # 5xx/internal_error는 일부 처리가 성공했을 수 있다. 확실한 거부만 재시도.
            if exc.response.status_code < 500 and exc.response.get("error") not in {
                "internal_error",
                "fatal_error",
                "request_timeout",
                "service_unavailable",
            }:
                pending["sending"] = False
                save_progress()
            raise
        pending["thread_ts"] = pending["thread_ts"] or response["ts"]
        pending["index"] += 1
        pending["sending"] = False
        save_progress()


class WeeklyReporter:
    def __init__(self, config: Config, client, path: Path = STATE_PATH):
        self.config, self.client, self.path = config, client, path
        self.state = None

    def initialize(self, now: datetime) -> None:
        settings = self.config.weekly_report
        signature = [
            settings.weekday,
            settings.time,
            settings.timezone,
            settings.channel_id or self.config.notifications.channel_id,
            self.config.table_sync.list_id,
        ]
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.state, dict) or self.state.get("version") != 1:
                raise RuntimeError("주간 보고 발송 기록이 손상되었습니다.")
            # 손상된 기록을 초기화하면 재발송할 수 있으므로 오류로 중단한다.
            if datetime.fromisoformat(self.state["next_due"]).tzinfo is None:
                raise RuntimeError("주간 보고 예약 시각에 시간대가 없습니다.")
            if self.state["signature"] != signature and self.state.get("pending"):
                raise RuntimeError("미완료 주간 보고가 있어 대상/일정 변경을 적용할 수 없습니다.")
        if self.state is None or self.state["signature"] != signature:
            self.state = {
                "version": 1,
                "signature": signature,
                "next_due": next_slot(now, settings).isoformat(),
                "pending": None,
            }
            save_state(self.path, self.state)
        logger.info("주간 보고 다음 예약: %s", self.state["next_due"])

    async def tick(self, now: datetime) -> None:
        if self.state is None:
            self.initialize(now)
        if now < datetime.fromisoformat(self.state["next_due"]):
            return
        pending = self.state["pending"]
        if pending is None:
            parts = await collect_messages(self.config, self.client, now)
            slot = latest_slot(now, self.config.weekly_report)
            pending = {
                "slot": slot.isoformat(),
                "parts": parts,
                "index": 0,
                "thread_ts": None,
                "sending": False,
            }
            self.state["pending"] = pending
            save_state(self.path, self.state)
        if pending["sending"]:
            raise RuntimeError(
                "주간 보고 발송 결과 확인 필요: 중복 방지를 위해 자동 재전송을 보류합니다."
            )
        await asyncio.to_thread(self._send, pending)

    def _send(self, pending: dict) -> None:
        client = _client(self.config.slack.bot_token, posting=True)
        channel = self.state["signature"][3]
        post_parts(client, channel, pending, lambda: save_state(self.path, self.state))
        completed_at = max(datetime.now(timezone.utc), datetime.fromisoformat(pending["slot"]))
        self.state["next_due"] = next_slot(
            completed_at + timedelta(seconds=1),
            self.config.weekly_report,
        ).isoformat()
        self.state["pending"] = None
        save_state(self.path, self.state)
        logger.info("주간 보고 발송 완료: %s, %s개 메시지", pending["slot"], len(pending["parts"]))


async def _run_scheduler(config: Config, client) -> None:
    if not config.weekly_report.enabled:
        return
    import fcntl

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 동일 data 볼륨의 두 프로세스가 동시에 보고를 보내는 것을 방지한다.
    with STATE_PATH.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("다른 프로세스가 주간 보고를 실행 중입니다.")
            return
        reporter = WeeklyReporter(config, client)
        try:
            reporter.initialize(datetime.now(timezone.utc))
        except Exception:
            logger.exception("주간 보고 초기화 실패: 다른 봇 기능은 계속 실행합니다.")
            return
        while not client.is_closed():
            try:
                if client.is_ready():
                    await reporter.tick(datetime.now(timezone.utc))
            except Exception:
                logger.exception("주간 보고 실패")
                await asyncio.sleep(240)
            await asyncio.sleep(60)


async def run_weekly_report(config: Config, client) -> None:
    try:
        await _run_scheduler(config, client)
    except Exception:
        logger.exception("주간 보고 스케줄러 중단: 다른 봇 기능은 계속 실행합니다.")


class ReportBusyError(RuntimeError):
    pass


async def send_manual_report(config: Config, client, request_id: str) -> int:
    """명령 요청별로 발송 진행을 저장하며 자동 발송 기록은 변경하지 않는다."""
    if not request_id:
        raise ValueError("Slack 요청 ID가 없습니다.")
    if _manual_lock.locked():
        raise ReportBusyError("다른 수동 보고를 발송 중입니다. 완료 후 다시 실행해주세요.")
    async with _manual_lock:
        channel = config.weekly_report.channel_id or config.notifications.channel_id
        if not (channel and config.table_sync.list_id and config.slack.bot_token):
            raise ValueError("보고 채널, List ID, Bot Token 설정을 확인해주세요.")
        key = hashlib.sha256(request_id.encode()).hexdigest()
        path = STATE_PATH.parent / "manual_reports" / f"{key}.json"
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("version") != 1 or state.get("channel") != channel:
                raise RuntimeError("수동 보고 기록과 현재 채널 설정이 다릅니다.")
        else:
            parts = await collect_messages(config, client, datetime.now(timezone.utc))
            state = {
                "version": 1,
                "channel": channel,
                "pending": {
                    "slot": f"manual:{request_id}",
                    "parts": parts,
                    "index": 0,
                    "thread_ts": None,
                    "sending": False,
                },
            }
            save_state(path, state)
        pending = state["pending"]
        progress.emit("delivery", f"⏳ 보고 발송 중: {len(pending['parts'])}개 메시지")
        await asyncio.to_thread(
            post_parts,
            _client(config.slack.bot_token, posting=True),
            channel,
            pending,
            lambda: save_state(path, state),
        )
        logger.info("수동 주간 보고 발송 완료: %s개 메시지", len(pending["parts"]))
        progress.emit("delivery", f"✅ 보고 {len(pending['parts'])}개 메시지 발송 완료")
        return len(pending["parts"])
