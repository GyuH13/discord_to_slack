"""python -m discord_forum_slack 로 실행 시 진입점."""

import logging
import sys

from .bot import run_bot
from .config import load_config


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

    run_bot(config)


if __name__ == "__main__":
    main()
