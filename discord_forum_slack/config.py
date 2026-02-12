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
    slack_webhook_url: str
    forum_channel_ids: list[str]
    trigger_webhook_url: str = ""
    # 동기화 슬래시 명령 허용 Discord 유저 ID 목록. 비우면 서버 내 누구나 실행 가능
    sync_command_user_ids: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """validate required settings."""
        if not self.discord_token or not self.slack_webhook_url:
            raise ValueError("discord_token or slack_webhook_url is not set.")


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
    slack_webhook_url = (data.get("slack_webhook_url") or "").strip()
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

    config = Config(
        discord_token=discord_token,
        slack_webhook_url=slack_webhook_url,
        forum_channel_ids=forum_channel_ids,
        trigger_webhook_url=trigger_webhook_url,
        sync_command_user_ids=sync_command_user_ids,
    )
    config.validate()
    return config
