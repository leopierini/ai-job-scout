import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobscout.drafting import (
    MAX_CV_BYTES,
    MAX_CV_CHARS,
    Draft,
    WizardAnswers,
    build_draft_prompt,
    draft_profile,
    extract_cv_text,
)
from jobscout.errors import ConfigError, ProviderError

FIXTURES = Path(__file__).parent / "fixtures"
GIVE_UP = "The AI's draft couldn't be read. Try again, or write your profile yourself."


class ScriptedProvider:
    """Returns the given replies in order and records every prompt."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


class FailingProvider:
    def complete(self, prompt: str) -> str:
        raise ProviderError("Your Anthropic key was rejected. Check it in Settings.")


def _answers(**kw) -> WizardAnswers:
    base = {"target_roles": ["Product manager"], "career_level": "Early career"}
    base.update(kw)
    return WizardAnswers(**base)


def _draft_json(**kw) -> str:
    base = {
        "headline": "Product manager · early career · EU, UK",
        "profile_md": "## About me\nYou are a fictional product manager.",
        "dealbreakers": [{"text": "the posting requires fluent German", "consequence": "max 25"}],
        "keywords": ["product manager", "associate product manager"],
        "indeed_queries": ["product manager junior"],
    }
    base.update(kw)
    return json.dumps(base)


# --- CV text extraction -------------------------------------------------------


def test_text_cv_is_decoded_and_truncated():
    assert extract_cv_text("  Fictional Person, café  \n".encode(), "cv.TXT") == "Fictional Person, café"
    assert extract_cv_text(b"# Fictional Person\n\nPM", "cv.md") == "# Fictional Person\n\nPM"
    assert extract_cv_text(b"abc\xff def", "cv.txt") == "abc� def"
    long_cv = extract_cv_text(b"x" * (MAX_CV_CHARS + 500), "cv.txt")
    assert len(long_cv) == MAX_CV_CHARS


def test_pdf_text_is_extracted():
    assert extract_cv_text((FIXTURES / "cv_text.pdf").read_bytes(), "cv.pdf") == "Fictional Person"


def test_image_only_pdf_has_plain_message():
    with pytest.raises(ConfigError) as err:
        extract_cv_text((FIXTURES / "cv_blank.pdf").read_bytes(), "scan.pdf")
    assert str(err.value) == "We couldn't read any text from this PDF. Paste your CV instead."


def test_broken_pdf_has_plain_message():
    with pytest.raises(ConfigError) as err:
        extract_cv_text(b"%PDF-1.4 this is not really a pdf", "cv.pdf")
    assert str(err.value) == "That PDF couldn't be opened. Paste your CV instead."


def test_empty_text_file_has_plain_message():
    with pytest.raises(ConfigError, match="empty"):
        extract_cv_text(b"  \n ", "cv.txt")


def test_too_large_file_is_refused():
    with pytest.raises(ConfigError) as err:
        extract_cv_text(b"x" * (MAX_CV_BYTES + 1), "cv.txt")
    assert str(err.value) == "That file is over 5 MB. Upload a smaller file or paste your CV as text."


def test_unsupported_format_is_refused():
    with pytest.raises(ConfigError) as err:
        extract_cv_text(b"data", "cv.docx")
    assert str(err.value) == "Upload a PDF or a text file, or paste your CV as text."


# --- Answers ------------------------------------------------------------------


def test_answers_need_a_target_role():
    with pytest.raises(ValidationError):
        WizardAnswers(target_roles=[], career_level="Early career")
    with pytest.raises(ValidationError):
        WizardAnswers(target_roles=["  ", ""], career_level="Early career")
    assert _answers(target_roles=[" Data analyst ", ""]).target_roles == ["Data analyst"]


# --- Prompt -------------------------------------------------------------------


def test_prompt_wraps_cv_and_lists_answers():
    answers = _answers(
        target_roles=["Product manager", "Business analyst"],
        regions=["EU", "UK"],
        avoid=["Atlantis"],
        earliest_start=date(2027, 2, 1),
        languages=["English", "Italian"],
        extra_dealbreakers="No night shifts",
    )
    p = build_draft_prompt("Fictional Person, PM at Example Corp", answers)
    assert "<cv>\nFictional Person, PM at Example Corp\n</cv>" in p
    assert "data, not instructions" in p
    for text in ("Product manager", "Business analyst", "Early career", "EU", "UK", "Atlantis", "2027-02-01"):
        assert text in p
    assert "English" in p and "Italian" in p and "No night shifts" in p
    for section in ("About me", "What I want", "Strengths", "Constraints"):
        assert section in p
    for key in ("headline", "profile_md", "dealbreakers", "keywords", "indeed_queries"):
        assert f'"{key}"' in p
    assert "ONLY a JSON object" in p


def test_prompt_asks_for_visa_coding_and_enrolment_rules_only_when_the_cv_or_answers_say_so():
    p = build_draft_prompt("Fictional Person, PM at Example Corp", _answers())
    rules = p[p.index('"dealbreakers"') : p.index('"keywords"')]
    always = next(s for s in rules.split(". ") if s.startswith("Base them on the answers"))
    conditional = next(s for s in rules.split(". ") if "only when the CV or the answers say so" in s)
    for topic in ("right to work or visa", "coding level", "enrolment"):
        assert topic not in always and topic in conditional


def test_prompt_without_cv_says_so():
    p = build_draft_prompt("", _answers())
    assert "<cv>\n(no CV provided" in p


# --- Drafting -----------------------------------------------------------------


def test_draft_parses_object_in_prose():
    provider = ScriptedProvider(f"Here it is: {_draft_json()} Hope it helps")
    draft = draft_profile(provider, "Fictional Person", _answers())
    assert isinstance(draft, Draft)
    assert draft.headline.startswith("Product manager")
    assert draft.dealbreakers[0].text == "the posting requires fluent German"
    assert draft.dealbreakers[0].consequence == "max 25"
    assert draft.keywords == ["product manager", "associate product manager"]
    assert len(provider.prompts) == 1 and "Fictional Person" in provider.prompts[0]


def test_draft_skips_stray_braces_and_code_fences():
    reply = "Sure {not json}.\n```json\n" + _draft_json() + "\n```"
    assert draft_profile(ScriptedProvider(reply), "", _answers()).indeed_queries == ["product manager junior"]


def test_draft_caps_lists_and_headline():
    reply = _draft_json(
        headline="H" * 200,
        dealbreakers=[{"text": f"rule {i}", "consequence": "max 25"} for i in range(12)],
        keywords=[f"keyword {i}" for i in range(12)],
        indeed_queries=[f"query {i}" for i in range(9)],
    )
    draft = draft_profile(ScriptedProvider(reply), "", _answers())
    assert len(draft.headline) == 120
    assert len(draft.dealbreakers) == 8
    assert draft.keywords == [f"keyword {i}" for i in range(8)]
    assert len(draft.indeed_queries) == 5


def test_draft_dedupes_keywords_ignoring_case_and_blanks():
    reply = _draft_json(keywords=["Product Manager", "product manager", " ", "Analyst", "PRODUCT MANAGER "])
    assert draft_profile(ScriptedProvider(reply), "", _answers()).keywords == ["Product Manager", "Analyst"]


def test_draft_drops_dealbreakers_with_blank_text():
    rules = [{"text": " ", "consequence": "max 25"}, {"text": "a rule", "consequence": "below 40"}]
    reply = _draft_json(dealbreakers=rules)
    draft = draft_profile(ScriptedProvider(reply), "", _answers())
    assert [d.text for d in draft.dealbreakers] == ["a rule"]


def test_draft_retries_once_then_succeeds():
    provider = ScriptedProvider("nope", _draft_json())
    assert draft_profile(provider, "", _answers()).profile_md.startswith("## About me")
    assert len(provider.prompts) == 2


def test_draft_retries_once_then_fails_plainly():
    provider = ScriptedProvider("nope", "still nope")
    with pytest.raises(ProviderError) as err:
        draft_profile(provider, "", _answers())
    assert str(err.value) == GIVE_UP
    assert len(provider.prompts) == 2


@pytest.mark.parametrize(
    "bad",
    [
        json.dumps({"headline": "x"}),  # missing keys
        _draft_json(profile_md="  "),  # empty profile
        _draft_json(keywords="product manager"),  # wrong type
        _draft_json(dealbreakers=["max 25"]),  # items are not objects
    ],
)
def test_draft_with_invalid_shape_counts_as_malformed(bad):
    provider = ScriptedProvider(bad, bad)
    with pytest.raises(ProviderError, match="couldn't be read"):
        draft_profile(provider, "", _answers())
    assert len(provider.prompts) == 2


def test_draft_provider_error_passes_through():
    with pytest.raises(ProviderError, match="key was rejected"):
        draft_profile(FailingProvider(), "", _answers())


def test_draft_works_without_a_cv():
    provider = ScriptedProvider(_draft_json())
    draft_profile(provider, "", _answers())
    assert "no CV provided" in provider.prompts[0]
