FROM python:3.11-slim

# ── 시스템 의존성 설치 (Chrome + 한글 폰트) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    unzip \
    fonts-nanum \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── 작업 디렉토리 설정 ──
WORKDIR /app

# ── Python 의존성 설치 ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 소스 코드 복사 ──
COPY . .

# ── 상태 저장 디렉토리 생성 ──
RUN mkdir -p /app/data/state

# ── 환경변수 기본값 ──
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/app/config.yaml

# ── 실행 ──
CMD ["python", "main.py"]
