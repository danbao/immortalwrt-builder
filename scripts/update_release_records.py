#!/usr/bin/env python3
"""Record published releases on the latest target branch with bounded retries."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def retry_operation(operation: Callable[[int], None], *, attempts: int) -> None:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            operation(attempt)
            return
        except Exception as exc:
            last_error = exc
            print(f"record update failed on attempt {attempt}/{attempts}: {exc}", file=sys.stderr)
    assert last_error is not None
    raise last_error


def update_records_once(args: argparse.Namespace, attempt: int) -> None:
    run(["git", "fetch", "origin", args.target_ref])
    release_tags = [str(item["release_tag"]) for item in json.loads(args.results.read_text(encoding="utf-8"))["built"]]
    with tempfile.TemporaryDirectory(prefix=f"openwrt-record-{attempt}-") as tmp_s:
        worktree = Path(tmp_s) / "worktree"
        run(["git", "worktree", "add", "--detach", str(worktree), "FETCH_HEAD"])
        try:
            result_target = worktree / "dist" / "build-results.json"
            result_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.results, result_target)
            run(
                [
                    sys.executable,
                    "scripts/openwrt_img_to_ova.py",
                    "record",
                    "--results",
                    "dist/build-results.json",
                    "--manifest",
                    "manifests/converted-images.json",
                    "--doc",
                    "docs/converted-images.md",
                ],
                cwd=worktree,
            )
            verify_command = [
                sys.executable,
                "scripts/openwrt_build_preflight.py",
                "verify-records",
                "--manifest",
                "manifests/converted-images.json",
                "--doc",
                "docs/converted-images.md",
            ]
            for tag in release_tags:
                verify_command.extend(["--release-tag", tag])
            run(verify_command, cwd=worktree)
            run(["git", "add", "manifests/converted-images.json", "docs/converted-images.md"], cwd=worktree)
            if run(["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False).returncode == 0:
                return
            run(["git", "commit", "-m", "docs: record converted OpenWrt images [skip ci]"], cwd=worktree)
            run(["git", "push", "origin", f"HEAD:{args.target_ref}"], cwd=worktree)
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], check=False)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--attempts", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        retry_operation(lambda attempt: update_records_once(args, attempt), attempts=args.attempts)
        return 0
    except Exception as exc:
        print(f"error: release exists, but repository records could not be updated: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
