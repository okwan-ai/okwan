"""Shared fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Limits are process-wide; one test's attempts must not spend another's."""
    from okwan_api.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()
