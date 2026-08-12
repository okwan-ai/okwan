"""Okwan error hierarchy — uniform across every connector."""
from __future__ import annotations


class OkwanError(Exception):
    """Base for all platform errors."""


class CredentialError(OkwanError):
    """Missing or invalid credentials for a connector."""


class RateLimitedError(OkwanError):
    """Upstream returned 429; carries the advised wait."""

    def __init__(self, retry_after: float = 1.0) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class UpstreamError(OkwanError):
    """Upstream returned a non-retryable or exhausted-retry error."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"upstream error {status}: {body[:200]}")
        self.status = status
        self.body = body
