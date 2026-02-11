"""Discord 봇 클라이언트."""

import discord
from discord import Thread

from .config import Config, load_config
from .slack import send_to_slack


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


async def _handle_thread_create(
    thread: Thread,
    config: Config,
) -> None:
    """send forum post to slack."""
    parent = thread.parent
    # sometimes thread is not created in a forum channel
    if parent is None:
        return

    # check if parent is a forum channel
    if not isinstance(parent, discord.ForumChannel):
        return
    
    # check if parent is in the list of forum channel ids
    if config.forum_channel_ids and str(parent.id) not in config.forum_channel_ids:
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
            u = forum_post.author
            display = getattr(u, "display_name", None)
            author = f"{display} ({u})"
    except discord.DiscordException:
        if thread.owner_id:
            author = f"알 수 없음 ({thread.owner_id})"

    url = f"https://discord.com/channels/{thread.guild.id}/{thread.parent_id}/{thread.id}"

    tag_names: list[str] = []
    tag_names = _tags_from_thread(thread)

    send_to_slack(
        webhook_url=config.slack_webhook_url,
        title=thread.name,
        content=content,
        author=author,
        url=url,
        forum_name=parent.name,
        tags=tag_names,
    )
    print(f"completed sending to slack: {thread.name} ({thread.id})")


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
