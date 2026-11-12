"""Exceptions shared across the engine."""

from typing import Literal


class ConfigError(Exception):
    """A settings, secrets or profile file is missing or unreadable."""


class SearchError(Exception):
    """A job source failed. `kind` tells the caller how to react: "limit" and "auth" stop every search."""

    def __init__(self, message: str, kind: Literal["limit", "auth", "other"] = "other") -> None:
        super().__init__(message)
        self.kind = kind


class ApifyError(SearchError):
    """An Apify call failed."""


class ProviderError(Exception):
    """An AI provider call failed."""


class RunInProgress(Exception):
    """Another run is already active."""


class WebhookError(Exception):
    """A webhook delivery failed. The message never includes the URL, which can hold a secret."""


class NotionError(Exception):
    """A Notion call failed. The message is short and plain and never includes the token.

    `status` is the HTTP status Notion answered with, or None when there was no answer."""

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
