#!/usr/bin/env bash
#
# Oracle Cloud(또는 임의의 Ubuntu 서버)에 ScreenX 알림 봇을 상시 실행으로 설치합니다.
#
# 사용법:
#   1) VM에 SSH 접속
#   2) 이 저장소를 클론한 뒤 실행:
#        bash deploy/setup-vm.sh
#   3) 안내에 따라 config.yaml에 봇 토큰과 채팅 ID를 입력
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="screenx-notifier"

echo "==> 설치 위치: $REPO_DIR"

# ── 1. 파이썬 준비 ────────────────────────────────────────────
echo "==> 파이썬 및 venv 설치"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip

echo "==> 가상환경 생성 및 의존성 설치"
python3 -m venv "$REPO_DIR/.venv"
# Selenium/Chrome은 필요 없습니다 (CGV JSON API 직접 호출).
"$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/.venv/bin/pip" install --quiet \
    "python-telegram-bot[job-queue]>=22.0" pyyaml requests beautifulsoup4

# ── 2. 설정 파일 ──────────────────────────────────────────────
if [[ ! -f "$REPO_DIR/config.yaml" ]]; then
    cp "$REPO_DIR/config.example.yaml" "$REPO_DIR/config.yaml"
    echo
    echo "!! config.yaml을 만들었습니다. 봇 토큰과 채팅 ID를 입력하세요:"
    echo "     nano $REPO_DIR/config.yaml"
    echo "   입력 후 이 스크립트를 다시 실행하세요."
    exit 1
fi

# ── 3. 동작 확인 ──────────────────────────────────────────────
echo "==> 크롤러 동작 확인 (알림은 보내지 않음)"
cd "$REPO_DIR"
"$REPO_DIR/.venv/bin/python" main.py --check

# ── 4. systemd 등록 ───────────────────────────────────────────
echo "==> systemd 서비스 등록"
sed "s|/home/ubuntu/movie-club-ticket-notifier|$REPO_DIR|g; s|^User=.*|User=$USER|" \
    "$REPO_DIR/deploy/$SERVICE_NAME.service" \
    | sudo tee "/etc/systemd/system/$SERVICE_NAME.service" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 3
sudo systemctl status "$SERVICE_NAME" --no-pager --lines=15 || true

cat <<EOF

────────────────────────────────────────────────
설치 완료.

  상태 확인 : sudo systemctl status $SERVICE_NAME
  실시간 로그: journalctl -u $SERVICE_NAME -f
  재시작     : sudo systemctl restart $SERVICE_NAME
  중지       : sudo systemctl stop $SERVICE_NAME
  자동시작 해제: sudo systemctl disable $SERVICE_NAME

텔레그램에서 /status 를 보내 봇이 살아있는지 확인하세요.
────────────────────────────────────────────────
EOF
