FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY discord_forum_slack/ ./discord_forum_slack/

RUN pip install --no-cache-dir -e .

# config.yaml는 볼륨으로 마운트 (토큰 등 비밀 포함)
ENTRYPOINT ["python", "-m", "discord_forum_slack"]
