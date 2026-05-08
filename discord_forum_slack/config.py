import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 프로젝트 루트(config.yaml 위치) 기준 경로
_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


@dataclass
class Config:
    """config class."""

    discord_token: str
    slack_channel_id: str
    forum_channel_ids: list[str]
    trigger_webhook_url: str = ""
    sync_command_user_ids: list[str] = field(default_factory=list)
    # Slack API (Socket Mode + List)
    slack_bot_token: str = ""   # xoxb-...
    slack_app_token: str = ""   # xapp-...
    list_id: str = ""

    def validate(self) -> None:
        """validate required settings."""
        if not self.discord_token:
            raise ValueError("discord_token is not set.")
        if self.slack_channel_id and not self.slack_bot_token:
            raise ValueError("slack_bot_token is required when slack_channel_id is set.")


def load_config(path: str | Path | None = None) -> Config:
    """load config from YAML file."""
    if path is not None:
        config_path = Path(path)
    elif env_path := os.environ.get("DISCORD_BOT_CONFIG_PATH"):
        config_path = Path(env_path)
    else:
        config_path = _DEFAULT_CONFIG_PATH

    if not config_path.is_file():
        raise FileNotFoundError(
            f"config file not found: {config_path}"
        )

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("config.yaml is empty.")

    discord_token = (data.get("discord_token") or "").strip()
    slack_channel_id = (data.get("slack_channel_id") or "").strip()
    forum_channel_ids = [
        str(s).strip()
        for s in (data.get("forum_channel_ids") or [])
        if s
    ]
    trigger_webhook_url = (data.get("trigger_webhook_url") or "").strip()
    sync_command_user_ids = [
        str(s).strip()
        for s in (data.get("sync_command_user_ids") or [])
        if s
    ]
    slack_bot_token = (data.get("slack_bot_token") or "").strip()
    slack_app_token = (data.get("slack_app_token") or "").strip()
    list_id = (data.get("list_id") or "").strip()
    config = Config(
        discord_token=discord_token,
        slack_channel_id=slack_channel_id,
        forum_channel_ids=forum_channel_ids,
        trigger_webhook_url=trigger_webhook_url,
        sync_command_user_ids=sync_command_user_ids,
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        list_id=list_id,
        list_column_title=list_column_title,
        list_column_status=list_column_status,
        list_column_msg=list_column_msg,
        list_column_thread_link=list_column_thread_link,
        list_column_date=list_column_date,
        list_select_new_issue=list_select_new_issue,
        list_select_handling=list_select_handling,
        list_select_solved=list_select_solved,
    )
    config.validate()
    return config
