# v4 전환 체크리스트

## 전환 전

- 기존 GitHub `main`을 별도 폴더에 새로 clone.
- v4 ZIP SHA-256 확인.
- 기존 `data/actions-state` / `data/v3-state` / `data/notifier-state`가 있으면 삭제하지 않음.
- 기존 watch-loop/watchdog을 Disable하고 Running + Pending/Queued를 모두 취소.

## 적용

```powershell
py APPLY_V4.py "C:\path\to\screenx-notifier" --dry-run
py APPLY_V4.py "C:\path\to\screenx-notifier"
cd "C:\path\to\screenx-notifier"
py -m pip install -r requirements-dev.txt
py -m compileall -q main.py src tests APPLY_V4.py
py main.py --config config.loop.yaml validate
py -m pytest -q
 git status
```

`data/actions-state`, `data/v3-state`, 기존 `data/notifier-state/state*.json`이 삭제/수정된 것으로 보이면 push하지 말고 원인을 먼저 확인합니다.

## GitHub 반영 후

- CI exact runtime 통과.
- Self-test 실제 Telegram 3단계(Rich / Plain fallback / 가짜 오픈) 확인.
- preflight에서 permanent 오류 0 확인.
- watch-loop running 1 + queued 1 확인.
- 2회 이상 handoff 관찰.
- watchdog이 정상 체인에 불필요한 run을 추가하지 않는지 확인.
- v4 state checkpoint가 `main`에 저장되는지 확인.
- 실제 CGV 신규 회차 알림을 받으면 최종 운영 검증 완료.

## Rollback

v4 안정화 전에는 legacy state 파일을 삭제하지 않습니다. 소스 rollback이 필요하면 Git commit을 되돌리고 기존 workflow 정의를 복원할 수 있습니다. v4 state는 version-neutral 경로에 별도로 있으므로 legacy 원본을 덮어쓰지 않습니다.
