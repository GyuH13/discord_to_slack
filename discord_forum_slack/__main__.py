"""python -m discord_forum_slack 로 실행 시 진입점."""

import sys

import logging
from .bot import run_bot
from .config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        print(e)
        sys.exit(1)

    run_bot(config)


if __name__ == "__main__":
    main()
