from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import APPLY_V4


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "deploy").mkdir()
    (repo / "data" / "actions-state").mkdir(parents=True)
    (repo / "data" / "v3-state").mkdir(parents=True)
    (repo / "data" / "notifier-state").mkdir(parents=True)

    (repo / "src" / "old.py").write_text("OLD\n", encoding="utf-8")
    (repo / "tests" / "old_test.py").write_text("OLD\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "old.yml").write_text("name: old\n", encoding="utf-8")
    (repo / "deploy" / "old.txt").write_text("OLD\n", encoding="utf-8")
    (repo / "setup_watch.py").write_text("OLD\n", encoding="utf-8")

    legacy = repo / "data" / "actions-state" / "용산_SCREENX_스파이더맨.json"
    legacy.write_text(json.dumps({"keys": ["one", "two"]}), encoding="utf-8")
    r3 = repo / "data" / "v3-state" / "state.json"
    r3.write_text(json.dumps({"schema": 3, "watchers": {}, "meta": {}}), encoding="utf-8")
    current = repo / "data" / "notifier-state" / "state.json"
    current.write_text(
        json.dumps({"schema": 4, "watchers": {}, "meta": {"last_heartbeat_date": None, "migration_sources": []}}),
        encoding="utf-8",
    )
    backup = repo / "data" / "notifier-state" / "state.backup.json"
    shutil.copy2(current, backup)
    return repo


def test_plan_reports_preserved_state_and_obsolete_files(tmp_path):
    repo = make_fake_repo(tmp_path)
    plan = APPLY_V4.plan(repo)
    assert "PRESERVE data/actions-state" in plan
    assert "PRESERVE data/v3-state" in plan
    assert "PRESERVE data/notifier-state/state.json" in plan
    assert "REMOVE obsolete deploy" in plan
    assert "REMOVE obsolete setup_watch.py" in plan


def test_apply_preserves_all_state_bytes_and_replaces_old_tree(tmp_path):
    repo = make_fake_repo(tmp_path)
    tracked = [
        repo / "data" / "actions-state" / "용산_SCREENX_스파이더맨.json",
        repo / "data" / "v3-state" / "state.json",
        repo / "data" / "notifier-state" / "state.json",
        repo / "data" / "notifier-state" / "state.backup.json",
    ]
    before = {str(path.relative_to(repo)): sha256(path) for path in tracked}

    APPLY_V4.apply(repo)

    after = {str(path.relative_to(repo)): sha256(path) for path in tracked}
    assert after == before
    assert not (repo / "deploy").exists()
    assert not (repo / "setup_watch.py").exists()
    assert not (repo / "src" / "old.py").exists()
    assert not (repo / "tests" / "old_test.py").exists()
    assert not (repo / ".github" / "workflows" / "old.yml").exists()
    assert (repo / "src" / "monitor.py").exists()
    assert (repo / "tests" / "test_monitor.py").exists()
    assert (repo / ".github" / "workflows" / "watch-loop.yml").exists()


def test_apply_refuses_non_git_directory(tmp_path):
    target = tmp_path / "not-a-repo"
    target.mkdir()
    with pytest.raises(SystemExit, match=".git"):
        APPLY_V4.apply(target)


def test_apply_refuses_source_directory_itself():
    with pytest.raises(SystemExit, match="원본 폴더"):
        APPLY_V4.apply(APPLY_V4.ROOT)
