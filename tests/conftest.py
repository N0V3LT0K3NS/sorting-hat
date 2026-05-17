"""Shared pytest configuration.

``livekit.agents.AgentTask.__init__`` allocates an ``asyncio.Future``, which
requires a current event loop on the thread. Synchronous (non-async) tests
that construct a probe task therefore need a loop installed — and once an
earlier async test has run and closed its loop, ``asyncio.get_event_loop()``
raises ``RuntimeError`` for the synchronous tests that follow.

The autouse fixture below guarantees every test runs with a usable current
event loop, so probe tasks construct cleanly regardless of test order.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Install a fresh current event loop for the duration of each test."""
    try:
        existing = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        existing = None

    if existing is None or existing.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        created = True
    else:
        loop = existing
        created = False

    yield

    if created:
        loop.close()
        asyncio.set_event_loop(None)
