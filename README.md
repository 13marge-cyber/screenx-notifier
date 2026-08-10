# 영화 예매 알리미 v5.0.1

개인 Telegram 1개로 **CGV IMAX + 메가박스 DOLBY CINEMA**의 지정 영화/날짜에 새 상영 회차가 생기는지 약 1분 간격으로 감시하는 개인용 알리미입니다.

v5의 우선순위는 기능 수보다 **무알림 누락 방지, 중복 억제, provider(CGV/메가박스) 격리, 상태 보존, GitHub Actions 실행 체인 안정성**입니다.

## 현재 감시 대상

`config.loop.yaml`의 enabled watcher는 정확히 4개입니다.

1. CGV 용산아이파크몰 / IMAX / 오디세이 / 2026-08-29, 2026-08-30
2. CGV 천호 / IMAX / 오디세이 / 2026-08-29, 2026-08-30
3. CGV 광교 / IMAX / 오디세이 / 2026-08-29, 2026-08-30
4. 메가박스 수원AK플라자(수원역) / DOLBY CINEMA / 오디세이 + 스파이더맨 브랜드 뉴 데이 / 2026-08-15, 16, 17

**v5.0.1 조정:** 2026-08-11 사용자 PC의 실제 `check`에서 8/14 DOLBY CINEMA 회차 5개가 이미 확인되어, 신규 오픈 실전 테스트 구간을 8/15~17로 한 칸 이동했습니다. provider 코드는 v5.0.0과 동일하고 watcher ID/이름/날짜 및 관련 회귀검증만 갱신했습니다.

메가박스 watcher의 `hall_keywords`는 **`DOLBY CINEMA`만** 사용합니다. `DOLBY ATMOS`, `DOLBY VISION+ATMOS`, 일반관은 대상이 아닙니다.

사용자에게 보이는 알림 제목:

- `🚨 NEW · 용산 IMAX`
- `🚨 NEW · 천호 IMAX`
- `🚨 NEW · 광교 IMAX`
- `🚨 NEW · 수원AK Dolby`

## v5 provider 구조

공통 Monitor/state/Telegram/health/heartbeat 위에 영화관별 provider만 분리합니다.

```text
ProviderRouter
├─ CGVClient
└─ MegaboxClient
       ↓
Monitor → 신규 판정 → Telegram → atomic state
```

- CGV는 기존 검증된 JSON 조회 경로를 유지합니다.
- 메가박스는 현재 공개 구현에서 사용되는 `SimpleBooking/selectBokdList.do` POST를 기본 상영목록으로 사용합니다.
- 메가박스 현재 공개 타입에는 상영관 이름 필드가 선언되어 있지 않으므로, DOLBY CINEMA를 추측하지 않습니다. 기본 응답에 상영관 메타데이터가 없으면 `schedulePage.do`의 동일 `playSchdlNo`를 이용해 상영관 정보를 보강합니다.
- 상영관을 끝내 증명하지 못하면 해당 날짜를 **빈 일정으로 처리하지 않고 오류로 처리**합니다.

참고한 공개 구현:

- https://github.com/ShimTeacher/megabox-notification-push-alarm
- https://github.com/hmmhmmhm/daiso-mcp
- https://github.com/pyeonjaesik/boodie

## 신규 판정

`현재 showtime key - known_keys`만 신규입니다. baseline/hash/initialized 같은 별도 상태는 사용하지 않습니다.

- CGV key: provider + 날짜 + 영화 + 상영관 + 시작시각
- 메가박스 key: `megabox + 날짜 + playSchdlNo`

메가박스는 회차 고유번호 `playSchdlNo`를 우선하므로 좌석 수나 표시명 변화만으로 같은 회차가 재알림되지 않습니다.

서로 다른 provider는 key에 provider가 포함되므로 충돌하지 않습니다.

## Silent-miss 방지

### CGV

- HTTP 200이어도 예상 데이터 필드가 없거나 비리스트이면 `SCHEMA_DRIFT`.
- falsey 비리스트(`{}`, `""`, `0`, `False`, `None`)를 빈 상영표로 바꾸지 않음.
- watcher 예상 밖 예외는 해당 watcher 장애로 격리.

### Megabox

- `movieFormList`가 존재하고 값이 있으면 반드시 list여야 함. 현재 공개 타입처럼 미래 날짜에서 필드가 생략/`null`될 수 있음을 반영해 **빈 객체**, 명시적 `null`, 또는 `areaBrchList`/`movieList`가 있는 빈 응답은 정상 미오픈 후보로 허용합니다. 대신 독립 runtime canary가 오늘~근일의 **비어 있지 않은 실제 상영표**를 주기적으로 요구해 provider 전체의 가짜-empty를 감시합니다.
- 회차 핵심 필드 `playSchdlNo`, `movieNo`, `brchNo`, 유효 시작시각을 검증.
- 현재 공개 타입에서 optional인 `movieNm`은 같은 응답의 `movieList`로 보강할 수 있음. 끝내 영화명을 확인하지 못하면 오류.
- `playDe`가 생략되면 요청 날짜를 사용하지만, 응답에 다른 날짜가 명시되면 오류.
- 응답이 `statCd=-1`로 요청을 거부하면 빈 일정으로 보지 않고 즉시 오류 처리.
- `areaBrchList`가 제공되면 요청 지점 `0052`가 실제 포함되는지 교차검증하고, 비어 있지 않은 응답의 상영행에 요청 지점이 하나도 없으면 `MEGABOX_BRANCH_MISMATCH`로 실패합니다. 현재 상영표 schema probe에서도 **0052의 실제 상영행**을 요구해 잘못된 area/branch 응답을 정상으로 오인하지 않음.
- 상영관의 **텍스트 식별값**(`theabExpoNm`/`playKindNm`)이 기본 응답에 없으면 `playSchdlNo` 기준 상세 응답으로 보강. `theabNo` 숫자만으로는 DOLBY CINEMA라고 추정하지 않음.
- 미래 8/15~17이 정상적으로 비어 있는 상황과 provider가 모든 요청에 빈 배열을 돌려주는 장애를 구분하기 위해, 운영 중 정상일 때는 **5분마다** 오늘~근일의 비어 있지 않은 수원AK 상영표를 runtime canary로 재검증. canary 실패 시에는 최대 60초 뒤 재검사하여 일시 네트워크 오류를 5분 동안 고정하지 않음. 실패는 대상 날짜의 실제 신규 회차 전송을 막지는 않지만 watcher health 실패로 누적되어 3회 연속이면 장애 알림.
- DOLBY CINEMA 여부를 증명하지 못하면 `HALL_METADATA_UNAVAILABLE`/`SCHEMA_DRIFT`; 절대로 “Dolby 회차 0개”로 조용히 통과시키지 않음.
- `DOLBY CINEMA` 정규화 문자열만 통과시켜 Atmos/Vision+Atmos와 구분.

## 요청량/장애 격리

- 같은 provider+극장+날짜는 한 사이클에서 캐시합니다.
- 요청 간 최소 delay를 둡니다.
- transport/HTTP/JSON 연속 실패는 극장별 circuit breaker로 같은 사이클 폭주를 막습니다.
- CGV와 Megabox client는 서로 분리되어 한 provider 장애가 다른 provider의 조회 코드를 직접 오염시키지 않습니다.
- 지난 날짜는 조회하지 않습니다.

## Telegram 전송 트랜잭션

신규 회차는 **날짜별**로 전송/상태 확정합니다.

예: 8/15 알림 성공, 8/16 전송 실패라면 8/15 key만 즉시 state에 저장하고 8/16만 다음 사이클에 재시도합니다.

- Rich Message 우선.
- Rich의 비일시적 4xx 형식/API 실패 → Plain fallback.
- 401/403 → 인증/권한 오류로 실패.
- 429 → `retry_after` 존중.
- network/5xx → 제한 재시도 후 transport backoff.
- Plain은 Telegram 길이 제한에 맞춰 안전 분할.
- 개인 private chat 1개만 지원.
- Telegram 명령어/polling/자동예매 기능 없음.

## 알림 예시

```text
🚨 NEW · 수원AK Dolby

새 상영 일정 오픈
⏰ 감지 시각: 2026-08-10 23:30
━━━━━━━━━━━━━━━━━━━━

📅 2026-08-15 (토)
🎥 오디세이
🏛 DOLBY CINEMA
⏰ 09:10, 12:40

📅 2026-08-15 (토)
🎥 스파이더맨: 브랜드 뉴 데이
🏛 DOLBY CINEMA
⏰ 15:30

━━━━━━━━━━━━━━━━━━━━
```

같은 날짜에 두 영화가 모두 새로 생기면 한 날짜 transaction 안에서 영화/상영관별 블록으로 표시됩니다.

## Health / heartbeat

- watcher 3회 연속 실패 → 장애 알림 1회.
- 계속 실패해도 반복 장애 스팸 없음.
- 복구 후 복구 알림 1회.
- 지정 날짜가 모두 끝난 watcher는 하트비트에 `지정 날짜 종료`.
- 하트비트 기본 시각은 KST 09:00 이후 첫 정상 사이클에서 하루 1회.
- 같은 날 감시 대상 구성이 바뀌면 watcher signature가 달라져 새 구성 하트비트를 1회 다시 보냅니다.

## State

기본 경로:

```text
data/notifier-state/state.json
data/notifier-state/state.backup.json
```

- temp → flush → file fsync → `os.replace` → 가능한 환경에서 directory fsync.
- 저장 직전의 정상 primary를 backup 한 세대 보관.
- primary 손상 시 검증된 backup만 복구.
- primary+backup 모두 손상이면 빈 state로 조용히 초기화하지 않고 실패.
- 기존 v2/v3 legacy state 파일은 삭제하지 않음.
- state schema는 v4에서 검증된 schema 4를 그대로 사용합니다. v5 provider 추가는 state 구조 변경이 아니므로 불필요한 schema bump를 하지 않습니다.

## KST

모든 사용자 표시 시각, 날짜 종료 판정, heartbeat는 `Asia/Seoul` 고정입니다. Windows/최소 런타임을 위해 `tzdata`를 고정 dependency로 포함합니다.

## GitHub Secrets

필요한 Secret은 2개뿐입니다.

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_IDS` — 양수 숫자 개인 chat id 정확히 1개

`WORKFLOW_PAT`은 사용하지 않습니다.

## 로컬 명령

```bash
python main.py --config config.loop.yaml validate
python main.py --config config.loop.yaml check
python main.py --config config.loop.yaml preflight
python main.py --config config.loop.yaml once
python main.py --config config.loop.yaml monitor --runtime-minutes 315
python main.py --config config.loop.yaml selftest
```

- `validate`: Secret 없이 YAML 구조/타입/provider별 필수값/중복/알 수 없는 키 검사.
- `check`: CGV+Megabox를 실제 1회 읽고 Telegram/state는 변경하지 않음.
- `preflight`: state, Telegram, provider 연결을 사전 점검. 영구 auth/schema 오류는 차단하고 timeout/5xx 같은 일시장애는 경고 후 체인을 살림.
- `once`: 실제 알림 포함 1회.
- `monitor`: 장기 감시.
- `selftest`: 실제 Telegram + CGV + Megabox live schema + 가짜 오픈 E2E.

## v5 selftest가 검증하는 것

수동 `영화 예매 알리미 v5 실연결 자체진단` workflow는 운영 state를 건드리지 않는 임시 state로 다음을 검사합니다.

1. Telegram `getMe` + `getChat` + private chat.
2. CGV 용산/천호/광교 각 지점 날짜 API.
3. 메가박스 수원AK의 오늘부터 짧은 범위에서 **비어 있지 않은 실제 상영표**를 찾아 current SimpleBooking schema, 지점 0052, 상영관 식별 가능 여부 확인.
4. 설정된 모든 CGV/Megabox 대상 날짜를 production scan 경로로 조회하여 failed date가 0인지 확인.
5. 실제 Rich Message 발송.
6. production과 같은 Rich 400→Plain 분기 판단을 synthetic 400으로 자극하고 Plain 메시지는 실제 Telegram으로 발송.
7. `🧪 자체진단 · 실제 예매 아님` 가짜 회차를 Monitor → Formatter → 실제 Telegram → atomic state로 통과.
8. 동일 가짜 회차 두 번째 실행에서 중복 알림 0건 확인.

정상 시 사용자에게 보이는 자체진단 메시지는 3건(Rich, Plain fallback, 가짜 오픈)입니다.

**중요:** 메가박스 API는 이 배포 ZIP을 만든 격리 환경에서 외부 DNS가 차단되어 실제 POST를 수행할 수 없습니다. 따라서 로컬 unit test 통과만으로 메가박스 실연결 완료라고 판정하지 않습니다. GitHub Actions의 v5 selftest가 실제 live gate입니다.

## GitHub Actions

### `영화 예매 알리미 v5 1분 감시 루프`

1. 최신 `main` checkout.
2. Python 3.11.15 + 고정 dependencies.
3. preflight.
4. 이미 대기 successor가 있는지 확인 후, 없을 때만 후속 실행 1개 선예약.
5. 내부 Monitor 315분.
6. GNU timeout 325분.
7. Actions job timeout 345분.
8. 정상/실패 종료 후 `data/notifier-state/` checkpoint를 main에 최대 3회 push 재시도.

수동 Cancel은 checkpoint를 일부러 건너뜁니다. runner 비정상 소실 시 최신 원격 checkpoint 이후 이미 보낸 메시지가 드물게 중복될 수 있으며, **누락보다 중복을 우선**합니다.

### `영화 예매 알리미 v5 Watchdog`

10분 간격으로 `main` watch-loop만 봅니다.

- running + queued/pending 존재 → 아무 작업 없음.
- running만 존재 → 후속 1개 생성.
- 둘 다 없음 → 체인 재시작.
- 가장 최근 run이 cancelled이고 active run 없음 → 수동 중지로 간주, 되살리지 않음.
- 최근 failure 뒤 1시간 cooldown.

### concurrency ID

workflow 표시명은 v5로 바뀌지만 내부 concurrency group은 의도적으로 기존
`screenx-watch-loop-${{ github.ref }}` / `screenx-watchdog-main`을 유지합니다.

이 값은 사용자 표시명이 아니라 **v4→v5 전환 중 구버전과 신버전이 서로 다른 group으로 동시에 돌지 않게 하는 안전 식별자**입니다.

## 안전 배포

`APPLY_V5.py`를 사용합니다.

```powershell
py APPLY_V5.py "C:\path\to\screenx-notifier" --dry-run
py APPLY_V5.py "C:\path\to\screenx-notifier"
```

배포 스크립트는 `src`, `tests`, `.github/workflows`를 교체하고 구형 배포 파일을 제거하지만 아래는 보존합니다.

```text
.git/
data/actions-state/
data/v3-state/
data/notifier-state/state.json
data/notifier-state/state.backup.json
```

적용 후 push 전에:

```powershell
py -m compileall -q main.py src tests APPLY_V5.py
py main.py --config config.loop.yaml validate
py main.py --config config.loop.yaml check
py -m pytest -q
git diff --check
git status
```

`check`는 실네트워크 검사이므로 실패 시 내용을 확인하고 push를 중단합니다.

## v4→v5 전환 순서

현재 v4 감시가 살아 있다면 먼저:

1. 기존 Watchdog Disable.
2. 기존 watch-loop Disable.
3. Pending/Queued를 먼저 Cancel.
4. Running Cancel.
5. v5 적용 및 로컬 검사.
6. commit/push.
7. `영화 예매 알리미 v5 CI` 성공 확인.
8. `영화 예매 알리미 v5 실연결 자체진단` 성공 + Telegram 3개 메시지 확인.
9. v5 watch-loop Enable/Run.
10. Running 1 + queued successor 1 확인.
11. handoff에서 이전 run completed / successor running / 새 successor queued 확인.
12. state checkpoint가 실제 `main`에 반영되는지 확인.
13. 실제 신규 회차 알림 수신 후 end-to-end 실서비스 검증 완료.

## 의도적으로 하지 않는 것

- 자동예매/자동 좌석선택
- Selenium/Cloudflare 우회
- 다중 Telegram 수신자/단톡방
- Telegram bot command/polling
- 과도한 병렬 요청
- 같은 사이클 무제한 retry
- 알림마다 Git push
- DOLBY ATMOS를 DOLBY CINEMA로 간주
- 상영관 식별 불가 응답을 “회차 없음”으로 간주

## 알려진 운영 한계

- 영화관 사이트/API가 변경되면 수정이 필요합니다. schema guard는 변경을 자동 적응하는 기능이 아니라 **조용한 무알림을 눈에 보이는 장애로 바꾸는 안전장치**입니다.
- Telegram이 메시지를 실제 수신했지만 HTTP 응답만 유실되는 극단적 경우 중복 가능성이 있습니다.
- GitHub runner handoff에는 checkout/setup/preflight 시간만큼 짧은 공백이 생길 수 있습니다.
- GitHub 권한/branch protection이 `GITHUB_TOKEN`의 Actions/contents write를 막으면 successor/state checkpoint가 실패할 수 있으므로 실제 GitHub에서 최종 검증해야 합니다.
