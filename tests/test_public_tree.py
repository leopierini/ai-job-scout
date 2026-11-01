"""Tests for scripts/check_public_tree.py, the check that keeps secrets and stray files out of the repo."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_public_tree.py"

# Built at runtime so this file holds no literal token.
FAKE_TOKEN = "apify_" + "api_" + "x" * 24
MARKER_WORD = "ex" + "ample-private-word"


def run_check(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), *extra],
        capture_output=True,
        text=True,
    )


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(root: Path, files: dict[str, str]) -> None:
    git(root, "init", "-q")
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A", "-f")


def write_markers(root: Path, body: str) -> Path:
    markers = root.parent / (root.name + "-markers.txt")
    markers.write_text(body, encoding="utf-8")
    return markers


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_lists_token_marker_word_and_duplicate_file(root: Path):
    make_repo(
        root,
        {
            "app.py": f"print('hi')\nkey = '{FAKE_TOKEN}'\n",
            "notes.txt": f"fine\nfine\nsee {MARKER_WORD.upper()} here\n",
            "x 2.py": "print('copy')\n",
        },
    )
    markers = write_markers(root, f"# private words\n\n{MARKER_WORD}\n")
    result = run_check(root, "--markers", str(markers))
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert any(line.startswith("app.py:2") for line in lines)
    assert any(line.startswith("notes.txt:3") for line in lines)
    assert any(line.startswith("x 2.py:") for line in lines)
    assert FAKE_TOKEN not in result.stdout


def test_clean_tree_passes(root: Path):
    make_repo(root, {"app.py": "print('hi')\n", "docs/readme.txt": "hello\n"})
    result = run_check(root)
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == ""


def test_walks_a_directory_that_is_not_a_git_repo(root: Path):
    write(root, "app.py", f"key = '{FAKE_TOKEN}'\n")
    write(root, "node_modules/pkg/index.js", f"const k = '{FAKE_TOKEN}'\n")
    write(root, ".venv/lib/mod.py", f"k = '{FAKE_TOKEN}'\n")
    write(root, "sub/y 2.txt", "copy\n")
    result = run_check(root)
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert any(line.startswith("app.py:1") for line in lines)
    assert any(line.startswith("sub/y 2.txt") for line in lines)
    assert not any("node_modules" in line or ".venv" in line for line in lines)


def test_missing_markers_file_is_ignored(root: Path):
    make_repo(root, {"app.py": f"# {MARKER_WORD}\n"})
    result = run_check(root, "--markers", str(root / "no-such-file"))
    assert result.returncode == 0, result.stdout


def test_marker_words_are_not_checked_without_a_markers_file(root: Path):
    make_repo(root, {"app.py": f"# {MARKER_WORD}\n"})
    assert run_check(root).returncode == 0


def test_generated_assets_and_lockfile_skip_word_checks_but_not_tokens(root: Path):
    word_line = f"{MARKER_WORD}\n"
    make_repo(
        root,
        {
            "src/jobscout/server/static/assets/app.js": word_line,
            "frontend/package-lock.json": word_line,
        },
    )
    markers = write_markers(root, MARKER_WORD + "\n")
    assert run_check(root, "--markers", str(markers)).returncode == 0

    write(root, "src/jobscout/server/static/assets/app.js", f"var k='{FAKE_TOKEN}'\n")
    git(root, "add", "-A")
    result = run_check(root, "--markers", str(markers))
    assert result.returncode == 1
    assert "src/jobscout/server/static/assets/app.js:1" in result.stdout


def test_binary_files_are_skipped_for_word_checks(root: Path):
    make_repo(root, {"app.py": "print('hi')\n"})
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00" + MARKER_WORD.encode() + b"\xff")
    git(root, "add", "-A")
    markers = write_markers(root, MARKER_WORD + "\n")
    assert run_check(root, "--markers", str(markers)).returncode == 0


@pytest.mark.parametrize(
    "token",
    [
        "ntn_" + "A" * 24,
        "sk-ant-" + "a1-" * 8,
        "sk-" + "B" * 40,
        "AIza" + "c" * 35,
    ],
)
def test_each_token_pattern_is_caught(root: Path, token: str):
    make_repo(root, {"conf.txt": f"value={token}\n"})
    result = run_check(root)
    assert result.returncode == 1
    assert "conf.txt:1" in result.stdout


def test_short_lookalikes_are_not_tokens(root: Path):
    make_repo(root, {"conf.txt": "sk-short\nntn_abc\napify_api_x\n"})
    assert run_check(root).returncode == 0
