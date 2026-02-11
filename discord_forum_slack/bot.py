"""Discord Bot Client."""

import discord
from discord import Thread

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

async def _handle_thread_create(
    thread: Thread,
    config: Config,
) -> None:
    """send forum post to slack."""
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

    @client.event
    async def on_ready():
        print(f"Bot logged in: {client.user}")

    @client.event
    async def on_thread_create(thread: Thread):
        try:
            await _handle_thread_create(thread, cfg)
        except Exception as e:
            print(f"Error sending forum to Slack: {e}")

    client.run(cfg.discord_token)
