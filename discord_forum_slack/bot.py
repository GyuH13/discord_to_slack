"""Discord Bot Client."""

import discord
from discord import app_commands, Thread

from .config import Config, load_config
from .slack import send_to_slack_message, send_to_trigger_webhook


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


async def _collect_all_forum_threads(client: discord.Client, config: Config) -> list[Thread]:
    """config에 기술된 forum_channel_ids 4개 채널에서만 활성+아카이브 스레드를 수집한다."""
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


async def _sync_forum_to_trigger(client: discord.Client, config: Config) -> int:
    """포럼 채널 전체 글을 트리거 웹후크로 전송하고 전송한 스레드 개수를 반환한다."""
    if not config.trigger_webhook_url:
        return 0
    threads = await _collect_all_forum_threads(client, config)
    sent = 0
    for thread in threads:
        try:
            parent = thread.parent
            if parent is None:
                continue
            url = f"https://discord.com/channels/{thread.guild.id}/{thread.parent_id}/{thread.id}"
            tag_names = _tags_from_thread(thread)
            send_to_trigger_webhook(
                webhook_url=config.trigger_webhook_url,
                title=thread.name,
                url=url,
                tags=tag_names,
                created_at=thread.created_at,
            )
            sent += 1
        except Exception:
            continue
    return sent


async def _handle_thread_create(
    thread: Thread,
    config: Config,
) -> None:
    """config에 기술된 4개 포럼 채널에서만 새 글을 Slack/트리거 웹후크로 전송한다."""
    parent = thread.parent
    if not _check_thread_valid(parent):
        return
    if not _check_target_channel(parent, config):
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

    url = f"https://discord.com/channels/{thread.guild.id}/{thread.parent_id}/{thread.id}"

    tag_names: list[str] = []
    tag_names = _tags_from_thread(thread)

    send_to_slack_message(
        webhook_url=config.slack_webhook_url,
        title=thread.name,
        content=content,
        author=author,
        url=url,
        forum_name=parent.name,
        tags=tag_names,
    )
    send_to_trigger_webhook(
        webhook_url=config.trigger_webhook_url,
        title=thread.name,
        url=url,
        tags=tag_names,
        created_at=thread.created_at,
    )



def run_bot(config: Config | None = None) -> None:
    """run bot."""
    cfg = config or load_config()

    client = _create_client()
    tree = app_commands.CommandTree(client)

    @tree.command(name="sync-issue-table", description="포럼 채널 전체 글을 장표(트리거 웹후크)에 동기화합니다")
    async def sync_issue_table(interaction: discord.Interaction) -> None:
        if cfg.sync_command_user_ids and str(interaction.user.id) not in cfg.sync_command_user_ids:
            await interaction.response.send_message(
                "이 명령을 실행할 권한이 없습니다.",
                ephemeral=True,
            )
            return
        if not cfg.trigger_webhook_url:
            await interaction.response.send_message(
                "트리거 웹후크 URL이 설정되지 않았습니다. config.yaml의 trigger_webhook_url을 설정하세요.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            count = await _sync_forum_to_trigger(interaction.client, cfg)
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
            await _handle_thread_create(thread, cfg)
        except Exception as e:
            print(f"Error sending forum to Slack: {e}")

    client.run(cfg.discord_token)
