"""기능별 YAML 설정. 기존 평면 설정도 읽어 동일한 구조로 변환한다."""

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class DiscordConfig:
    token: str = ""
    forum_channel_ids: list[str] = field(default_factory=list)


@dataclass
class SlackConfig:
    bot_token: str = ""
    app_token: str = ""
    user_token: str = ""


@dataclass
class NotificationsConfig:
    enabled: bool = True
    channel_id: str = ""


@dataclass
class TableSyncConfig:
    enabled: bool = True
    list_id: str = ""


@dataclass
class AutoSolveConfig:
    enabled: bool = False
    # 기존 설정 호환 및 포럼별로 조회한 ID 보관용. 새 설정에서는 이름을 사용한다.
    forum_channel_id: str = ""
    forum_channel_name: str = ""
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
                    "auto_solve.forum_channel_id는 discord.forum_channel_ids에 포함되어야 합니다."
                )
            for name in ("robotis_role_name", "solved_tag_name"):
                if not getattr(self, name):
                    raise ValueError(f"auto_solve.{name}은 비워둘 수 없습니다.")


@dataclass
class Config:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    table_sync: TableSyncConfig = field(default_factory=TableSyncConfig)
    auto_solve: AutoSolveConfig = field(default_factory=AutoSolveConfig)

    def validate(self) -> None:
        for name in ("notifications", "table_sync"):
            if not isinstance(getattr(self, name).enabled, bool):
                raise ValueError(f"{name}.enabled는 true 또는 false여야 합니다.")
        if not self.discord.token:
            raise ValueError("discord.token이 설정되지 않았습니다.")
        ids = self.discord.forum_channel_ids
        if not ids or any(not value.isdecimal() or int(value) <= 0 for value in ids):
            raise ValueError("discord.forum_channel_ids에 대상 포럼 ID 목록을 설정해주세요.")
        if self.notifications.enabled or self.table_sync.enabled:
            if not self.slack.bot_token:
                raise ValueError("slack.bot_token이 설정되지 않았습니다.")
        if self.notifications.enabled and not self.notifications.channel_id:
            raise ValueError("notifications.channel_id가 설정되지 않았습니다.")
        if self.table_sync.enabled:
            if not self.slack.app_token:
                raise ValueError("slack.app_token이 설정되지 않았습니다.")
            if not self.table_sync.list_id:
                raise ValueError("table_sync.list_id가 설정되지 않았습니다.")
        self.auto_solve.validate(ids)


_LEGACY_KEYS = {
    "discord_token": ("discord", "token"),
    "forum_channel_ids": ("discord", "forum_channel_ids"),
    "slack_bot_token": ("slack", "bot_token"),
    "slack_app_token": ("slack", "app_token"),
    "slack_user_token": ("slack", "user_token"),
    "slack_channel_id": ("notifications", "channel_id"),
    "list_id": ("table_sync", "list_id"),
}
_SECTIONS = {
    "discord": DiscordConfig,
    "slack": SlackConfig,
    "notifications": NotificationsConfig,
    "table_sync": TableSyncConfig,
    "auto_solve": AutoSolveConfig,
}


def parse_config(data: object) -> Config:
    if not isinstance(data, dict) or not data:
        raise ValueError("config.yaml은 비어 있지 않은 YAML 객체여야 합니다.")
    unknown = set(data) - set(_SECTIONS) - set(_LEGACY_KEYS) - {"trigger_webhook_url"}
    if unknown:
        raise ValueError("config.yaml에 지원하지 않는 설정 키가 있습니다.")
    sections = {}
    for name, cls in _SECTIONS.items():
        value = data.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"{name}는 설정 항목을 가진 YAML 객체여야 합니다.")
        sections[name] = dict(value)
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError(f"{name}에 지원하지 않는 설정 키가 있습니다.")
    for old_key, (section, key) in _LEGACY_KEYS.items():
        if old_key in data:
            if key in sections[section]:
                raise ValueError(f"{old_key}와 {section}.{key}를 함께 설정할 수 없습니다.")
            sections[section][key] = data[old_key]
    if set(data) & set(_LEGACY_KEYS):
        logger.warning(
            "기존 평면 config를 읽었습니다. config.example.yaml의 기능별 구조로 이전하세요."
        )
    if "trigger_webhook_url" in data:
        logger.warning("trigger_webhook_url은 현재 사용하지 않는 설정입니다. 삭제해도 됩니다.")

    for section, values in sections.items():
        for key, value in values.items():
            if key == "forum_channel_ids":
                if not isinstance(value, list) or any(
                    isinstance(item, bool) or not isinstance(item, (str, int)) for item in value
                ):
                    raise ValueError("discord.forum_channel_ids는 ID 목록이어야 합니다.")
                values[key] = list(dict.fromkeys(str(item).strip() for item in value))
            elif key not in {"enabled", "inactive_days"}:
                if value is None:
                    values[key] = ""
                elif isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError(f"{section}.{key}는 문자열이어야 합니다.")
                else:
                    values[key] = str(value).strip()
    config = Config(**{name: cls(**sections[name]) for name, cls in _SECTIONS.items()})
    config.validate()
    return config


def config_to_dict(config: Config) -> dict:
    """현재 값을 새 YAML 구조로 내보낸다. 비어 있는 이전 버전 ID 필터는 생략한다."""
    data = asdict(config)
    for key in ("forum_channel_id", "forum_channel_name", "robotis_role_id", "solved_tag_id"):
        if not data["auto_solve"][key]:
            del data["auto_solve"][key]
    return data


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path or os.environ.get("DISCORD_BOT_CONFIG_PATH") or _DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        return parse_config(yaml.safe_load(stream))
