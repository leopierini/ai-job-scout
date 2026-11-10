"""Tolerant parser for the model's scoring response."""

import json
import re
from datetime import date

from jobscout.models import ScoreResult

MAX_FLAGS = 5
MAX_FLAG_CHARS = 60

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_INT_STRING = re.compile(r"[+-]?\d{1,6}")


def _score(value: object) -> int | None:
    """An integer score clamped to 0..100, or None when the value is not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, str) and _INT_STRING.fullmatch(value.strip()):
        value = int(value.strip())
    if not isinstance(value, int):
        return None
    return max(0, min(100, value))


def _iso_date(value: object) -> str:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def _shorten(flag: str) -> str:
    """Cut a flag that is too long at the last whole word, ending in an ellipsis."""
    if len(flag) <= MAX_FLAG_CHARS:
        return flag
    cut = flag[: MAX_FLAG_CHARS - 1]
    head, space, _ = cut.rpartition(" ")
    return (head if space and head else cut).rstrip(" ,;:.-") + "\u2026"


def _flags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_shorten(f.strip()) for f in value if isinstance(f, str) and f.strip()]
    return cleaned[:MAX_FLAGS]


def _result(item: object) -> ScoreResult | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    score = _score(item.get("score"))
    if score is None:
        return None
    reason = item.get("reason")
    return ScoreResult(
        pk=item["id"],
        score=score,
        reason=reason.strip() if isinstance(reason, str) else "",
        flags=_flags(item.get("flags")),
        start_date=_iso_date(item.get("start_date")),
        deadline=_iso_date(item.get("deadline")),
    )


def _find_items(text: str) -> list | None:
    """The response's JSON array of objects, tolerating prose and stray brackets around it."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        outer = json.loads(text[start : end + 1])
    except ValueError:
        outer = None
    if _is_item_list(outer):
        return outer
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "[":
            continue
        try:
            candidate, _ = decoder.raw_decode(text, i)
        except ValueError:
            continue
        if _is_item_list(candidate):
            return candidate
    return None


def _is_item_list(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(v, dict) for v in value)


def parse_scores(text: str, expected: set[str]) -> tuple[list[ScoreResult], set[str]]:
    """Return the valid results for the expected ids, and the expected ids that got none."""
    items = _find_items(_FENCE.sub("", text))
    if items is None:
        return [], set(expected)
    results: dict[str, ScoreResult] = {}
    for item in items:
        result = _result(item)
        if result is not None and result.pk in expected and result.pk not in results:
            results[result.pk] = result
    return list(results.values()), set(expected) - results.keys()
