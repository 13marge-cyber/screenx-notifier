# 영화 예매 알리미 v5 전환 체크리스트

## 0. 전환 전

- 현재 v4 Watchdog Disable.
- 현재 v4 watch-loop Disable.
- Pending/Queued를 먼저 Cancel.
- Running을 마지막에 Cancel.
- `git status`가 예상 상태인지 확인.
- v5 ZIP SHA-256 확인.

## 1. Dry-run / 적용

```powershell
py APPLY_V5.py "C:\path\to\screenx-notifier" --dry-run
py APPLY_V5.py "C:\path\to\screenx-notifier"
cd "C:\path\to\screenx-notifier"
```

`data/actions-state`, `data/v3-state`, `data/notifier-state/state*.json`, `.git`은 보존되어야 합니다.

## 2. Push 전 검사

```powershell
py -m pip install -r requirements.txt -r requirements-dev.txt
py -m compileall -q main.py src tests APPLY_V5.py
py main.py --config config.loop.yaml validate
py main.py --config config.loop.yaml check
py -m pytest -q --cov=src --cov-fail-under=90
git diff --check
git status
```

`check`에서 CGV/Megabox 실제 조회 오류가 있으면 push하지 않습니다.

## 3. GitHub

1. commit/push.
2. 실제 `main` 파일이 전달본과 같은지 다시 확인.
3. `영화 예매 알리미 v5 CI` Success 확인.
4. `영화 예매 알리미 v5 실연결 자체진단` 실행.
5. Telegram Rich + Plain fallback + 가짜 오픈 3개 메시지 확인.
6. selftest Success 확인.

메가박스는 local package 환경에서 live POST를 검증할 수 없으므로 **selftest 성공 전에는 실연결 완료로 판정하지 않습니다.**

## 4. 실제 감시 시작

1. `영화 예매 알리미 v5 1분 감시 루프` Enable.
2. Run workflow(main).
3. Running 1 + Pending/Queued 1 확인.
4. heartbeat에서 4개 watcher 확인.
5. 다음 handoff에서 successor 자동 승계 확인.
6. `data/notifier-state/state.json` checkpoint의 main 반영 확인.
7. 실제 신규 회차 Telegram 수신 확인.

## Rollback

legacy state 원본은 삭제하지 않습니다. 문제가 생기면 v5 watch-loop/Watchdog부터 안전하게 중지한 뒤 Git commit을 되돌릴 수 있습니다. `APPLY_V5.py`는 기존 state JSON을 덮어쓰거나 삭제하지 않도록 검증합니다.
