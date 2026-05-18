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
import socket

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


@pytest.fixture(autouse=True)
def _block_live_ai_provider_http(monkeypatch):
    """Fail fast if a test tries to reach paid live AI provider APIs."""
    blocked_hosts = {
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "openrouter.ai",
    }
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection

    def _normalize_host(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="ignore")
        return str(host).rstrip(".").lower()

    def _assert_not_blocked(host):
        if _normalize_host(host) in blocked_hosts:
            raise RuntimeError(
                f"Blocked live AI provider network call during tests: {host}"
            )

    def guarded_getaddrinfo(host, *args, **kwargs):
        _assert_not_blocked(host)
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        host, _port = address
        _assert_not_blocked(host)
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
