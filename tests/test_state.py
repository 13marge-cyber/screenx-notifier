from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.state import SCHEMA_VERSION, StateError, StateManager


def v4_state(*, keys=None, heartbeat=None):
    return {
        "schema": 4,
        "watchers": {
            "w": {
                "known_keys": list(keys or []),
                "health": {
                    "consecutive_failures": 0,
                    "down_notified": False,
                    "last_success_at": None,
                    "last_error": None,
                },
            }
        },
        "meta": {"last_heartbeat_date": heartbeat, "migration_sources": []},
    }


def watcher_config(watcher_id="w", name="Watcher Name"):
    return {"id": watcher_id, "name": name}


def test_atomic_roundtrip(tmp_path):
    state = StateManager(tmp_path)
    state.load()
    state.watcher("a")["known_keys"] = ["k1"]
    state.save()
    again = StateManager(tmp_path)
    again.load()
    assert again.watcher("a")["known_keys"] == ["k1"]
    assert not list(tmp_path.glob("*.tmp"))


def test_default_health(tmp_path):
    state = StateManager(tmp_path)
    state.load()
    health = state.watcher("a")["health"]
    assert health["consecutive_failures"] == 0
    assert health["down_notified"] is False


def test_corrupt_json_fails_closed_without_backup(tmp_path):
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(StateError, match="손상.*백업"):
        StateManager(tmp_path).load()


def test_wrong_schema_fails_closed_without_backup(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"schema": 2, "watchers": {}, "meta": {}}), encoding="utf-8")
    with pytest.raises(StateError, match="손상.*백업"):
        StateManager(tmp_path).load()


def test_valid_backup_recovers_corrupt_primary(tmp_path):
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    backup = v4_state(keys=["known"])
    (tmp_path / "state.backup.json").write_text(json.dumps(backup), encoding="utf-8")
    state = StateManager(tmp_path)
    loaded = state.load()
    assert loaded["watchers"]["w"]["known_keys"] == ["known"]
    restored = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert restored["watchers"]["w"]["known_keys"] == ["known"]


def test_missing_primary_recovers_valid_backup(tmp_path):
    (tmp_path / "state.backup.json").write_text(json.dumps(v4_state(keys=["b"])), encoding="utf-8")
    state = StateManager(tmp_path)
    state.load()
    assert state.watcher("w")["known_keys"] == ["b"]
    assert (tmp_path / "state.json").exists()


def test_both_primary_and_backup_corrupt_fail_closed(tmp_path):
    (tmp_path / "state.json").write_text("{bad", encoding="utf-8")
    (tmp_path / "state.backup.json").write_text("{also-bad", encoding="utf-8")
    with pytest.raises(StateError, match="모두 손상"):
        StateManager(tmp_path).load()


def test_save_creates_previous_generation_backup(tmp_path):
    state = StateManager(tmp_path)
    state.load()
    state.watcher("w")["known_keys"] = ["one"]
    state.save()
    state.watcher("w")["known_keys"] = ["one", "two"]
    state.save()
    backup = json.loads((tmp_path / "state.backup.json").read_text(encoding="utf-8"))
    primary = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert backup["watchers"]["w"]["known_keys"] == ["one"]
    assert primary["watchers"]["w"]["known_keys"] == ["one", "two"]


def test_corrupt_primary_is_never_copied_over_good_backup_on_save(tmp_path):
    good = v4_state(keys=["safe"])
    (tmp_path / "state.backup.json").write_text(json.dumps(good), encoding="utf-8")
    state = StateManager(tmp_path)
    state.load()  # recovers primary from backup
    (tmp_path / "state.json").write_text("{corrupt", encoding="utf-8")
    state.watcher("w")["known_keys"] = ["safe", "new"]
    state.save()
    backup = json.loads((tmp_path / "state.backup.json").read_text(encoding="utf-8"))
    assert backup["watchers"]["w"]["known_keys"] == ["safe"]


def test_probe_writable(tmp_path):
    state = StateManager(tmp_path)
    state.probe_writable()
    assert not list(tmp_path.glob(".probe-*"))


def test_snapshot_is_copy(tmp_path):
    state = StateManager(tmp_path)
    state.load()
    snap = state.snapshot()
    snap["schema"] = 999
    assert state.data["schema"] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "mutator,pattern",
    [
        (lambda d: d["watchers"]["w"].update({"health": []}), "health"),
        (lambda d: d["watchers"]["w"].update({"known_keys": [123]}), "known_keys"),
        (lambda d: d["watchers"]["w"]["health"].update({"consecutive_failures": "3"}), "consecutive_failures"),
        (lambda d: d["watchers"]["w"]["health"].update({"down_notified": 1}), "down_notified"),
        (lambda d: d["meta"].update({"last_heartbeat_date": "not-a-date"}), "last_heartbeat_date"),
        (lambda d: d["meta"].update({"last_heartbeat_date": 123}), "last_heartbeat_date"),
        (lambda d: d["meta"].update({"migration_sources": [123]}), "migration_sources"),
    ],
)
def test_invalid_v4_state_fails_closed(tmp_path, mutator, pattern):
    data = v4_state()
    mutator(data)
    (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateError) as exc:
        StateManager(tmp_path).load()
    # The public error intentionally describes failed recovery, while the cause keeps the exact field.
    chain = str(exc.value) + " " + str(exc.value.__cause__ or "")
    assert pattern in chain


def test_migrates_r3_single_state_and_heartbeat(tmp_path):
    new_dir = tmp_path / "new"
    old_dir = tmp_path / "old-r3"
    old_dir.mkdir()
    raw = {
        "schema": 3,
        "watchers": {
            "w": {
                "known_keys": ["r3-key"],
                "health": {"consecutive_failures": 2, "down_notified": False},
            }
        },
        "meta": {"last_heartbeat_date": "2026-08-08"},
    }
    (old_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
    state = StateManager(new_dir, [old_dir])
    state.load([watcher_config()])
    assert state.watcher("w")["known_keys"] == ["r3-key"]
    assert state.watcher("w")["health"]["consecutive_failures"] == 2
    assert state.meta()["last_heartbeat_date"] == "2026-08-08"
    assert str(old_dir) in state.meta()["migration_sources"]
    assert (old_dir / "state.json").exists()


def test_migrates_v312_per_watcher_state(tmp_path):
    new_dir = tmp_path / "new"
    old_dir = tmp_path / "old-v312"
    old_dir.mkdir()
    (old_dir / "v3_w.json").write_text(
        json.dumps({"schema": 3, "known_keys": ["v312-key"], "health": {}}), encoding="utf-8"
    )
    (old_dir / "v3_system.json").write_text(
        json.dumps({"schema": 3, "last_heartbeat_date": "2026-08-07"}), encoding="utf-8"
    )
    state = StateManager(new_dir, [old_dir])
    state.load([watcher_config()])
    assert state.watcher("w")["known_keys"] == ["v312-key"]
    assert state.meta()["last_heartbeat_date"] == "2026-08-07"


def test_migrates_v2_name_based_keys_without_modifying_source(tmp_path):
    new_dir = tmp_path / "new"
    old_dir = tmp_path / "actions-state"
    old_dir.mkdir()
    legacy = old_dir / "Watcher_Name.json"
    original = {"keys": ["a", "b"], "hash": "legacy", "initialized": True}
    legacy.write_text(json.dumps(original), encoding="utf-8")
    before = legacy.read_bytes()
    state = StateManager(new_dir, [old_dir])
    state.load([watcher_config(name="Watcher Name")])
    assert state.watcher("w")["known_keys"] == ["a", "b"]
    assert legacy.read_bytes() == before


def test_migration_unions_r3_v312_and_v2_keys(tmp_path):
    new_dir = tmp_path / "new"
    r3 = tmp_path / "r3"
    v2 = tmp_path / "v2"
    r3.mkdir(); v2.mkdir()
    (r3 / "state.json").write_text(json.dumps({
        "schema": 3,
        "watchers": {"w": {"known_keys": ["shared", "r3"], "health": {}}},
        "meta": {"last_heartbeat_date": None},
    }), encoding="utf-8")
    (r3 / "v3_w.json").write_text(json.dumps({"known_keys": ["shared", "v312"]}), encoding="utf-8")
    (v2 / "Watcher_Name.json").write_text(json.dumps({"keys": ["v2"]}), encoding="utf-8")
    state = StateManager(new_dir, [r3, v2])
    state.load([watcher_config(name="Watcher Name")])
    assert state.watcher("w")["known_keys"] == ["r3", "shared", "v2", "v312"]


def test_existing_v4_state_wins_and_does_not_remigrate_legacy(tmp_path):
    new_dir = tmp_path / "new"
    old_dir = tmp_path / "old"
    new_dir.mkdir(); old_dir.mkdir()
    (new_dir / "state.json").write_text(json.dumps(v4_state(keys=["v4"])), encoding="utf-8")
    (old_dir / "Watcher_Name.json").write_text(json.dumps({"keys": ["legacy"]}), encoding="utf-8")
    state = StateManager(new_dir, [old_dir])
    state.load([watcher_config(name="Watcher Name")])
    assert state.watcher("w")["known_keys"] == ["v4"]
