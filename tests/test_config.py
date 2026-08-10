from __future__ import annotations

import copy

import pytest

from src.config import ConfigError, validate_config


def raw_config():
    return {
        "telegram": {},
        "monitor": {"interval_seconds": 60, "state_dir": "./data/notifier-state", "legacy_state_dirs": ["./data/actions-state"], "heartbeat": {"time": "09:00"}},
        "cgv": {"timeout_seconds": 6, "request_delay_seconds": 0.2, "circuit_breaker_failures": 3},
        "watchers": [
            {
                "id": "one",
                "name": "one",
                "theater_code": "0013",
                "theater_name": "용산",
                "alert_title": "용산 IMAX",
                "hall_keywords": ["IMAX"],
                "movie_keywords": ["오디세이"],
                "dates": ["2026-08-21"],
            }
        ],
    }


def test_valid_config_without_secrets():
    cfg = validate_config(raw_config(), require_secrets=False)
    assert cfg["watchers"][0]["co_cd"] == "A420"
    assert "timezone" not in cfg["monitor"]["heartbeat"]
    assert cfg["monitor"]["state_dir"] == "./data/notifier-state"


def test_valid_single_chat_secret():
    cfg = validate_config(
        raw_config(),
        require_secrets=True,
        env={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_IDS": "12345"},
    )
    assert cfg["telegram"]["chat_id"] == "12345"


def test_multiple_chat_ids_rejected():
    with pytest.raises(ConfigError, match="1개만 허용"):
        validate_config(
            raw_config(),
            require_secrets=True,
            env={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_IDS": "1,2"},
        )


def test_missing_token_rejected():
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        validate_config(raw_config(), require_secrets=True, env={"TELEGRAM_CHAT_IDS": "1"})


def test_duplicate_watcher_id_rejected():
    cfg = raw_config()
    cfg["watchers"].append(copy.deepcopy(cfg["watchers"][0]))
    with pytest.raises(ConfigError, match="중복"):
        validate_config(cfg, require_secrets=False)


def test_bad_date_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["dates"] = ["2026-02-30"]
    with pytest.raises(ConfigError, match="날짜"):
        validate_config(cfg, require_secrets=False)


def test_duplicate_dates_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["dates"] = ["2026-08-21", "2026-08-21"]
    with pytest.raises(ConfigError, match="중복 날짜"):
        validate_config(cfg, require_secrets=False)


def test_empty_keyword_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["hall_keywords"] = []
    with pytest.raises(ConfigError, match="hall_keywords"):
        validate_config(cfg, require_secrets=False)


def test_interval_too_fast_rejected():
    cfg = raw_config()
    cfg["monitor"]["interval_seconds"] = 1
    with pytest.raises(ConfigError, match="30~3600"):
        validate_config(cfg, require_secrets=False)


def test_chat_ids_split_on_whitespace_and_reject_multiple():
    with pytest.raises(ConfigError, match="1개만 허용"):
        validate_config(
            raw_config(),
            require_secrets=True,
            env={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_IDS": "12345\n67890"},
        )


def test_group_chat_id_rejected_for_private_only_v5():
    with pytest.raises(ConfigError, match="개인 채팅"):
        validate_config(
            raw_config(),
            require_secrets=True,
            env={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_IDS": "-100123456"},
        )


def test_non_numeric_chat_id_rejected():
    with pytest.raises(ConfigError, match="개인 채팅"):
        validate_config(
            raw_config(),
            require_secrets=True,
            env={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_IDS": "@channel"},
        )


def test_non_ascii_watcher_id_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["id"] = "용산_imax"
    with pytest.raises(ConfigError, match="영문/숫자"):
        validate_config(cfg, require_secrets=False)


def test_duplicate_watcher_name_rejected():
    cfg = raw_config()
    second = copy.deepcopy(cfg["watchers"][0])
    second["id"] = "two"
    cfg["watchers"].append(second)
    with pytest.raises(ConfigError, match="name 중복"):
        validate_config(cfg, require_secrets=False)


def test_all_watchers_disabled_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["enabled"] = False
    with pytest.raises(ConfigError, match="enabled=true"):
        validate_config(cfg, require_secrets=False)


def test_string_false_is_rejected_instead_of_becoming_true():
    cfg = raw_config()
    cfg["watchers"][0]["enabled"] = "false"
    with pytest.raises(ConfigError, match="true 또는 false"):
        validate_config(cfg, require_secrets=False)


def test_bad_numeric_setting_becomes_config_error():
    cfg = raw_config()
    cfg["cgv"]["timeout_seconds"] = "six"
    with pytest.raises(ConfigError, match="숫자"):
        validate_config(cfg, require_secrets=False)


def test_empty_state_dir_rejected(config_copy):
    config_copy["monitor"]["state_dir"] = ""
    with pytest.raises(ConfigError, match="state_dir"):
        validate_config(config_copy, require_secrets=False)


def test_non_string_keyword_rejected(config_copy):
    config_copy["watchers"][0]["hall_keywords"] = [None]
    with pytest.raises(ConfigError, match="문자열"):
        validate_config(config_copy, require_secrets=False)


def test_non_string_required_text_rejected(config_copy):
    config_copy["watchers"][0]["theater_name"] = ["용산"]
    with pytest.raises(ConfigError, match="문자열"):
        validate_config(config_copy, require_secrets=False)


def test_non_string_state_dir_rejected(config_copy):
    config_copy["monitor"]["state_dir"] = {"path": "data"}
    with pytest.raises(ConfigError, match="문자열"):
        validate_config(config_copy, require_secrets=False)


def test_timezone_setting_is_rejected_because_kst_is_fixed():
    cfg = raw_config()
    cfg["monitor"]["heartbeat"]["timezone"] = "UTC"
    with pytest.raises(ConfigError, match="timezone"):
        validate_config(cfg, require_secrets=False)


@pytest.mark.parametrize("obsolete_key", ["priority_dates", "full_scan_every", "max_retries", "cooldown_minutes"])
def test_obsolete_top_or_monitor_setting_is_not_silently_ignored(obsolete_key):
    cfg = raw_config()
    cfg["monitor"][obsolete_key] = 1
    with pytest.raises(ConfigError, match="지원하지 않는"):
        validate_config(cfg, require_secrets=False)


def test_unknown_watcher_key_is_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["typo_hall_keyword"] = ["IMAX"]
    with pytest.raises(ConfigError, match="지원하지 않는"):
        validate_config(cfg, require_secrets=False)


def test_legacy_state_dirs_default_and_explicit_are_normalized():
    cfg = raw_config()
    cfg["monitor"].pop("legacy_state_dirs")
    validated = validate_config(cfg, require_secrets=False)
    assert validated["monitor"]["legacy_state_dirs"] == ["./data/v3-state", "./data/actions-state"]


def test_duplicate_legacy_state_dir_rejected():
    cfg = raw_config()
    cfg["monitor"]["legacy_state_dirs"] = ["a", "a"]
    with pytest.raises(ConfigError, match="중복 경로"):
        validate_config(cfg, require_secrets=False)


def test_telegram_values_must_not_be_committed_in_yaml():
    cfg = raw_config()
    cfg["telegram"]["bot_token"] = "do-not-store"
    with pytest.raises(ConfigError, match="지원하지 않는"):
        validate_config(cfg, require_secrets=False)


def test_bad_log_level_rejected():
    cfg = raw_config()
    cfg["log_level"] = "VERBOSE"
    with pytest.raises(ConfigError, match="log_level"):
        validate_config(cfg, require_secrets=False)

@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda c: c.__setitem__("monitor", []), "monitor"),
        (lambda c: c["monitor"].__setitem__("heartbeat", []), "heartbeat"),
        (lambda c: c.__setitem__("cgv", []), "cgv"),
        (lambda c: c.__setitem__("watchers", []), "watchers"),
        (lambda c: c["watchers"].__setitem__(0, "bad"), "watchers"),
        (lambda c: c.__setitem__("telegram", []), "telegram"),
    ],
)
def test_structural_type_errors_fail_early(mutator, message):
    cfg = raw_config()
    mutator(cfg)
    with pytest.raises(ConfigError, match=message):
        validate_config(cfg, require_secrets=False)


def test_bool_is_not_accepted_as_integer_interval():
    cfg = raw_config()
    cfg["monitor"]["interval_seconds"] = True
    with pytest.raises(ConfigError, match="정수"):
        validate_config(cfg, require_secrets=False)


def test_invalid_heartbeat_clock_rejected():
    cfg = raw_config()
    cfg["monitor"]["heartbeat"]["time"] = "25:00"
    with pytest.raises(ConfigError, match="HH:MM"):
        validate_config(cfg, require_secrets=False)


def test_non_list_dates_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["dates"] = "2026-08-21"
    with pytest.raises(ConfigError, match="리스트"):
        validate_config(cfg, require_secrets=False)


def test_non_string_date_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["dates"] = [20260821]
    with pytest.raises(ConfigError, match="문자열"):
        validate_config(cfg, require_secrets=False)


def test_invalid_theater_code_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["theater_code"] = "013"
    with pytest.raises(ConfigError, match="4자리"):
        validate_config(cfg, require_secrets=False)


def test_bad_cgv_bounds_are_rejected():
    cfg = raw_config()
    cfg["cgv"]["request_delay_seconds"] = 6
    with pytest.raises(ConfigError, match="0~5"):
        validate_config(cfg, require_secrets=False)
    cfg = raw_config()
    cfg["cgv"]["circuit_breaker_failures"] = 11
    with pytest.raises(ConfigError, match="1~10"):
        validate_config(cfg, require_secrets=False)


def test_megabox_watcher_requires_area_code_and_rejects_cgv_only_field():
    cfg = raw_config()
    w = cfg["watchers"][0]
    w["provider"] = "megabox"
    w.pop("co_cd", None)
    with pytest.raises(ConfigError, match="area_code"):
        validate_config(cfg, require_secrets=False)
    w["area_code"] = "30"
    w["co_cd"] = "A420"
    with pytest.raises(ConfigError, match="co_cd"):
        validate_config(cfg, require_secrets=False)


def test_unknown_provider_rejected():
    cfg = raw_config()
    cfg["watchers"][0]["provider"] = "lotte"
    with pytest.raises(ConfigError, match="cgv 또는 megabox"):
        validate_config(cfg, require_secrets=False)


def test_live_config_is_three_cgv_imax_plus_one_suwonak_dolby_test_watcher():
    from pathlib import Path
    from src.config import load_config

    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.loop.yaml", require_secrets=False)
    watchers = cfg["watchers"]
    assert [(w["provider"], w["theater_code"], w["alert_title"]) for w in watchers] == [
        ("cgv", "0013", "용산 IMAX"),
        ("cgv", "0199", "천호 IMAX"),
        ("cgv", "0257", "광교 IMAX"),
        ("megabox", "0052", "수원AK Dolby"),
    ]
    cgv = watchers[:3]
    assert all(w["movie_keywords"] == ["오디세이"] for w in cgv)
    assert all(w["hall_keywords"] == ["IMAX", "아이맥스"] for w in cgv)
    assert all(w["dates"] == ["2026-08-29", "2026-08-30"] for w in cgv)
    mb = watchers[3]
    assert mb["id"] == "megabox_suwonak_dolby_odyssey_spiderman_20260815_17"
    assert mb["name"] == "메가박스 수원AK Dolby · 오디세이+스파이더맨 · 8/15-17"
    assert mb["area_code"] == "30"
    assert mb["hall_keywords"] == ["DOLBY CINEMA"]
    assert mb["movie_keywords"] == ["오디세이", "스파이더맨 브랜드 뉴 데이"]
    assert mb["dates"] == ["2026-08-15", "2026-08-16", "2026-08-17"]
    assert len({w["id"] for w in watchers}) == 4


def test_megabox_runtime_canary_interval_defaults_and_bounds():
    base_mb = {"timeout_seconds": 8, "request_delay_seconds": 0.3, "circuit_breaker_failures": 3, "schema_probe_days": 4}
    cfg = raw_config()
    cfg["megabox"] = dict(base_mb)
    validated = validate_config(cfg, require_secrets=False)
    assert validated["megabox"]["runtime_canary_interval_seconds"] == 900

    for bad in (299, 3601, True):
        cfg = raw_config()
        cfg["megabox"] = dict(base_mb, runtime_canary_interval_seconds=bad)
        with pytest.raises(ConfigError):
            validate_config(cfg, require_secrets=False)
