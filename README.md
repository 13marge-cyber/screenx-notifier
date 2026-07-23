<div align="center">

# 🎬 Movie Club Ticket Notifier

**영화 동아리를 위한 올인원 예매 알림 텔레그램 봇**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/kimble125/movie-club-ticket-notifier/pulls)

*CGV IMAX 예매 오픈 감지 · 영화제 공지 모니터링 · 범용 웹사이트 변경 알림*

[한국어](#-기능) | [English](#-features-1)

</div>

---

## 🎯 이런 분들을 위한 프로젝트입니다

- 🎟 **CGV 용산 IMAX** 예매 전쟁에서 이기고 싶은 분
- 🏔 **무주산골영화제** 등 영화제 예매 오픈을 놓치고 싶지 않은 분
- 🎬 **영화 동아리** 운영자로서 멤버들에게 예매 알림을 자동화하고 싶은 분
- 🔔 어떤 웹사이트든 **변경 사항을 실시간으로** 감지하고 싶은 분

---

## ✨ 기능

### 🎟 CGV 특별관 예매 오픈 감지
- CGV 신규 사이트의 **공개 JSON API 직접 호출** (Selenium·Chrome 불필요)
- 1회 조회가 1초 내에 끝나 **1분 간격 감시** 가능
- IMAX, 4DX, ScreenX 등 **특별관 필터링**
- 특정 영화만 감시하는 **키워드 필터** 지원
- `watch_dates`로 감시 날짜를 좁혀 필요한 날짜만 정밀 감시
- 새 상영 회차가 생기면 **그 회차만** 골라 텔레그램 알림

### 📢 영화제 공지 모니터링
- 무주산골영화제 등 **게시판 새 글 감지**
- CSS 선택자 기반으로 **어떤 게시판이든** 모니터링 가능
- 예매 오픈, 라인업 공개 등 **키워드 필터링**

### 🔧 범용 웹사이트 변경 감지
- `config.yaml` 하나로 **무한 확장** 가능
- 메가박스, 롯데시네마, 독립영화관 등 자유롭게 추가
- 코딩 없이 **YAML 설정만으로** 새 모니터링 대상 추가

### 🤖 텔레그램 봇 인터페이스

| 명령어 | 설명 |
|--------|------|
| `/start` | 알림 등록 (채팅 ID 자동 감지) |
| `/status` | 모니터링 상태 확인 |
| `/check` | 즉시 전체 확인 실행 |
| `/help` | 도움말 |

---

## 🏗 아키텍처

```
┌─────────────────────────────────────────────────┐
│                 Telegram Bot Engine              │
│          (python-telegram-bot + APScheduler)     │
├─────────┬──────────────┬────────────────────────┤
│  CGV    │   Webpage    │    (확장 가능)          │
│ Crawler │   Crawler    │   Your Custom Crawler   │
│ (JSON   │  (requests)  │                         │
│  API,   │              │                         │
│ requests)│             │                         │
├─────────┴──────────────┴────────────────────────┤
│              State Manager (JSON)                │
│         변경 감지 · 중복 알림 방지 · 해시 비교     │
├─────────────────────────────────────────────────┤
│              config.yaml (YAML)                  │
│     모든 설정을 한 파일에서 관리 · 코딩 불필요      │
└─────────────────────────────────────────────────┘
```

---

## 🚀 빠른 시작

### 1. 사전 준비

- [Telegram BotFather](https://t.me/BotFather)에서 봇 생성 후 토큰 발급
- Python 3.11+ 또는 Docker

### 2. 설치 및 실행

#### 방법 A: Docker (권장)

```bash
git clone https://github.com/kimble125/movie-club-ticket-notifier.git
cd movie-club-ticket-notifier

cp config.example.yaml config.yaml
# config.yaml을 열어 봇 토큰과 채팅 ID를 입력하세요

docker compose up -d
```

#### 방법 B: 직접 실행

```bash
git clone https://github.com/kimble125/movie-club-ticket-notifier.git
cd movie-club-ticket-notifier

pip install -r requirements.txt

cp config.example.yaml config.yaml
# config.yaml을 열어 봇 토큰과 채팅 ID를 입력하세요

python main.py
```

#### 방법 C: 테스트 모드 (1회 확인)

```bash
python main.py --check   # 1회 확인, 결과만 출력 (알림 없음)
python main.py --once    # 1회 확인, 변경이 있으면 알림까지 전송
```

`--once`는 프로세스를 상주시킬 수 없는 GitHub Actions/cron 환경용입니다.

#### 방법 D: 상시 서버 (Oracle Cloud 등 Ubuntu VM)

맥북을 꺼도 계속 감시하려면 서버에 systemd 서비스로 등록하세요:

```bash
git clone <this-repo> && cd movie-club-ticket-notifier
bash deploy/setup-vm.sh   # 안내에 따라 config.yaml 작성 후 재실행
journalctl -u screenx-notifier -f
```

### 3. 채팅 ID 확인

봇을 실행한 후 텔레그램에서 봇에게 `/start`를 보내면 채팅 ID가 자동으로 표시됩니다.
표시된 ID를 `config.yaml`의 `chat_ids`에 추가하세요.

---

## ⚙️ 설정 가이드

`config.yaml` 파일 하나로 모든 것을 제어합니다:

```yaml
telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_ids:
    - "YOUR_CHAT_ID"

watchers:
  # CGV 특별관 모니터링
  - name: "CGV 용산 IMAX"
    type: "cgv"
    enabled: true
    interval_minutes: 5
    settings:
      theater_code: "0013"      # 용산아이파크몰
      hall_keywords: ["IMAX"]
      movie_keywords: []        # 비어있으면 모든 영화

  # 웹페이지 변경 감지
  - name: "무주산골영화제 공지"
    type: "webpage"
    enabled: true
    interval_minutes: 30
    settings:
      url: "https://mjff.or.kr/kor/artyboard/mboard.asp?strBoardID=KZND_Q154"
      selector: "a.brd_tit"
      encoding: "euc-kr"
```

### CGV 극장 코드

| 극장 | 코드 | 지역 코드 |
|------|------|-----------|
| 용산아이파크몰 | 0013 | 01 (서울) |
| 영등포 | 0059 | 01 |
| 왕십리 | 0074 | 01 |
| 강남 | 0056 | 01 |
| 여의도 | 0112 | 01 |
| 수원 | 0012 | 12 (경기) |

### 새 모니터링 대상 추가하기

코딩 없이 `config.yaml`에 항목을 추가하면 됩니다:

```yaml
  - name: "메가박스 코엑스"
    type: "webpage"
    enabled: true
    interval_minutes: 10
    settings:
      url: "https://www.megabox.co.kr/theater/time"
      selector: ".theater-schedule"
      encoding: "utf-8"
      keywords: []
```

---

## 🐳 배포 가이드

### Oracle Cloud (무료 영구 서버)

```bash
# Oracle Cloud VM에 SSH 접속 후
sudo apt update && sudo apt install -y docker.io docker-compose
git clone https://github.com/kimble125/movie-club-ticket-notifier.git
cd movie-club-ticket-notifier
cp config.example.yaml config.yaml
nano config.yaml  # 설정 편집
docker compose up -d
```

### GitHub Actions

`.github/workflows/notify.yml`을 생성하여 주기적 자동 실행도 가능합니다.

---

## 📁 프로젝트 구조

```
movie-club-ticket-notifier/
├── main.py                    # 엔트리포인트
├── config.example.yaml        # 설정 파일 템플릿
├── requirements.txt           # Python 의존성
├── Dockerfile                 # Docker 이미지 빌드
├── docker-compose.yml         # Docker Compose 설정
├── src/
│   ├── config.py              # 설정 로더 및 검증
│   ├── state.py               # 상태 관리 (변경 감지)
│   ├── crawlers/
│   │   ├── cgv.py             # CGV 크롤러 (Selenium + CF bypass)
│   │   └── webpage.py         # 범용 웹페이지 크롤러
│   └── bot/
│       └── telegram_bot.py    # 텔레그램 봇 엔진
└── data/
    └── state/                 # 상태 데이터 (자동 생성)
```

---

## ✨ Features

An all-in-one Telegram notification bot for movie clubs:

- **CGV IMAX Booking Detection** - Selenium-based Cloudflare bypass, special hall filtering
- **Film Festival Monitoring** - Detects new announcements on festival websites
- **Universal Web Change Detection** - Monitor any website via CSS selectors, no coding required
- **YAML-based Configuration** - Add new monitoring targets without writing code
- **Docker Ready** - One-command deployment with Docker Compose

---

## 🤝 기여하기 / Contributing

새로운 크롤러 모듈이나 기능 개선을 환영합니다!

1. Fork this repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### 크롤러 추가 가이드

`src/crawlers/` 디렉토리에 새 크롤러를 추가하려면:

1. `check()` 메서드: `{"items": [...], "raw_data": str}` 반환
2. `format_message()` 메서드: Telegram MarkdownV2 형식 메시지 반환
3. `config.yaml`에 새 `type` 등록

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [cgv-open-push](https://github.com/0w0i0n0g0/cgv-open-push) - CGV 크롤링 구조 참고
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - 텔레그램 봇 프레임워크
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) - 웹 변경 감지 아이디어 참고

---

<div align="center">

**⭐ 이 프로젝트가 도움이 되었다면 Star를 눌러주세요!**

Made with ❤️ for movie lovers

</div>
