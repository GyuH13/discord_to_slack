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

- Slack의 기존 행을 읽고 `Link`에 있는 **Discord 서버 ID + 글 ID**별로 `assign`을
  백업한 뒤, 모든 행을 삭제하고 현재 미해결 글을 **작성일 최신순으로 다시 생성**합니다.
  삭제되거나 해결된 글은 재생성하지 않습니다. Slack의 별도 뷰 정렬이 있으면 그 설정이
  표시 순서에 우선할 수 있습니다.
- `assign` 열은 Slack에서 한 번 추가해야 합니다. 이름은 대소문자를 구분하지 않으며,
  사람·선택·텍스트 유형을 지원합니다. 사용자 ID와 선택 옵션 ID를 그대로 복원합니다.
  새 글은 빈칸으로 생성하고, 사용자가 기존 담당자를 지운 빈칸도 그대로 반영합니다.
- 백업 파일은 컨테이너의 `/app/data/assign_backups/`에 List별로 저장합니다.
  기본 Compose 설정에서는 호스트의 `./data/assign_backups/`에 남으므로 재빌드나
  컨테이너 재생성 후에도 유지됩니다. **복구하려면 이 data 디렉터리를 보존하세요.**
- 백업을 임시 파일에 쓰고 디스크에 저장한 후 교체합니다. 백업 저장 실패 또는 손상,
  조회 실패, 식별 불가능한 담당자 값, 상충하는 중복 행은 삭제 전에 오류로 중단합니다.
- 재생성 도중 실패하면 테이블이 비어 있거나 일부만 남을 수 있습니다. 문제 해결 후
  `/table_update`를 다시 실행하면, 남은 행의 최신 담당자와 파일 백업을 합쳐 복원합니다.
  현재 테이블에 없는 글의 백업도 보관하므로 같은 글이 다시 열리면 이전 담당자를 복원합니다.
- 전체 재작성으로 **행 ID·댓글·변경 이력 및 assign 이외의 수동 필드는 보존되지 않습니다.**
  Discord 글 링크가 없는 빈 수동 행도 제거됩니다. 봇 전용 테이블로 사용하세요.
- Discord 포럼 및 보관된 글 조회부터 Slack 재작성 완료까지 순차 실행합니다.
  Slack 호출 제한(429)은 서버가 지정한 시간만큼 기다린 뒤 요청당 최대 3회 재시도합니다.

수정한 코드를 배포한 디렉터리에서 다음 명령으로 이미지를 빌드하고 컨테이너를 교체하세요.
`docker compose build`만 실행하면 실행 중인 봇 프로세스는 바뀌지 않습니다.

```bash
docker compose up -d --build --force-recreate discord-to-slack-bot
```

로그에 `전체 재작성 시작` / `전체 재작성 완료`가 표시되는지 확인할 수 있습니다.

## support 무응답 글 자동 Solved

`config.yaml`에 다음 설정을 추가하고 봇을 재시작하면 활성화됩니다.
설정을 생략하면 기존 동작과 동일하게 자동 완료 기능은 꺼져 있습니다.

```yaml
auto_solve:
  enabled: true
  inactive_days: 14
```

기존 `forum_channel_ids` 안에서 이름이 `support`인 포럼을 찾고, 그 서버의 `ROBOTIS`
역할과 포럼의 `Solved` 또는 `✅Solved` 태그를 찾습니다. 영문 대소문자는 구분하지 않습니다.
이름이 다르면 `config.example.yaml`의 이름 설정을 변경하세요. 같은 이름이 여러 개이면
태그를 변경하지 않고 로그를 남깁니다. 이때만 해당 ID 설정을 사용하면 됩니다.

- 새 대상 포럼 글 생성 또는 `/table_update` 실행 시 검사합니다. 주기 작업은 없으며,
  **14일이 지난 뒤 다음 트리거 때** 처리합니다. 일반 댓글 작성 자체는 동기화 트리거가 아닙니다.
- 보관된 글을 포함하여 support의 미해결 글을 검사합니다. 마지막 사람 메시지가
  최초 게시글이 아닌 답변이고, 작성자가 **검사 시점에** ROBOTIS 역할을 가지고 있으며,
  해당 메시지 작성 시각에서 14×24시간 이상 지났을 때만 완료 처리합니다.
- 봇·웹후크·시스템 메시지는 대화에서 제외합니다. 마지막 사람이 고객이면 오래된 글도
  자동 완료하지 않습니다. 직원이 추가 답변하면 그 답변 시각부터 다시 계산합니다.
- 변경 직전 메시지와 역할을 재확인합니다. `New`/`Handling` 상태 태그를 제거하고
  `Solved`로 교체하며, 제품 및 기타 태그는 유지합니다. 이미 완료된 글은 건너뜁니다.
- 보관된 글은 잠시 보관 해제 후 태그를 바꾸고 보관 상태를 복원합니다. 해제 도중
  새 대화가 확인되면 완료 처리하지 않고 열어둡니다. 봇에는 채널 보기, 메시지 기록 보기,
  **스레드 관리(Manage Threads)** 권한이 필요합니다.
- 글별 메시지/역할 조회나 편집이 실패하면 해당 글은 건너뛰고 로그를 남깁니다.
  전체 포럼 목록 조회 실패 시에는 기존과 같이 동기화 자체를 중단합니다.
- 판정과 태그 변경을 먼저 마친 뒤, 변경된 상태로 Slack 테이블을 한 번 재작성합니다.
  로그의 `자동 Solved 처리`로 적용된 글 ID를 확인할 수 있습니다.

회귀 테스트 실행(프로젝트 의존성 설치 후):

```bash
python -m unittest discover -s tests -v
```
