# Discord 포럼 → Slack 자동 전송

Discord 포럼 채널에 새 글이 올라오면 해당 내용을 Slack으로 자동 전송하는 봇입니다.

## 동작 방식

- Discord 봇이 **포럼 채널에서 새 스레드(글) 생성** 시점을 감지합니다.
- 해당 글의 **제목(스레드 이름)** 과 **첫 메시지 내용**, **작성자**, **포럼 이름**을 Slack으로 보냅니다.
- 특정 포럼만 보내고 싶다면 `config.yaml`의 `forum_channel_ids`로 채널 ID를 지정할 수 있습니다.

## 준비물

> 아래 웹후크 설치 안내는 이전 버전 기준입니다. 현재 실행 설정은
> `config.example.yaml`의 Slack Bot/App/User Token과 `list_id`를 사용합니다.

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

## Slack List 동기화와 assign

대상 포럼에 새 글이 생성되거나 Slack에서 `/table_update`를 실행하면 동기화합니다.
기존 글의 상태 태그 변경은 `/table_update`로 반영할 수 있습니다.

- `Link` 열의 Discord 서버 ID와 글 ID로 기존 행을 찾습니다. 이 열은 식별용으로 유지하세요.
- 새 글은 행을 추가하고, 기존 글은 봇이 관리하는 제목·상태·메시지 링크·글 링크·작성일 중
  변경된 셀만 수정합니다. 기존 행 ID는 유지됩니다.
- `assign` 등 수동으로 추가한 열은 수정하지 않습니다. Slack List 맨 오른쪽에
  `assign` 열을 추가하고 기본값을 비워두면 새 행의 담당자도 빈칸으로 시작합니다.
  사람 선택형과 일반 선택형 모두 기존 값을 보존합니다.
- Discord에서 `Solved` / `✅Solved`로 확인된 글만 행을 삭제합니다. 조회에서 빠진 글이나
  Discord 글 링크가 없는 수동 행은 삭제하지 않습니다.
- 해결된 글의 행을 삭제하면 그 행의 담당자도 사라집니다. 다시 미해결 상태가 되면 새 행으로
  추가되므로 이전 담당자는 복원되지 않습니다.
- Discord 포럼 또는 보관된 글 조회가 실패하면 Slack 변경 없이 동기화를 중단합니다.
  Slack API 변경 도중 실패하면 이미 성공한 변경은 남으며, 오류를 알리고 다음 동기화에서
  현재 행을 다시 비교합니다.

셀 수정은 [Slack Lists API](https://docs.slack.dev/reference/methods/slackLists.items.update/)를
사용합니다. 빈 테이블의 스키마도 `slackLists.items.list`의 `include_list`로 조회합니다.

회귀 테스트 실행(프로젝트 의존성 설치 후):

```bash
python -m unittest discover -s tests -v
```
