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

## 🔄 다른 영화 / 상영관으로 다시 쓰기

극장 코드나 상영관 이름을 찾을 필요 없이, 마법사가 CGV에서 실제 목록을
가져와 고르게 해줍니다.

```bash
python setup_watch.py            # 대화형 설정 (config.yaml 생성)
python setup_watch.py --loop     # GitHub Actions용 config.loop.yaml도 함께
python setup_watch.py --list-movies   # 현재 상영작만 확인
```

물어보는 것은 다섯 가지뿐입니다 — 영화 / 극장 / 상영관 / 날짜 / 주기.
설정을 만들기 전에 **각 날짜가 이미 열렸는지 진단**해서 알려줍니다.

```
  ⚠️  이미 예매가 열린 날짜: 2026-08-10 (13회)
      → 이 날짜들은 기준선에 포함되어 알림이 오지 않습니다.
  ✅ 아직 안 열린 날짜: 4개 → 열리는 즉시 알림
```

이미 열린 날짜만 넣으면 알림이 올 수 없기 때문에, 시작 전에 확인시켜 줍니다.

### GitHub Actions로 다시 감시하기

맥북을 켜두지 않고 감시하려면 Actions 루프를 씁니다.
(감시가 끝나면 워크플로를 비활성화해 두는 것이 기본입니다.)

```bash
# 1. 새 설정 만들기
python setup_watch.py --loop

# 2. 커밋 & 푸시
git add config.loop.yaml && git commit -m "chore: 새 감시 대상 설정" && git push

# 3. 워크플로 다시 켜기
gh workflow enable watch-loop.yml --repo <사용자>/<리포>

# 4. 감시 시작
gh workflow run watch-loop.yml --repo <사용자>/<리포>
```

끝낼 때는 `gh workflow disable watch-loop.yml --repo <사용자>/<리포>`.

필요한 Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS`,
그리고 자동 재실행 체인을 위한 `WORKFLOW_PAT`(Actions: Read and write 권한).

> **주의**: 상태 파일(`data/actions-state`)이 커밋되지 않으면, 실행이
> 교체될 때 기준선이 어긋나 알림이 누락될 수 있습니다. 감시 중에
> `chore: 감지 상태 갱신` 커밋이 주기적으로 올라오는지 확인하세요.

### ⚠️ 감지가 느려지는 함정: `priority_dates`

`priority_dates`에 넣은 날짜는 **매 사이클** 확인하지만, 나머지
`watch_dates`는 `full_scan_every` 사이클에 한 번만 봅니다.
CGV 요청량을 줄이려는 옵션인데, **정작 예매가 열린 날짜가
`priority_dates`에 없으면 그만큼 알림이 늦습니다.**

> 실제 사례: 1분 감시로 설정했지만 `priority_dates`가 특정 하루뿐이라,
> 다른 날짜가 열렸을 때 실효 감지 주기가 5분이 되었습니다.

날짜가 20개 이하라면 **`priority_dates`를 비우고 `full_scan_every: 1`**로
두세요. 마법사는 기본으로 그렇게 설정합니다.

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
