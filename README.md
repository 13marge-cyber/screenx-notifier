# ScreenX Notifier v4.0.2

개인 Telegram 1개로 CGV 용산/영등포 특별관 상영 일정 오픈을 약 1분 간격으로 감시하는 개인용 알리미입니다. v4의 목표는 기능 추가보다 **무알림 위험, 실행 체인 단절, 상태 손상과 검증 흔들림을 줄이는 것**입니다.

## 현재 감시 대상

`config.loop.yaml`에 다음 3개 watcher가 들어 있습니다.

- 용산아이파크몰 / 스파이더맨 / PRIVATE BOX
- 영등포 / 스파이더맨 / SCREENX
- 용산아이파크몰 / 오디세이 / IMAX·아이맥스

영화·극장·특별관의 실질적 역할을 바꿀 때는 기존 `id`를 재사용하지 말고 새 `id`를 사용하세요. 표시용 `name`만 바꾸는 것은 괜찮습니다.

## v4 핵심 설계

- CGV JSON API 직접 조회. Selenium·Cloudflare 우회·병렬 폭격 없음.
- `현재 showtime key - known_keys`만으로 신규 판정. hash/baseline/initialized 없음.
- 같은 극장+날짜 조회는 한 사이클 안에서 캐시해 중복 요청을 줄임.
- CGV transport/HTTP/JSON 연속 실패는 극장별 circuit breaker(회로 차단기)로 격리.
- CGV가 HTTP 200을 반환하더라도 상영 데이터 필드 구조가 달라지면 `SCHEMA_DRIFT`로 fail-closed(오류 시 정상으로 가장하지 않고 중단/장애 처리).
- CGV 시간은 `0700`, `07:00`, `2430`, `25:30` 등을 검증해 처리하며 24·25시 표현을 그대로 보존.
- 지난 날짜는 조회하지 않음.
- watcher 내부 예상 밖 예외는 해당 watcher 장애로 격리해 다른 watcher 감시는 계속함.
- 신규 회차는 **날짜별 Telegram transaction(성공/실패 단위)** 으로 전송. 날짜 A가 성공하고 날짜 B가 실패하면 A만 즉시 state에 확정하고 B만 다음 사이클에 재시도.
- Telegram은 단일 개인채팅만 지원. `python-telegram-bot`/polling/명령어 없음.
- Rich Message 정상 경로, 비일시적 Rich 4xx에 Plain fallback(대체 전송). 401/403은 즉시 실패, 429는 `retry_after`, network/5xx는 제한 재시도.
- Plain 메시지가 길면 Telegram 제한에 맞춰 안전하게 분할.
- 사용자 표시 시각·로그·날짜 판정은 `Asia/Seoul`(KST) 고정. timezone 설정 자체를 받지 않음.
- watcher별 health(건강 상태): 3회 연속 실패 → 장애 알림 1회, 계속 실패 → 스팸 없음, 복구 → 복구 알림 1회.
- 모든 지정 날짜가 끝난 watcher는 성공/복구로 가장하지 않고 하트비트에 `지정 날짜 종료`로 표시.
- 상태는 `data/notifier-state/state.json` 한 곳을 사용하며 버전 번호와 분리.
- 상태 저장은 temp → flush → file fsync → `os.replace` → 가능한 환경에서 directory fsync.
- 이전 정상 state를 `state.backup.json` 한 세대 보관. primary 손상 시 검증된 backup만 복구하며 둘 다 손상이면 빈 상태로 조용히 초기화하지 않음.
- 기존 v2/v3 state의 알려진 `keys`/`known_keys`를 v4 첫 실행 때 자동 이관하고 원본 legacy 파일은 보존.
- Monitor의 시간은 주입 가능하게 만들어 테스트가 실제 오전/오후 시각에 따라 흔들리지 않음.

## GitHub Secrets

기존 Secret 두 개를 그대로 사용합니다.

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_IDS` — **양수 숫자 개인 chat id 정확히 1개**

`WORKFLOW_PAT`은 v4 런타임에서 사용하지 않습니다.

## 로컬 명령

```bash
python main.py --config config.loop.yaml validate
python main.py --config config.loop.yaml check
python main.py --config config.loop.yaml preflight
python main.py --config config.loop.yaml once
python main.py --config config.loop.yaml monitor --runtime-minutes 315
python main.py --config config.loop.yaml selftest
```

- `validate`: Secret 없이 YAML 구조·타입·중복·알 수 없는 키를 엄격 검사.
- `check`: CGV를 1회 읽지만 Telegram/state는 변경하지 않음.
- `preflight`: state는 반드시 정상이어야 함. Telegram 401/403·잘못된 private chat·CGV schema drift는 치명적 오류. Telegram/CGV timeout·5xx 같은 일시장애는 경고 후 감시 체인을 살림.
- `once`: 실제 알림을 포함한 1회 감시.
- `monitor`: 장기 1분 감시.
- `selftest`: 실제 Telegram/CGV 연결과 가짜 오픈 종단간 시험.

## Self-test가 실제로 하는 것

수동 `ScreenX v4 실연결 자체진단` workflow를 실행하면 운영 state를 건드리지 않는 임시 state로 다음을 검사합니다.

1. Telegram `getMe` + `getChat` 및 private chat 확인.
2. CGV 날짜 API와 실제 상영 API 확인.
3. Rich Message가 fallback 없이 실제 Rich로 성공하는지 확인.
4. 의도적으로 잘못된 Rich payload를 보내 Plain fallback이 실제 동작하는지 확인.
5. `🧪 자체진단 · 실제 예매 아님`이라고 명확히 표시한 가짜 신규 회차를 Monitor → Formatter → 실제 Telegram → atomic state 저장까지 통과시킴.
6. 같은 가짜 회차를 한 번 더 검사해 중복 알림이 0건인지 확인.

따라서 정상이라면 **사용자에게 보이는 테스트 메시지는 3건**(Rich 1건, Plain fallback 1건, 가짜 오픈 1건)이며 **실제 CGV 예매 오픈이 아닙니다.**

## 상태 이관

v4 기본 상태 경로:

```text
data/notifier-state/state.json
data/notifier-state/state.backup.json
```

첫 v4 실행에서 state가 아직 없다면 `legacy_state_dirs`를 읽습니다.

```text
data/v3-state/
data/actions-state/
```

지원하는 이전 형태:

- r3 단일 `schema: 3` state
- v3.1.2 `v3_<watcher-id>.json` / `v3_system.json`
- v2 watcher 이름 기반 JSON의 `keys`

이관은 **known key 합집합**만 사용합니다. 기존 legacy 파일은 삭제하지 않으므로 v4 안정화 전까지 rollback(이전 버전 복귀) 자료로 남습니다.

## GitHub Actions 구조

### `ScreenX v4 1분 감시 루프`

1. 실행 시점의 최신 `main` checkout.
2. Python 3.11.15 + 고정 dependency 설치.
3. preflight.
4. 후속 watch-loop 1개를 `github.token`으로 먼저 선예약.
5. 내부 Monitor 최대 315분.
6. shell `timeout` 325분.
7. job `timeout-minutes` 345분.
8. 정상/실패 종료 후 `data/notifier-state/` checkpoint를 main에 최대 3회 재시도해 push.

수동 Cancel은 상태 push를 일부러 건너뜁니다. 러너가 비정상 소실되면 최근 원격 checkpoint 이후 이미 보낸 알림이 중복될 수 있습니다. **누락보다 드문 중복을 우선하는 정책**입니다.

### `ScreenX v4 Watchdog`

10분 간격으로 `main`의 watch-loop만 확인합니다.

- running + queued가 있으면 아무것도 하지 않음.
- running만 있고 queued가 없으면 후속 1개 생성.
- 둘 다 없으면 체인을 다시 시작.
- 가장 최근 run이 `cancelled`이고 active run이 없으면 수동 중지로 간주해 되살리지 않음.
- 최근 failure 뒤에는 1시간 재시작 cooldown(재시작 대기)을 두어 설정 오류로 무한 실패하는 것을 막음.

## 배포 순서

가장 안전한 방법은 제공된 `APPLY_V4.py`를 사용하는 것입니다.

```powershell
py APPLY_V4.py "C:\path\to\screenx-notifier" --dry-run
py APPLY_V4.py "C:\path\to\screenx-notifier"
```

배포 스크립트는 `src`, `tests`, `.github/workflows`를 v4로 교체하고 구형 Docker/deploy/setup_watch 파일을 제거하지만 다음은 보존합니다.

```text
.git/
data/actions-state/
data/v3-state/
data/notifier-state/state.json
data/notifier-state/state.backup.json
```

적용 후에는 즉시 push하지 말고:

```powershell
py -m compileall -q main.py src tests APPLY_V4.py
py main.py --config config.loop.yaml validate
py -m pytest -q
 git status
```

을 먼저 확인하세요.

실서비스 전환은 다음 순서를 권장합니다.

1. 기존 v2/v3 **Running과 Pending/Queued를 모두 취소**하고 옛 watch-loop/watchdog을 Disable.
2. v4를 `main`에 push.
3. `ScreenX v4 CI` 통과 확인.
4. `ScreenX v4 실연결 자체진단` 수동 실행 및 Telegram 테스트 메시지 확인.
5. `ScreenX v4 1분 감시 루프` 수동 실행.
6. running 1개 + queued successor 1개 확인.
7. 다음 handoff에서 queued가 실제 running으로 승계되는지 확인.
8. `data/notifier-state/state.json`이 main에 checkpoint되는지 확인.
9. 실제 신규 CGV 회차가 생겼을 때 end-to-end 알림 수신 확인.

## 감시를 완전히 멈추는 방법

1. `ScreenX v4 Watchdog`을 Disable.
2. `ScreenX v4 1분 감시 루프`를 Disable.
3. **Queued/Pending을 먼저 Cancel**.
4. Running을 Cancel.

후속 queued run을 남겨두고 running만 취소하면 queued가 이어서 시작할 수 있습니다.

## 의도적으로 하지 않는 것

- 단톡방/다중 수신자
- Telegram 명령어·polling
- 자동예매/자동 좌석선택
- Selenium/Cloudflare 우회
- CGV 병렬 요청 폭격
- 같은 사이클 CGV 재시도 폭주
- 알림마다 Git push
- 여러 날짜를 하나의 성공/실패 단위로 묶는 방식

## 운영 한계

- Telegram 서버가 실제 메시지를 받았지만 HTTP 응답만 유실되면 같은 알림이 드물게 재전송될 수 있습니다.
- GitHub runner handoff에는 checkout/setup/preflight 시간만큼 짧은 공백이 생길 수 있습니다.
- GitHub cron은 혼잡 시 지연될 수 있습니다.
- CGV가 endpoint 자체나 정책을 바꾸면 코드 수정이 필요합니다. v4 schema guard는 **조용한 무알림**을 장애로 바꾸는 안전장치이지 API 변경을 자동 적응시키는 기능은 아닙니다.
- repository/branch protection이 `GITHUB_TOKEN`의 Actions 또는 contents write를 막으면 successor dispatch/state checkpoint가 실패할 수 있으므로 실제 GitHub에서 최종 검증해야 합니다.
