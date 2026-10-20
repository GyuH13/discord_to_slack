import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


@dataclass
class AutoSolveConfig:
    enabled: bool = False
    forum_channel_id: str = ""
    forum_channel_name: str = "support"
    robotis_role_id: str = ""
    robotis_role_name: str = "ROBOTIS"
    solved_tag_id: str = ""
    solved_tag_name: str = "Solved"
    inactive_days: int = 14

    def validate(self, forum_channel_ids: list[str]) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("auto_solve.enabled는 true 또는 false여야 합니다.")
        if type(self.inactive_days) is not int or self.inactive_days <= 0:
            raise ValueError("auto_solve.inactive_days는 양의 정수여야 합니다.")
        if self.enabled:
            for name in ("forum_channel_id", "robotis_role_id", "solved_tag_id"):
                value = getattr(self, name)
                if value and (not value.isdecimal() or int(value) <= 0):
                    raise ValueError(f"auto_solve.{name}에 올바른 ID를 설정해주세요.")
            if self.forum_channel_id and self.forum_channel_id not in forum_channel_ids:
                raise ValueError(
                    "auto_solve.forum_channel_id는 forum_channel_ids에 포함되어야 합니다."
                )
            for name in ("forum_channel_name", "robotis_role_name", "solved_tag_name"):
                if not getattr(self, name):
                    raise ValueError(f"auto_solve.{name}은 비워둘 수 없습니다.")


@dataclass
class Config:
    """config.yaml에서 읽은 봇의 설정 파일"""

    discord_token: str
    slack_channel_id: str
    forum_channel_ids: list[str]
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_user_token: str = ""
    list_id: str = ""
    auto_solve: AutoSolveConfig = field(default_factory=AutoSolveConfig)

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
        self.auto_solve.validate(self.forum_channel_ids)


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
    forum_channel_ids = [str(s).strip() for s in (data.get("forum_channel_ids") or []) if s]
    slack_bot_token = (data.get("slack_bot_token") or "").strip()
    slack_app_token = (data.get("slack_app_token") or "").strip()
    slack_user_token = (data.get("slack_user_token") or "").strip()
    list_id = (data.get("list_id") or "").strip()
    auto_data = data.get("auto_solve", {})
    if not isinstance(auto_data, dict):
        raise ValueError("auto_solve는 설정 항목을 가진 YAML 객체여야 합니다.")
    auto_solve = AutoSolveConfig(
        enabled=auto_data.get("enabled", False),
        forum_channel_id=str(auto_data.get("forum_channel_id") or "").strip(),
        forum_channel_name=str(auto_data.get("forum_channel_name", "support")).strip(),
        robotis_role_id=str(auto_data.get("robotis_role_id") or "").strip(),
        robotis_role_name=str(auto_data.get("robotis_role_name", "ROBOTIS")).strip(),
        solved_tag_id=str(auto_data.get("solved_tag_id") or "").strip(),
        solved_tag_name=str(auto_data.get("solved_tag_name", "Solved")).strip(),
        inactive_days=auto_data.get("inactive_days", 14),
    )
    config = Config(
        discord_token=discord_token,
        slack_channel_id=slack_channel_id,
        forum_channel_ids=forum_channel_ids,
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        slack_user_token=slack_user_token,
        list_id=list_id,
        auto_solve=auto_solve,
    )
    config.validate()
    return config
