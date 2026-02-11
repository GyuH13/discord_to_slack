# Discord 포럼 → Slack 자동 전송

Discord 포럼 채널에 새 글이 올라오면 해당 내용을 Slack으로 자동 전송하는 봇입니다.

## 동작 방식

- Discord 봇이 **포럼 채널에서 새 스레드(글) 생성** 시점을 감지합니다.
- 해당 글의 **제목(스레드 이름)** 과 **첫 메시지 내용**, **작성자**, **포럼 이름**을 Slack으로 보냅니다.
- 특정 포럼만 보내고 싶다면 `config.yaml`의 `forum_channel_ids`로 채널 ID를 지정할 수 있습니다.

## 준비물

1. **Discord 봇**
2. **Slack Incoming Webhook URL**

---

## 1. Discord 봇 만들기

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → 이름 입력 후 생성.
2. 왼쪽 **Bot** → **Add Bot** → 봇 생성.
3. **Reset Token**으로 토큰 복사 후 `config.yaml`의 `discord_token`에 넣기 (절대 공개하지 마세요).
4. **Privileged Gateway Intents**에서 아래를 켭니다.
   - **MESSAGE CONTENT INTENT** (포럼 글 내용 읽기용)
   - **SERVER MEMBERS INTENT**는 이 기능에는 필수 아님.
5. **OAuth2 → URL Generator**에서:
   - Scopes: `bot`
   - Bot Permissions: `Read Messages/View Channels`, `Read Message History`
   → 생성된 URL로 서버에 봇 초대.
6. 포럼 채널이 있는 서버에 봇이 들어와 있고, 해당 채널을 봇이 **볼 수 있는 권한**이 있어야 합니다.

---

## 2. Slack Incoming Webhook

1. [Slack API](https://api.slack.com/apps) → **Create New App** → **From scratch** → 앱 이름과 워크스페이스 선택.
2. **Incoming Webhooks** → **Activate Incoming Webhooks** 켜기.
3. **Add New Webhook to Workspace** → 메시지를 받을 채널 선택 → **Webhook URL** 복사.
4. `config.yaml`의 `slack_webhook_url`에 그 URL을 넣기.

---

## 3. 포럼 채널 ID (선택)

특정 포럼만 Slack으로 보내려면:

1. Discord 설정에서 **개발자 모드** 켜기 (설정 → 앱 설정 → 고급).
2. 해당 포럼 채널 우클릭 → **채널 ID 복사**.
3. `config.yaml`의 `forum_channel_ids`에 채널 ID 목록으로 설정. 비우면 **모든 포럼 채널**이 대상입니다.

---

## 4. 설치 및 실행

```bash
cp config.example.yaml config.yaml
# config.yaml 에서 discord_token, slack_webhook_url (및 필요 시 forum_channel_ids) 수정

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 방법 1: 패키지로 설치 후 실행 (권장)
pip install -e .
python -m discord_forum_slack
# 또는: discord-forum-slack

# 방법 2: 의존성만 설치 후 실행
pip install -r requirements.txt
python -m discord_forum_slack   # 프로젝트 루트에서 실행
```

설정 파일 경로: `CONFIG_PATH=/path/to/config.yaml python -m discord_forum_slack`

---

## 설정 파일 (config.yaml) 요약

| 키 | 필수 | 설명 |
|------|------|------|
| `discord_token` | ✅ | Discord 봇 토큰 |
| `slack_webhook_url` | ✅ | Slack Incoming Webhook URL |
| `forum_channel_ids` | ❌ | 전송할 포럼 채널 ID 목록. 비우면 전체 포럼 |

---

## 트러블슈팅

- **메시지 내용이 비어 옴**  
  Discord Developer Portal에서 봇의 **MESSAGE CONTENT INTENT**가 켜져 있는지 확인하세요.
- **Slack에 안 옴**  
  `config.yaml`의 `slack_webhook_url`이 맞는지, 해당 Slack 앱/채널에 웹후크가 연결돼 있는지 확인하세요.
- **특정 포럼만 안 옴**  
  봇이 그 포럼 채널을 볼 수 있는지, 채널 ID가 `forum_channel_ids`에 정확히 들어갔는지 확인하세요.
