# Discord 포럼 → Slack

Discord 포럼의 새 글을 Slack 채널에 알리고, 미해결 글을 Slack List에 동기화합니다.
같은 트리거에서 직원 답변 후 오래 대화가 없는 글을 자동으로 Solved 처리할 수 있습니다.

## 설정

`config.example.yaml`을 `config.yaml`로 복사한 뒤 실제 값을 입력하세요.

| 설정 | 용도 |
|------|------|
| `discord.token` | Discord 봇 토큰 |
| `discord.forum_channel_ids` | 세 기능이 함께 사용하는 포럼 ID 목록. 최소 1개 필요 |
| `slack.bot_token` | Slack 메시지 전송 및 앱 인증 |
| `slack.app_token` | `/table_update`를 수신하는 Socket Mode 연결 |
| `slack.user_token` | Slack List API 토큰. 비우면 Bot Token 사용 |
| `notifications.enabled` / `channel_id` | 새 글 알림 사용 여부와 수신 Slack 채널 |
| `table_sync.enabled` / `list_id` | 테이블 동기화 사용 여부와 대상 Slack List |
| `auto_solve.enabled` | 무응답 글 자동 완료 사용 여부. 기본 false |
| `auto_solve.inactive_days` | 직원의 마지막 답변 이후 대기 일수. 기본 14 |
| `auto_solve.robotis_role_name` | 직원 역할 이름. 기본 ROBOTIS |
| `auto_solve.solved_tag_name` | 완료 태그 이름. 기본 Solved (`✅Solved`도 인식) |

- 알림과 테이블 동기화는 기본적으로 켜져 있습니다. 각각 `enabled: false`로 끌 수 있습니다.
- 알림만 켜면 Slack App/User Token과 List ID가 필요하지 않습니다.
  자동 완료만 켜면 Slack 설정을 생략할 수 있습니다.
- 테이블 동기화를 끄면 Socket Mode와 `/table_update` 수신도 중단합니다.
  이때 자동 완료가 켜져 있으면 새 대상 포럼 글이 생성될 때만 검사합니다.
- 새 글 알림이 실패해도 자동 완료와 테이블 동기화는 계속 실행합니다.
- `assign` 열 이름, `/app/data` 저장 위치, 전체 재작성 방식은 코드와 Compose에서
  고정합니다. 운영상 바꿀 필요가 있는 대상·활성화 여부·자동 완료 기준만 설정에 둡니다.
- 이전 평면 키(`discord_token`, `forum_channel_ids`, `slack_*_token`,
  `slack_channel_id`, `list_id`)도 읽을 수 있습니다. 이전 키와 대응하는 새 키를
  동시에 쓰면 시작 전에 오류로 중단합니다. 알 수 없는 설정 키도 오류로 처리합니다.
- 이전 `trigger_webhook_url`은 사용하지 않습니다. 읽을 때 경고만 남기며 새 설정에서 제거하세요.
- 설정 변경 후 봇을 재시작해야 반영됩니다. 실제 토큰이 담긴 파일은 커밋하지 마세요.

## 준비 및 실행

Discord Developer Portal에서 봇을 만들고 **MESSAGE CONTENT INTENT**를 켜세요.
대상 포럼에 채널 보기·메시지 기록 보기 권한이 필요하며,
자동 완료에는 **스레드 관리(Manage Threads)** 권한도 필요합니다.
Discord 개발자 모드에서 각 포럼의 채널 ID를 복사해 목록에 넣으세요.

Slack 앱의 Bot Token과 메시지를 받을 채널 ID를 설정하세요.
테이블 동기화에는 Slack List 접근 권한이 있는 토큰과 List ID가 필요합니다.
Socket Mode를 켜고 `connections:write` 권한의 App-Level Token을 설정한 뒤,
앱에 `/table_update` 명령을 등록하세요.

```bash
cp config.example.yaml config.yaml
# config.yaml에 실제 토큰과 대상 ID 입력
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m discord_forum_slack
```

설정 파일 경로는 `DISCORD_BOT_CONFIG_PATH=/path/to/config.yaml` 환경 변수로 바꿀 수 있습니다.
Docker 실행 방법은 아래 배포 명령을 참고하세요. Compose가 `config.yaml`과 `data`를 마운트합니다.

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

기존 알림·동기화에 사용하는 **`discord.forum_channel_ids` 전체**를 자동 완료 대상으로 사용합니다.
따라서 `support〈en〉`, `support〈kr〉`를 함께 등록해두면 둘 다 검사합니다.
각 포럼이 속한 서버의 `ROBOTIS` 역할과 해당 포럼의 `Solved` 또는 `✅Solved` 태그를
별도로 찾습니다. 영문 대소문자는 구분하지 않으며 포럼마다 다른 태그 ID를 사용합니다.
역할·태그 이름이 다르면 `config.example.yaml`의 이름 설정을 변경하세요.
어느 포럼에서 역할/태그를 찾지 못하거나 이름이 중복되면 그 포럼만 건너뜁니다.

이전 단일 포럼 설정의 `auto_solve.forum_channel_name` 또는 `forum_channel_id`가 남아 있으면
그 범위로 계속 제한됩니다. **여러 포럼을 사용하려면 두 값을 지우거나 빈 문자열로 설정**하세요.
`solved_tag_id`도 비워두어 각 포럼에서 태그를 찾게 해야 합니다.
여러 서버를 사용하는 경우 `robotis_role_id` 대신 역할 이름을 사용하세요.

- 새 대상 포럼 글 생성 또는 `/table_update` 실행 시 검사합니다. 주기 작업은 없으며,
  **14일이 지난 뒤 다음 트리거 때** 처리합니다. 일반 댓글 작성 자체는 동기화 트리거가 아닙니다.
- 보관된 글을 포함하여 각 대상 포럼의 미해결 글을 검사합니다. 마지막 사람 메시지가
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
  자동 완료 단계에서 한 포럼의 역할/태그 설정이나 편집이 실패해도 다른 포럼은 계속 검사합니다.
  전체 포럼 목록 조회 실패 시에는 기존과 같이 동기화 자체를 중단합니다.
- 판정과 태그 변경을 먼저 마친 뒤, 변경된 상태로 Slack 테이블을 한 번 재작성합니다.
  로그의 `자동 Solved 처리`로 적용된 글 ID를 확인할 수 있습니다.

회귀 테스트 실행(프로젝트 의존성 설치 후):

```bash
python -m unittest discover -s tests -v
```
