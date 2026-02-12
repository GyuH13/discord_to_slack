"""Discord Bot Client."""

import asyncio
import logging

import discord
from discord import app_commands, Thread

from .config import Config, load_config
from .slack import send_to_slack_message, send_to_trigger_webhook

logger = logging.getLogger(__name__)

FIELD_TAG = ["dynamixel", "ai-worker", "omy", "omx", "hand","turtlebot","others"]
STATUS_TAG_LABEL: dict[str, str] = {
    "🟢New": "New Issue",
    "🟡Handling": "Handling",
    "✅Solved": "Complete",
    "New": "New Issue",
    "Handling": "Handling",
    "Solved": "Complete",
}


def _create_client() -> discord.Client:
    """create discord client with configured intents."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    return discord.Client(intents=intents)

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
    print(f"Found {len(threads)} threads to sync.")
    sent = 0
    for thread in threads:
        try:
            parent = thread.parent
            if not _check_thread_valid(parent):
                logger.debug("skip thread %s: parent invalid or not forum channel", thread.id)
                continue
            if not _check_target_channel(parent, config):
                logger.debug("skip thread %s: parent channel %s not in config", thread.id, parent.id if parent else None)
                continue
            url = f"https://discord.com/channels/{thread.guild.id}/{thread.id}"
            tag_names = _tags_from_thread(thread)
            field_tag = [tag for tag in tag_names if tag in FIELD_TAG]
            status_tag = [STATUS_TAG_LABEL[tag] for tag in tag_names if tag in STATUS_TAG_LABEL]
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
        except Exception:
            continue
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
    try:
        forum_post = None
        async for msg in thread.history(limit=1, oldest_first=True):
            forum_post = msg
            break
        if forum_post:
            content = (forum_post.content or "").strip()
            user_id = forum_post.author
            user_nickname = getattr(user_id, "display_name", None)
            author = f"{user_nickname} ({user_id})"
    except discord.DiscordException:
        if thread.owner_id:
            author = f"알 수 없음 ({thread.owner_id})"

    url = f"https://discord.com/channels/{thread.guild.id}/{thread.id}"

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



def run_bot(config: Config | None = None) -> None:
    """run bot."""
    cfg = config or load_config()

    client = _create_client()
    tree = app_commands.CommandTree(client)

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
        except Exception as e:
            print(f"Error sending forum to Slack: {e}")

    client.run(cfg.discord_token)
