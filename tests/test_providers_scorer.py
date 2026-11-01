import json
import os
import subprocess
import threading
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from conftest import make_job

from jobscout.errors import ConfigError, ProviderError
from jobscout.scoring import providers
from jobscout.scoring.parse import parse_scores
from jobscout.scoring.prompt import build_prompt
from jobscout.scoring.providers import (
    AnthropicProvider,
    ClaudeCodeProvider,
    FakeProvider,
    OpenAICompatProvider,
    make_provider,
    test_provider as check_provider,
)
from jobscout.scoring.scorer import score_jobs
from jobscout.settings import ScoringSettings, Secrets

TODAY = date(2026, 11, 1)


def _jobs(n):
    return [make_job(job_id=str(i), title=f"Role {i}") for i in range(1, n + 1)]


def _ids(prompt):
    return [j["id"] for j in json.loads(prompt.split("<jobs>\n")[1].split("\n</jobs>")[0])]


def _answer(ids):
    return json.dumps(
        [{"id": i, "score": 80, "reason": "ok", "flags": [], "start_date": "", "deadline": ""} for i in ids]
    )


def _run(jobs, provider, batch_size=10, cancel=None, progress=None):
    return score_jobs(
        jobs, provider, "profile", ScoringSettings(batch_size=batch_size), TODAY,
        progress or (lambda *a: None), cancel or threading.Event(),
    )


# --- make_provider -----------------------------------------------------------

def test_make_provider_missing_key():
    with pytest.raises(ConfigError, match="API key"):
        make_provider(ScoringSettings(provider="openai", model="gpt-x"), Secrets())


def test_make_provider_missing_key_names_provider():
    with pytest.raises(ConfigError, match="Add your OpenAI API key first."):
        make_provider(ScoringSettings(provider="openai", model="gpt-x"), Secrets())
    with pytest.raises(ConfigError, match="Gemini"):
        make_provider(ScoringSettings(provider="gemini", model="g"), Secrets())
    with pytest.raises(ConfigError, match="OpenRouter"):
        make_provider(ScoringSettings(provider="openrouter", model="m"), Secrets())
    with pytest.raises(ConfigError, match="Anthropic"):
        make_provider(ScoringSettings(provider="anthropic", model="m"), Secrets())


def test_make_provider_no_key_needed():
    assert isinstance(make_provider(ScoringSettings(provider="ollama", model="llama3"), Secrets()), OpenAICompatProvider)
    assert isinstance(make_provider(ScoringSettings(provider="claude_code", model="sonnet"), Secrets()), ClaudeCodeProvider)
    assert isinstance(make_provider(ScoringSettings(provider="fake", model="x"), Secrets()), FakeProvider)


def test_make_provider_with_keys():
    s = Secrets(anthropic_api_key="k1", openai_api_key="k2", gemini_api_key="k3", openrouter_api_key="k4")
    assert isinstance(make_provider(ScoringSettings(provider="anthropic", model="m"), s), AnthropicProvider)
    for name in ("openai", "gemini", "openrouter"):
        assert isinstance(make_provider(ScoringSettings(provider=name, model="m"), s), OpenAICompatProvider)


def test_base_urls():
    assert providers.BASE_URLS == {
        "openai": None,
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
    }


# --- SDK providers (mocked clients) ------------------------------------------

def test_anthropic_joins_text_blocks(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="[1,"), SimpleNamespace(type="tool_use"), SimpleNamespace(type="text", text="2]")]
    )
    monkeypatch.setattr(providers.anthropic, "Anthropic", lambda **kw: client)
    assert AnthropicProvider("k", "claude-x").complete("hi") == "[1,2]"
    client.messages.create.assert_called_once_with(
        model="claude-x", max_tokens=8000, messages=[{"role": "user", "content": "hi"}]
    )


def test_anthropic_error_is_wrapped_without_key(monkeypatch):
    client = MagicMock()
    req = httpx.Request("POST", "https://api.anthropic.com")
    client.messages.create.side_effect = anthropic.APIConnectionError(request=req)
    monkeypatch.setattr(providers.anthropic, "Anthropic", lambda **kw: client)
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider("sk-secret-key", "m").complete("hi")
    assert "sk-secret-key" not in str(exc.value) and str(exc.value)


def test_openai_compat_returns_message_content(monkeypatch):
    created = {}
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
    )

    def factory(**kw):
        created.update(kw)
        return client

    monkeypatch.setattr(providers.openai, "OpenAI", factory)
    p = OpenAICompatProvider("ollama", "llama3", providers.BASE_URLS["ollama"])
    assert p.complete("hi") == "answer"
    assert created["base_url"] == "http://localhost:11434/v1" and created["api_key"] == "ollama"
    client.chat.completions.create.assert_called_once_with(
        model="llama3", messages=[{"role": "user", "content": "hi"}]
    )


def test_sdk_clients_never_retry_and_time_out_after_five_minutes(monkeypatch):
    created = []
    monkeypatch.setattr(providers.anthropic, "Anthropic", lambda **kw: created.append(kw))
    monkeypatch.setattr(providers.openai, "OpenAI", lambda **kw: created.append(kw))
    AnthropicProvider("k", "m")
    OpenAICompatProvider("k", "m", None)
    assert [(kw["max_retries"], kw["timeout"]) for kw in created] == [(0, 300), (0, 300)]


def test_openai_compat_empty_content_and_errors(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
    )
    monkeypatch.setattr(providers.openai, "OpenAI", lambda **kw: client)
    assert OpenAICompatProvider("k", "m", None).complete("hi") == ""
    client.chat.completions.create.side_effect = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    with pytest.raises(ProviderError):
        OpenAICompatProvider("sk-secret", "m", None).complete("hi")


# --- Claude Code --------------------------------------------------------------

def test_claude_code_strips_claudecode_env(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("KEEP_ME", "yes")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    assert ClaudeCodeProvider("sonnet").complete("the prompt") == "done"
    assert seen["cmd"] == [
        "claude", "-p", "--model", "sonnet", "--output-format", "text",
        "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
        "--no-session-persistence",
    ]
    kw = seen["kw"]
    assert kw["input"] == "the prompt" and kw["timeout"] == 900 and kw["text"] and kw["capture_output"]
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace"  # not the Windows code page
    assert "CLAUDECODE" not in kw["env"] and kw["env"]["KEEP_ME"] == "yes"


def test_claude_code_runs_in_an_empty_temporary_folder(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cwd"] = kw["cwd"]
        seen["existed"] = os.path.isdir(kw["cwd"]) and os.listdir(kw["cwd"]) == []
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    ClaudeCodeProvider("sonnet").complete("x")
    assert seen["existed"]
    assert os.path.realpath(seen["cwd"]) != os.path.realpath(os.getcwd())
    assert not os.path.exists(seen["cwd"])  # removed afterwards


def test_claude_code_errors(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(providers.subprocess, "run", missing)
    with pytest.raises(ProviderError, match="Claude Code isn't installed or isn't on your PATH."):
        ClaudeCodeProvider("m").complete("x")

    monkeypatch.setattr(
        providers.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
    )
    with pytest.raises(ProviderError, match="exit code 1"):
        ClaudeCodeProvider("m").complete("x")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr(providers.subprocess, "run", slow)
    with pytest.raises(ProviderError, match="too long"):
        ClaudeCodeProvider("m").complete("x")


# --- Fake provider -------------------------------------------------------------

def test_fake_provider_answers_every_id_in_range():
    jobs = _jobs(4)
    prompt = build_prompt("p", ScoringSettings(), jobs, TODAY)
    out = FakeProvider().complete(prompt)
    results, missing = parse_scores(out, {j.pk for j in jobs})
    assert not missing and len(results) == 4
    assert all(40 <= r.score <= 95 and r.reason == "Sample score." for r in results)
    again, _ = parse_scores(FakeProvider().complete(prompt), {j.pk for j in jobs})
    assert [r.score for r in again] == [r.score for r in results]


def test_fake_provider_uses_given_scores():
    jobs = _jobs(2)
    prompt = build_prompt("p", ScoringSettings(), jobs, TODAY)
    results, _ = parse_scores(FakeProvider({"linkedin:1": 99}).complete(prompt), {j.pk for j in jobs})
    assert {r.pk: r.score for r in results}["linkedin:1"] == 99


def test_fake_provider_answers_with_the_given_answers():
    jobs = _jobs(2)
    jobs[0].description = "Great role.\nDeadline: 2026-01-15"
    answer = {"score": 84, "reason": "Close to your last role.", "flags": ["Asks for SQL"], "start_date": "2027-01-01"}
    prompt = build_prompt("p", ScoringSettings(), jobs, TODAY)
    results, _ = parse_scores(FakeProvider(answers={jobs[0].pk: answer}).complete(prompt), {j.pk for j in jobs})
    first, second = sorted(results, key=lambda r: r.pk != jobs[0].pk)
    assert (first.score, first.reason, first.flags, first.start_date) == (84, "Close to your last role.", ["Asks for SQL"], "2027-01-01")
    assert first.deadline == "2026-01-15"  # still read from the posting
    assert second.reason == "Sample score."  # a job with no answer gets the defaults


def test_fake_provider_reads_a_deadline_line():
    jobs = _jobs(2)
    jobs[0].description = "Great role.\nDeadline: 2026-01-15\nApply soon."
    prompt = build_prompt("p", ScoringSettings(), jobs, TODAY)
    results, _ = parse_scores(FakeProvider().complete(prompt), {j.pk for j in jobs})
    assert {r.pk: r.deadline for r in results} == {jobs[0].pk: "2026-01-15", jobs[1].pk: ""}


def test_fake_provider_answers_ok_without_jobs():
    assert FakeProvider().complete("Reply with the single word OK.") == "OK"


# --- test_provider ---------------------------------------------------------------

def test_test_provider_never_raises():
    class Boom:
        def complete(self, prompt):
            raise ProviderError("nope")

    class Weird:
        def complete(self, prompt):
            raise RuntimeError("sk-secret leaked?")

    ok, msg = check_provider(FakeProvider())
    assert ok and msg
    ok, msg = check_provider(Boom())
    assert not ok and "nope" in msg
    ok, msg = check_provider(Weird())
    assert not ok and msg and "sk-secret" not in msg


def test_test_provider_not_ok_when_reply_lacks_ok():
    class Chatty:
        def complete(self, prompt):
            return "I cannot help"

    ok, _ = check_provider(Chatty())
    assert not ok


# --- score_jobs ------------------------------------------------------------------

def test_scores_all_in_batches_with_progress():
    seen_batches, progress = [], []

    class P:
        def complete(self, prompt):
            ids = _ids(prompt)
            seen_batches.append(ids)
            return _answer(ids)

    out = _run(_jobs(5), P(), batch_size=2, progress=lambda d, t: progress.append((d, t)))
    assert len(out.results) == 5 and out.failed == [] and out.errors == []
    assert [len(b) for b in seen_batches] == [2, 2, 1]
    assert progress == [(0, 5), (2, 5), (4, 5), (5, 5)]


def test_missing_jobs_retried_then_left_scraped():
    class Flaky:  # answers only for the first job of each prompt
        calls = 0

        def complete(self, prompt):
            Flaky.calls += 1
            return _answer(_ids(prompt)[:1])

    out = _run(_jobs(3), Flaky(), batch_size=3)
    assert len(out.results) == 2 and len(out.failed) == 1  # 1 from batch, 1 from retry
    assert Flaky.calls == 2
    assert out.failed == ["linkedin:3"]


@pytest.mark.parametrize(
    ("n_jobs", "message"),
    [
        (2, "The AI didn't return a valid score for 1 job."),
        (3, "The AI didn't return a valid score for 2 jobs."),
    ],
)
def test_unscored_jobs_error_counts_them_in_the_right_number(n_jobs, message):
    class AnswersOnlyTheFirst:
        def complete(self, prompt):
            return _answer([i for i in _ids(prompt) if i == "linkedin:1"])

    out = _run(_jobs(n_jobs), AnswersOnlyTheFirst(), batch_size=n_jobs)
    assert out.errors == [message]


def test_retry_recovers_missing_jobs():
    class OnRetry:
        def __init__(self):
            self.n = 0

        def complete(self, prompt):
            self.n += 1
            ids = _ids(prompt)
            return _answer(ids[:1] if self.n == 1 else ids)

    out = _run(_jobs(3), OnRetry(), batch_size=3)
    assert len(out.results) == 3 and out.failed == []


def test_provider_error_marks_batch_failed():
    class P:
        def complete(self, prompt):
            ids = _ids(prompt)
            if "linkedin:1" in ids:
                raise ProviderError("rate limited")
            return _answer(ids)

    progress = []
    out = _run(_jobs(4), P(), batch_size=2, progress=lambda d, t: progress.append((d, t)))
    assert sorted(out.failed) == ["linkedin:1", "linkedin:2"]
    assert [r.pk for r in out.results] == ["linkedin:3", "linkedin:4"]
    assert any("rate limited" in e for e in out.errors)
    assert progress[-1] == (4, 4)


def test_retry_provider_error_marks_missing_failed():
    class P:
        def __init__(self):
            self.n = 0

        def complete(self, prompt):
            self.n += 1
            if self.n == 2:
                raise ProviderError("down")
            return _answer(_ids(prompt)[:1])

    out = _run(_jobs(3), P(), batch_size=3)
    assert len(out.results) == 1 and sorted(out.failed) == ["linkedin:2", "linkedin:3"]
    assert any("down" in e for e in out.errors)


def test_cancel_stops_between_batches():
    cancel = threading.Event()

    class P:
        def complete(self, prompt):
            cancel.set()
            return _answer(_ids(prompt))

    out = _run(_jobs(5), P(), batch_size=2, cancel=cancel)
    assert [r.pk for r in out.results] == ["linkedin:1", "linkedin:2"]
    assert out.failed == ["linkedin:3", "linkedin:4", "linkedin:5"]


def test_empty_jobs():
    out = _run([], FakeProvider())
    assert out.results == [] and out.failed == [] and out.errors == []


def test_results_reach_the_caller_after_each_batch_and_retry():
    saved = []

    class OnRetry:
        def __init__(self):
            self.n = 0

        def complete(self, prompt):
            self.n += 1
            ids = _ids(prompt)
            return _answer(ids[:1] if self.n == 1 else ids)

    score_jobs(
        _jobs(4), OnRetry(), "profile", ScoringSettings(batch_size=3), TODAY, lambda *a: None, threading.Event(),
        on_results=lambda results: saved.append([r.pk for r in results]),
    )  # fmt: skip
    assert saved == [["linkedin:1"], ["linkedin:2", "linkedin:3"], ["linkedin:4"]]


def test_unexpected_provider_exception_fails_only_its_batch():
    class Boom:
        def complete(self, prompt):
            ids = _ids(prompt)
            if "linkedin:3" in ids:
                raise RuntimeError("secret detail sk-123")
            return _answer(ids)

    out = _run(_jobs(4), Boom(), batch_size=2)
    assert [r.pk for r in out.results] == ["linkedin:1", "linkedin:2"]
    assert out.failed == ["linkedin:3", "linkedin:4"]
    assert out.errors == ["The AI request failed unexpectedly."]
