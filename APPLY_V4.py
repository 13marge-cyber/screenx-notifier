#!/usr/bin/env python3
"""Safely overlay screenx-notifier v4 onto an existing cloned repository.

Legacy state directories are deliberately preserved. Use --dry-run first.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYNC_DIRS = ("src", "tests", ".github/workflows")
COPY_FILES = (
    "main.py",
    "config.loop.yaml",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    ".gitignore",
    "APPLY_V4.py",
    "MIGRATION_V4.md",
)
REMOVE_PATHS = (
    "deploy",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".DS_Store",
    "config.actions.yaml",
    "config.example.yaml",
    "requirements-vm.txt",
    "setup_watch.py",
)
PRESERVE = (
    "data/actions-state",
    "data/v3-state",
    "data/notifier-state/state.json",
    "data/notifier-state/state.backup.json",
    ".git",
)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def plan(target: Path) -> list[str]:
    actions: list[str] = []
    for rel in SYNC_DIRS:
        actions.append(f"SYNC {rel}/ (기존 디렉터리 교체)")
    for rel in COPY_FILES:
        actions.append(f"COPY {rel}")
    actions.append("COPY data/notifier-state/.gitignore + .gitkeep (기존 state JSON 보존)")
    for rel in REMOVE_PATHS:
        if (target / rel).exists():
            actions.append(f"REMOVE obsolete {rel}")
    for rel in PRESERVE:
        if (target / rel).exists():
            actions.append(f"PRESERVE {rel}")
    return actions


def apply(target: Path) -> None:
    if not target.exists() or not target.is_dir():
        raise SystemExit(f"대상 저장소 폴더가 없습니다: {target}")
    if target.resolve() == ROOT.resolve():
        raise SystemExit("v4 배포 원본 폴더 자체에는 적용할 수 없습니다.")
    if not (target / ".git").exists():
        raise SystemExit("대상에 .git이 없습니다. 먼저 GitHub 저장소를 clone 하세요.")

    for rel in SYNC_DIRS:
        dst = target / rel
        _remove(dst)
        shutil.copytree(ROOT / rel, dst)

    for rel in COPY_FILES:
        src = ROOT / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    state_dir = target / "data/notifier-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in (".gitignore", ".gitkeep"):
        shutil.copy2(ROOT / "data/notifier-state" / name, state_dir / name)

    for rel in REMOVE_PATHS:
        _remove(target / rel)


def main() -> int:
    parser = argparse.ArgumentParser(description="screenx-notifier v4 안전 적용 도구")
    parser.add_argument("target", help="git clone 한 기존 screenx-notifier 폴더")
    parser.add_argument("--dry-run", action="store_true", help="변경 계획만 표시")
    args = parser.parse_args()
    target = Path(args.target).expanduser().resolve()

    print("=== screenx-notifier v4 적용 계획 ===")
    for item in plan(target):
        print(f"- {item}")
    if args.dry_run:
        print("DRY-RUN 완료: 실제 파일은 변경하지 않았습니다.")
        return 0

    apply(target)
    print("v4 파일 적용 완료")
    print("중요: data/actions-state, data/v3-state, 기존 notifier-state JSON은 삭제하지 않았습니다.")
    print("다음 단계: python -m pytest -q && git status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
