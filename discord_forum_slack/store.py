"""Persistent storage for Discord thread ID → Slack message permalink."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_STORE_PATH = Path(__file__).resolve().parent.parent / "thread_links.json"


def _load() -> dict[str, str]:
    if not _STORE_PATH.exists():
        return {}
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("thread_links.json load failed: %s", e)
        return {}


def _save(data: dict[str, str]) -> None:
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_link(thread_id: int | str, permalink: str) -> None:
    data = _load()
    data[str(thread_id)] = permalink
    _save(data)


def get_link(thread_id: int | str) -> str | None:
    return _load().get(str(thread_id))
