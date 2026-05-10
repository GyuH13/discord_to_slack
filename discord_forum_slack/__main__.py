import asyncio
import logging
import sys

from .config import Config, load_config
from .discord_bot import create_discord_bot
from .slack_bot import create_slack_bot

logger = logging.getLogger(__name__)


async def _run(cfg: Config) -> None:
    client = create_discord_bot(cfg)
    tasks = [client.start(cfg.discord_token)]

    handler = create_slack_bot(cfg, client)
    tasks.append(handler.start_async())
    logger.info("Slack Socket Mode 활성화")

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
