#!/usr/bin/env python3
"""Check a source tree before it is published.

Usage: check_public_tree.py [root] [--markers FILE]

Reports, one per line as `path:line: reason`, every
- line that looks like an API key or token,
- file or folder named like a copy made by a sync tool (`name 2.ext`),
- line holding a word from the optional markers file.

The markers file lists one word per line (case-insensitive; blank lines and lines starting with `#`
are ignored). A missing markers file is skipped. Exit code 0 means clean, 1 means findings.

Inside a git work tree only tracked files are read; otherwise the folder is walked, skipping
.git, node_modules and .venv. Token patterns apply to every file; marker words skip generated
frontend assets and the npm lockfile.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TOKEN_PATTERNS = {
    "apify token": re.compile(r"apify_api_[A-Za-z0-9]{20,}"),
    "notion token": re.compile(r"ntn_[A-Za-z0-9]{20,}"),
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),
    "api key": re.compile(r"sk-[A-Za-z0-9]{32,}"),
    "google key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
}
DUPLICATE_NAME = re.compile(r"^.+ 2(\..*)?$")
WORD_CHECK_SKIPS = ("src/jobscout/server/static/assets/", "frontend/package-lock.json")
WALK_SKIPS = {".git", "node_modules", ".venv"}


def list_files(root: Path) -> list[str]:
    """Relative POSIX paths of the files to check."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        out = None
    if out is not None:
        names = [n.decode("utf-8", "surrogateescape") for n in out.split(b"\0") if n]
        return sorted({n for n in names if (root / n).is_file()})
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if WALK_SKIPS.intersection(rel.parts):
            continue
        if path.is_file():
            found.append(rel.as_posix())
    return sorted(found)


def read_markers(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line.lower())
    return words


def check(root: Path, markers: list[str]) -> list[str]:
    findings = []
    for rel in list_files(root):
        if any(DUPLICATE_NAME.match(part) for part in rel.split("/")):
            findings.append(f"{rel}:1: file name looks like a sync-tool copy")
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        try:
            text = data.decode("utf-8")
            is_text = b"\0" not in data
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            is_text = False
        check_words = bool(markers) and is_text and not rel.startswith(WORD_CHECK_SKIPS)
        for number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in TOKEN_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{rel}:{number}: looks like a {label}")
            if check_words:
                lowered = line.lower()
                for word in markers:
                    if word in lowered:
                        findings.append(f"{rel}:{number}: contains a listed word")
                        break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a source tree before it is published.")
    parser.add_argument("root", nargs="?", default=".", help="folder to check (default: current folder)")
    parser.add_argument("--markers", type=Path, help="file with one word per line to flag")
    args = parser.parse_args(argv)
    findings = check(Path(args.root).resolve(), read_markers(args.markers))
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
