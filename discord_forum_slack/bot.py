"""Discord Bot Client."""

import asyncio
import logging

import discord
from discord import app_commands, Thread

from .config import Config, load_config
from .slack import send_to_slack_message, send_to_trigger_webhook

logger = logging.getLogger(__name__)

# --- Constants ---
FIELD_TAG = ["dynamixel", "ai-worker", "omy", "omx", "hand","turtlebot","others"]
STATUS_TAG_LABEL: dict[str, str] = {
    "🟢New": "New Issue",
    "🟡Handling": "Handling",
    "✅Solved": "Complete",
    "New": "New Issue",
    "Handling": "Handling",
    "Solved": "Complete",
}

# --- Client ---
def _create_client() -> discord.Client:
    """create discord client with configured intents."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    return discord.Client(intents=intents)

# --- Forum/Thread helpers ---
def _tags_from_thread(t: discord.Thread) -> list[str]:
    out: list[str] = []
    applied = getattr(t, "applied_tags", None) or []
    for tag in applied:
        if name := getattr(tag, "name", None):
            out.append(name)
    return out

def _check_thread_valid(parent: discord.ForumChannel) -> bool:
    """check if thread is valid."""
    return parent is not None and isinstance(parent, discord.ForumChannel)

def _check_target_channel(parent: discord.ForumChannel, config: Config) -> bool:
    """check if parent is in the list of forum channel ids."""
    return config.forum_channel_ids and str(parent.id) in config.forum_channel_ids


def _thread_url(thread: Thread) -> str:
    """Discord 스레드 링크 URL."""
    return f"https://discord.com/channels/{thread.guild.id}/{thread.id}"

# --- Slack transfer ---
async def _send_thread_to_slack_message_only(thread: Thread, config: Config) -> None:
    """해당 스레드를 Slack 메시지로만 전송 (트리거 웹후크 없음). 포럼 스레드에서만 가능."""
    parent = thread.parent
    if not _check_thread_valid(parent):
        raise ValueError("이 채널은 포럼 스레드가 아닙니다.")
    content = ""
    author = "unknown"
    attachment_urls: list[str] = []
    try:
        forum_post = None
        async for msg in thread.history(limit=1, oldest_first=True):
            forum_post = msg
            break
        if forum_post:
            content = (forum_post.content or "").strip()
            attachments = getattr(forum_post, "attachments", None) or []
            attachment_urls = [a.url for a in attachments if getattr(a, "url", None)]
            u = forum_post.author
            author = f"{getattr(u, 'display_name', None)} ({u})"
    except discord.DiscordException:
        if thread.owner_id:
            author = f"알 수 없음 ({thread.owner_id})"
    url = _thread_url(thread)
    tag_names = _tags_from_thread(thread)
    await asyncio.to_thread(
        send_to_slack_message,
        webhook_url=config.slack_webhook_url,
        title=thread.name,
        content=content,
        author=author,
        url=url,
        forum_name=parent.name,
        tags=tag_names,
        attachment_urls=attachment_urls,
    )


async def _get_all_threads(client: discord.Client, config: Config) -> list[Thread]:
    """Return all threads in the forum channels specified in the config."""
    threads: list[Thread] = []
    for cid in config.forum_channel_ids:
        try:
            channel = client.get_channel(int(cid)) or await client.fetch_channel(int(cid))
        except (ValueError, discord.DiscordException):
            continue
        if not isinstance(channel, discord.ForumChannel):
            continue
        threads.extend(channel.threads)
        try:
            async for thread in channel.archived_threads(limit=500):
                threads.append(thread)
        except discord.DiscordException:
            pass
    return threads


async def _sync_issue_table(client: discord.Client, config: Config) -> int:
    """synchronize issue table in slack."""
    if not config.trigger_webhook_url:
        return 0
    threads = await _get_all_threads(client, config)
    logger.info("sync-issue-table: found %s threads", len(threads))
    sent = 0
    for thread in threads:
        try:
            parent = thread.parent
            if not _check_thread_valid(parent):
                logger.info("skip thread %s: parent invalid or not forum channel", thread.id)
                continue
            if not _check_target_channel(parent, config):
                logger.info("skip thread %s: parent channel %s not in config", thread.id, parent.id if parent else None)
                continue
            url = _thread_url(thread)
            tag_names = _tags_from_thread(thread)
            field_tag = [tag for tag in tag_names if tag in FIELD_TAG]
            status_tag = [STATUS_TAG_LABEL[tag] for tag in tag_names if tag in STATUS_TAG_LABEL]
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    await asyncio.to_thread(
                        send_to_trigger_webhook,
                        webhook_url=config.trigger_webhook_url,
                        title=thread.name,
                        url=url,
                        field_tag=field_tag,
                        status_tag=status_tag,
                        created_at=thread.created_at,
                    )
                    sent += 1
                    if attempt > 0:
                        logger.info("thread %s succeeded on retry %s", thread.id, attempt + 1)
                    break
                except Exception as e:
                    last_error = e
                    logger.warning("sync attempt %s/3 failed for thread %s (%s): %s", attempt + 1, thread.id, thread.name, e)
                    if attempt < 2:
                        delay = 5.0 if "429" in str(e) else 2.0
                        await asyncio.sleep(delay)
            else:
                logger.warning("sync gave up for thread %s (%s): %s", thread.id, thread.name, last_error)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.warning("sync failed for thread %s (%s): %s", thread.id, thread.name, e)
            if "429" in str(e):
                await asyncio.sleep(2.0)
            await asyncio.sleep(1.5)
    logger.info("sync-issue-table done: sent=%s total_threads=%s", sent, len(threads))
    return sent


async def _transfer_issue_to_slack(
    thread: Thread,
    config: Config,
) -> None:
    """transfer issue to slack."""
    parent = thread.parent
    if not _check_thread_valid(parent) or not _check_target_channel(parent, config):
        return

    content = ""
    author = "unknown"
    attachment_urls: list[str] = []
    try:
        forum_post = None
        async for msg in thread.history(limit=1, oldest_first=True):
            forum_post = msg
            break
        if forum_post:
            content = (forum_post.content or "").strip()
            attachments = getattr(forum_post, "attachments", None) or []
            attachment_urls = [a.url for a in attachments if getattr(a, "url", None)]
            user_id = forum_post.author
            user_nickname = getattr(user_id, "display_name", None)
            author = f"{user_nickname} ({user_id})"
    except discord.DiscordException:
        if thread.owner_id:
            author = f"알 수 없음 ({thread.owner_id})"

    url = _thread_url(thread)

    tag_names = _tags_from_thread(thread)
    field_tag = [tag for tag in tag_names if tag in FIELD_TAG]
    status_tag = [STATUS_TAG_LABEL[tag] for tag in tag_names if tag in STATUS_TAG_LABEL]

    await asyncio.to_thread(
        send_to_slack_message,
        webhook_url=config.slack_webhook_url,
        title=thread.name,
        content=content,
        author=author,
        url=url,
        forum_name=parent.name,
        tags=tag_names,
        attachment_urls=attachment_urls,
    )
    await asyncio.to_thread(
        send_to_trigger_webhook,
        webhook_url=config.trigger_webhook_url,
        title=thread.name,
        url=url,
        field_tag=field_tag,
        status_tag=status_tag,
        created_at=thread.created_at,
    )


# --- Bot entry ---
def run_bot(config: Config | None = None) -> None:
    """run bot."""
    cfg = config or load_config()

    client = _create_client()
    tree = app_commands.CommandTree(client)

    @tree.command(name="send-this-thread-to-slack", description="이 스레드를 Slack 메시지로 전송합니다")
    async def send_this_thread_to_slack(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, Thread):
            await interaction.response.send_message(
                "이 명령은 포럼 스레드 안에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return
        if not cfg.slack_webhook_url:
            await interaction.response.send_message(
                "Slack 웹후크 URL이 설정되지 않았습니다. 관리자에게 문의하세요.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await _send_thread_to_slack_message_only(interaction.channel, cfg)
            await interaction.followup.send(
                "Slack 메시지로 전송했습니다.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"전송 중 오류: {e}",
                ephemeral=True,
            )

    @tree.command(name="sync-issue-table", description="포럼 채널 전체 글을 슬랙의 장표에 동기화합니다")
    async def sync_issue_table(interaction: discord.Interaction) -> None:
        if cfg.sync_command_user_ids and str(interaction.user.id) not in cfg.sync_command_user_ids:
            await interaction.response.send_message(
                "이 명령을 실행할 권한이 없습니다.",
                ephemeral=True,
            )
            return
        if not cfg.trigger_webhook_url:
            await interaction.response.send_message(
                "트리거 웹후크 URL이 설정되지 않았습니다. 관리자에게 문의하세요.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            count = await _sync_issue_table(interaction.client, cfg)
            await interaction.followup.send(
                f"동기화 완료: {count}개 스레드를 장표로 전송했습니다.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"동기화 중 오류: {e}",
                ephemeral=True,
            )

    @client.event
    async def on_ready():
        await tree.sync()
        print(f"Bot logged in: {client.user}")

    @client.event
    async def on_thread_create(thread: Thread):
        try:
            await _transfer_issue_to_slack(thread, cfg)
            logger.info("전송 완료: 스레드 %s, 제목: %s → Slack", thread.id, thread.name)
        except Exception:
            logger.exception(
                "전송 실패: 스레드 %s, 제목: %s → Slack",
                thread.id,
                thread.name,
            )

    client.run(cfg.discord_token)
