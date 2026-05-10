import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


@dataclass
class Config:
    """config.yaml에서 읽은 봇의 설정 파일"""

    discord_token: str
    slack_channel_id: str
    forum_channel_ids: list[str]
    trigger_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_user_token: str = ""
    list_id: str = ""

    def validate(self) -> None:
        if not self.discord_token:
            raise ValueError("discord_token이 설정되지 않았습니다.")
        if not self.slack_bot_token:
            raise ValueError("slack_bot_token이 설정되지 않았습니다.")
        if not self.slack_app_token:
            raise ValueError("slack_app_token이 설정되지 않았습니다.")
        if not self.slack_channel_id:
            raise ValueError("slack_channel_id가 설정되지 않았습니다.")
        if not self.list_id:
            raise ValueError("list_id가 설정되지 않았습니다.")


def load_config(path: str | Path | None = None) -> Config:
    """YAML 파일에서 설정을 읽고 검증합니다."""
    if path is not None:
        config_path = Path(path)
    elif env_path := os.environ.get("DISCORD_BOT_CONFIG_PATH"):
        config_path = Path(env_path)
    else:
        config_path = _DEFAULT_CONFIG_PATH

    if not config_path.is_file():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("config.yaml이 비어 있습니다.")

    discord_token = (data.get("discord_token") or "").strip()
    slack_channel_id = (data.get("slack_channel_id") or "").strip()
    forum_channel_ids = [
        str(s).strip()
        for s in (data.get("forum_channel_ids") or [])
        if s
    ]
    trigger_webhook_url = (data.get("trigger_webhook_url") or "").strip()
    slack_bot_token = (data.get("slack_bot_token") or "").strip()
    slack_app_token = (data.get("slack_app_token") or "").strip()
    slack_user_token = (data.get("slack_user_token") or "").strip()
    list_id = (data.get("list_id") or "").strip()
    config = Config(
        discord_token=discord_token,
        slack_channel_id=slack_channel_id,
        forum_channel_ids=forum_channel_ids,
        trigger_webhook_url=trigger_webhook_url,
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        slack_user_token=slack_user_token,
        list_id=list_id,
    )
    config.validate()
    return config
