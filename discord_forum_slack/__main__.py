"""python -m discord_forum_slack 로 실행 시 진입점."""

import asyncio
import logging
import sys

from .bot import create_bot
from .config import Config, load_config


async def _run(cfg: Config) -> None:
    client = create_bot(cfg)
    tasks = [client.start(cfg.discord_token)]

    if cfg.slack_app_token and cfg.slack_bot_token and cfg.list_id:
        from .slack_app import create_slack_bot
        handler = create_slack_bot(cfg, client)
        tasks.append(handler.start_async())
        logging.getLogger(__name__).info("Slack Socket Mode 활성화")

    await asyncio.gather(*tasks)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        print(e)
        sys.exit(1)

    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
