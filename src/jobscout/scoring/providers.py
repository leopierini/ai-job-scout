"""AI providers behind one small interface, plus the factory and a connection test."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Protocol

import anthropic
import openai

from jobscout.errors import ConfigError, ProviderError
from jobscout.settings import ScoringSettings, Secrets

MAX_TOKENS = 8000
REQUEST_TIMEOUT_S = 900  # Claude Code: one local CLI call per batch
SDK_TIMEOUT_S = 300  # API calls; the scorer has its own retry, so the SDKs never retry
FAKE_SCORE_MIN, FAKE_SCORE_MAX = 40, 95

# Verified against each provider's OpenAI-compatibility docs.
BASE_URLS: dict[str, str | None] = {
    "openai": None,
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

_DISPLAY_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Gemini", "openrouter": "OpenRouter"}
_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
}


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


def _sdk_error(name: str, exc: Exception) -> ProviderError:
    """A short plain message. Never uses the SDK's text: it can echo part of an API key."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return ProviderError(f"{name} rejected the API key. Check the key and try again.")
    if status == 429:
        return ProviderError(f"{name} rate limit or quota reached. Wait a bit or check your plan.")
    if "Timeout" in type(exc).__name__:
        return ProviderError(f"{name} took too long to answer.")
    if isinstance(status, int):
        return ProviderError(f"{name} returned an error (status {status}).")
    return ProviderError(f"Could not reach {name}. Check your connection.")


class AnthropicProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=SDK_TIMEOUT_S, max_retries=0)

    def complete(self, prompt: str) -> str:
        try:
            msg = self._client.messages.create(
                model=self._model, max_tokens=MAX_TOKENS, messages=[{"role": "user", "content": prompt}]
            )
        except anthropic.APIError as exc:
            raise _sdk_error("Anthropic", exc) from None
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


class OpenAICompatProvider:
    """OpenAI, Gemini, OpenRouter and Ollama all speak the OpenAI chat API."""

    def __init__(self, api_key: str, model: str, base_url: str | None) -> None:
        self._model = model
        kwargs: dict = {"api_key": api_key, "timeout": SDK_TIMEOUT_S, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def complete(self, prompt: str) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self._model, messages=[{"role": "user", "content": prompt}]
            )
        except openai.APIError as exc:
            raise _sdk_error("The AI provider", exc) from None
        return (resp.choices[0].message.content or "") if resp.choices else ""


class ClaudeCodeProvider:
    """Uses the user's installed Claude Code CLI (and its login) in print mode."""

    def __init__(self, model: str) -> None:
        self._model = model

    def complete(self, prompt: str) -> str:
        # Claude Code refuses to start when it thinks it is already inside Claude Code.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        # Job texts come from the web, so the model gets no tools, no MCP servers and no files to look at.
        cmd = [
            "claude", "-p", "--model", self._model, "--output-format", "text",
            "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--no-session-persistence",
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="jobscout-claude-") as empty_dir:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True, timeout=REQUEST_TIMEOUT_S, env=env,
                    cwd=empty_dir, encoding="utf-8", errors="replace",  # never the Windows code page
                )
        except FileNotFoundError:
            raise ProviderError("Claude Code isn't installed or isn't on your PATH.") from None
        except subprocess.TimeoutExpired:
            raise ProviderError("Claude Code took too long to answer.") from None
        except OSError:
            raise ProviderError("Claude Code could not be started.") from None
        if proc.returncode != 0:
            lines = (proc.stderr or "").strip().splitlines()
            detail = f": {lines[-1][:200]}" if lines else ""
            raise ProviderError(f"Claude Code failed (exit code {proc.returncode}){detail}")
        return proc.stdout


_FAKE_DEADLINE = re.compile(r"^Deadline: (\d{4}-\d{2}-\d{2})\b", re.MULTILINE)


class FakeProvider:
    """Deterministic offline provider for tests and sample mode.

    `scores` sets a job's score by id; `answers` sets any of its score, reason, flags and start_date (sample mode
    uses this for realistic results). Anything not given gets a stable placeholder.
    """

    def __init__(
        self,
        scores: dict[str, int] | None = None,
        *,
        answers: dict[str, dict] | None = None,
        delay: float = 0.0,
        cancel: threading.Event | None = None,
    ) -> None:
        self._scores = scores or {}
        self._answers = answers or {}
        self._delay = delay  # seconds each batch takes, so sample runs can be watched
        self._cancel = cancel or threading.Event()

    def complete(self, prompt: str) -> str:
        start, end = prompt.find("<jobs>\n"), prompt.rfind("\n</jobs>")
        if start == -1 or end < start:
            return "OK"
        try:
            jobs = json.loads(prompt[start + len("<jobs>\n") : end])
        except ValueError:
            return "OK"
        if self._delay:
            self._cancel.wait(self._delay)  # a pause the Cancel button cuts short
        answers = []
        for job in jobs:
            deadline = _FAKE_DEADLINE.search(job.get("description", ""))
            answers.append(
                {
                    "id": job["id"],
                    "score": self._scores.get(job["id"], self._title_score(job.get("title", ""))),
                    "reason": "Sample score.",
                    "flags": [],
                    "start_date": "",
                    **self._answers.get(job["id"], {}),
                    "deadline": deadline.group(1) if deadline else "",
                }
            )
        return json.dumps(answers)

    @staticmethod
    def _title_score(title: str) -> int:
        digest = hashlib.sha256(title.encode("utf-8")).digest()
        span = FAKE_SCORE_MAX - FAKE_SCORE_MIN + 1
        return FAKE_SCORE_MIN + int.from_bytes(digest[:4], "big") % span


def make_provider(cfg: ScoringSettings, secrets: Secrets) -> Provider:
    name = cfg.provider
    if name == "fake":
        return FakeProvider()
    if name == "claude_code":
        return ClaudeCodeProvider(cfg.model)
    if name == "ollama":
        return OpenAICompatProvider("ollama", cfg.model, BASE_URLS["ollama"])
    key = getattr(secrets, _KEY_FIELDS[name])
    if not key:
        raise ConfigError(f"Add your {_DISPLAY_NAMES[name]} API key first.")
    if name == "anthropic":
        return AnthropicProvider(key, cfg.model)
    return OpenAICompatProvider(key, cfg.model, BASE_URLS[name])


def test_provider(p: Provider) -> tuple[bool, str]:
    """Send a tiny prompt. Returns (ok, message) and never raises."""
    try:
        reply = p.complete("Reply with the single word OK.")
    except ProviderError as exc:
        return False, str(exc)
    except Exception:  # noqa: BLE001 - a connection test must never raise
        return False, "The connection test failed unexpectedly."
    if re.search(r"\bok\b", reply, re.IGNORECASE):
        return True, "Connected."
    return False, "The AI answered, but not as expected. Check the model name."


# pytest would otherwise collect this helper when it is imported into a test module.
test_provider.__test__ = False  # type: ignore[attr-defined]
