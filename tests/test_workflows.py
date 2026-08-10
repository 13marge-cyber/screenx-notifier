from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_all_workflow_yaml_parses():
    paths = list(WORKFLOWS.glob("*.yml"))
    assert {p.name for p in paths} == {"ci.yml", "selftest.yml", "watch-loop.yml", "watchdog.yml"}
    for path in paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None


def test_all_python_actions_are_full_sha_pinned():
    for name in ("ci.yml", "selftest.yml", "watch-loop.yml"):
        text = _text(name)
        assert f"actions/checkout@{CHECKOUT_SHA}" in text
        assert f"actions/setup-python@{SETUP_PYTHON_SHA}" in text
        assert "actions/checkout@v" not in text
        assert "actions/setup-python@v" not in text
        assert "python-version: '3.11.15'" in text


def test_watch_loop_has_no_old_pat_or_shell_true_masking():
    text = _text("watch-loop.yml")
    assert "WORKFLOW_PAT" not in text
    assert "|| true" not in text
    assert "github.token" in text


def test_watch_loop_checks_out_latest_main_and_queues_successor_before_monitor():
    text = _text("watch-loop.yml")
    assert "ref: main" in text
    assert text.index("후속 실행 정확히 1개 선예약") < text.index("1분 감시 (내부 315분 / 외부 325분 제한)")


def test_watch_loop_has_three_layer_runtime_limits():
    text = _text("watch-loop.yml")
    assert "timeout-minutes: 345" in text
    assert "325m" in text
    assert "--runtime-minutes 315" in text
    assert "--kill-after=60s" in text


def test_watch_loop_checkpoint_targets_version_neutral_state():
    text = _text("watch-loop.yml")
    assert "git add data/notifier-state/" in text
    assert "data/v3-state" not in text
    ignore = (ROOT / "data" / "notifier-state" / ".gitignore").read_text(encoding="utf-8")
    assert "*.json" not in ignore
    assert "state.json" not in ignore


def test_watch_loop_checkpoint_has_bounded_push_retry():
    text = _text("watch-loop.yml")
    assert "for attempt in 1 2 3" in text
    assert "git pull --rebase origin main" in text
    assert "상태 체크포인트 push가 3회 모두 실패" in text


def test_watch_loop_concurrency_is_branch_scoped():
    text = _text("watch-loop.yml")
    assert "screenx-watch-loop-${{ github.ref }}" in text
    assert "cancel-in-progress: false" in text


def test_watchdog_runs_every_ten_minutes_and_maintains_one_successor():
    text = _text("watchdog.yml")
    assert "3,13,23,33,43,53" in text
    assert '.status == "in_progress"' in text
    assert '.status == "queued"' in text
    assert 'if [ "$waiting" -gt 0 ]' in text
    assert "후속 실행 생성" in text
    assert "감시 체인 완전 단절" in text


def test_watchdog_is_explicitly_main_branch_scoped():
    text = _text("watchdog.yml")
    assert "--branch main" in text
    assert "--ref main" in text
    assert "screenx-watchdog-main" in text


def test_watchdog_does_not_resurrect_manual_cancel():
    text = _text("watchdog.yml")
    assert 'latest_conclusion" = "cancelled"' in text
    assert "수동 중지로 간주" in text


def test_watchdog_has_failure_restart_cooldown():
    text = _text("watchdog.yml")
    assert 'latest_conclusion" = "failure"' in text
    assert '"$age" -lt 3600' in text
    assert "1시간 재시작 차단" in text


def test_ci_has_coverage_floor_and_strict_config_validation():
    text = _text("ci.yml")
    assert "--cov=src" in text
    assert "--cov-fail-under=90" in text
    assert "main.py --config config.loop.yaml validate" in text
    assert "compileall" in text


def test_selftest_uses_real_secrets_and_v5_command():
    text = _text("selftest.yml")
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "TELEGRAM_CHAT_IDS" in text
    assert "main.py --config config.loop.yaml selftest" in text


def test_runtime_requirements_include_first_party_tzdata_fallback():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "tzdata==2026.3" in text


def test_ci_forces_zoneinfo_fallback_smoke_test():
    text = _text("ci.yml")
    assert "PYTHONTZPATH" in text
    assert "ZoneInfo('Asia/Seoul')" in text


def test_watch_loop_does_not_add_successor_when_one_is_already_waiting():
    text = _text("watch-loop.yml")
    assert "gh run list" in text
    assert '--workflow watch-loop.yml' in text
    assert '.status == "queued"' in text
    assert 'if [ "$waiting" -gt 0 ]' in text
    assert "추가 예약하지 않음" in text
    assert "중복 예약을 피하기 위해" in text


def test_workflow_display_names_are_v5_and_old_concurrency_ids_are_intentionally_preserved():
    assert _text("ci.yml").startswith("name: 영화 예매 알리미 v5 CI\n")
    assert _text("selftest.yml").startswith("name: 영화 예매 알리미 v5 실연결 자체진단\n")
    assert _text("watch-loop.yml").startswith("name: 영화 예매 알리미 v5 1분 감시 루프\n")
    assert _text("watchdog.yml").startswith("name: 영화 예매 알리미 v5 Watchdog\n")
    assert "screenx-watch-loop-${{ github.ref }}" in _text("watch-loop.yml")
    assert "screenx-watchdog-main" in _text("watchdog.yml")


def test_ci_tracks_v5_apply_script_not_v4():
    text = _text("ci.yml")
    assert "APPLY_V5.py" in text
    assert "APPLY_V4.py" not in text
