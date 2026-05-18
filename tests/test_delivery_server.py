"""Tests for the local kiosk delivery server (delivery/server.py).

All offline — the server is started in a background thread on a real loopback
socket and reached over plain HTTP. Covers:

* ``GET /status/<id>`` returns the documented shape and the right stage given
  a ``status.json``.
* a pending / missing session degrades to stage ``pending`` — never a 500.
* ``GET /<id>/portrait.png`` and ``/<id>/qr.png`` serve the bytes.
* ``GET /<id>/`` serves the mobile portrait page.
* an unsafe / unknown route does not crash the server.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from delivery.server import build_server, read_live_state, read_status

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """A tmp sessions root, exported as SESSIONS_DIR (what the server reads)."""
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(root))
    return root


@pytest.fixture
def server(sessions_dir):
    """A running delivery server on an OS-assigned loopback port.

    Yields the ``http://127.0.0.1:<port>`` base URL. Bound to 127.0.0.1 (not
    0.0.0.0) only so the test never prompts a firewall dialog — the routing
    code under test is identical.
    """
    httpd = build_server(port=0, host="127.0.0.1")
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _write_status(folder, stage, error=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(
        json.dumps({"session_id": folder.name, "stage": stage, "error": error}),
        encoding="utf-8",
    )


def _get(url):
    """GET ``url``; return ``(status_code, body_bytes, content_type)``."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _request(url, method="GET"):
    """Make ``method`` request to ``url``; return ``(status, body, headers)``.

    ``headers`` is the response's ``http.client.HTTPMessage`` — used to assert
    on CORS headers the lighter ``_get`` helper discards.
    """
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _write_live_state(folder, **overrides):
    """Write a live_state.json into ``folder`` with sensible defaults."""
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": folder.name,
        "phase": "base_questions",
        "base_questions_completed": 2,
        "base_questions_total": 5,
        "signals": {"iceberg": 0.7, "two_buttons": 0.1, "compass": 0.0, "arc": 0.0},
        "leading_template": "iceberg",
        "chosen_template": None,
        "routing_done": False,
        "turn_count": 4,
        "updated_at": "2026-05-18T12:00:00+00:00",
    }
    payload.update(overrides)
    (folder / "live_state.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# read_status — the pure function
# ---------------------------------------------------------------------------


def test_read_status_reports_the_stage(sessions_dir) -> None:
    """read_status surfaces the stage written into status.json."""
    _write_status(sessions_dir / "sess-1", "rendering")
    status = read_status("sess-1", sessions_dir=sessions_dir)
    assert status["session_id"] == "sess-1"
    assert status["stage"] == "rendering"
    assert status["error"] is None


def test_read_status_pending_when_missing(sessions_dir) -> None:
    """A session with no folder/status.json degrades to 'pending', not error."""
    status = read_status("never-seen", sessions_dir=sessions_dir)
    assert status["stage"] == "pending"
    assert status["portrait_url"] is None
    assert status["qr_url"] is None
    assert status["error"] is None


def test_read_status_fills_urls_when_done(sessions_dir) -> None:
    """Once portrait/qr exist, read_status reports their relative URLs."""
    folder = sessions_dir / "sess-done"
    _write_status(folder, "done")
    (folder / "portrait.png").write_bytes(b"\x89PNG fake")
    (folder / "qr.png").write_bytes(b"\x89PNG fake-qr")
    status = read_status("sess-done", sessions_dir=sessions_dir)
    assert status["stage"] == "done"
    assert status["portrait_url"] == "/sess-done/portrait.png"
    assert status["qr_url"] == "/sess-done/qr.png"


def test_read_status_corrupt_json_degrades(sessions_dir) -> None:
    """A corrupt status.json yields stage 'error' with a message — not a raise."""
    folder = sessions_dir / "sess-bad"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text("{not json", encoding="utf-8")
    status = read_status("sess-bad", sessions_dir=sessions_dir)
    assert status["stage"] == "error"
    assert status["error"]


# ---------------------------------------------------------------------------
# GET /status/<id> over HTTP
# ---------------------------------------------------------------------------


def test_status_endpoint_shape_and_stage(server, sessions_dir) -> None:
    """GET /status/<id> returns the documented JSON shape and stage."""
    _write_status(sessions_dir / "sess-http", "filling")
    code, body, ctype = _get(f"{server}/status/sess-http")
    assert code == HTTPStatus.OK
    assert "application/json" in ctype
    payload = json.loads(body)
    assert set(payload) == {
        "session_id", "stage", "portrait_url", "qr_url", "error"
    }
    assert payload["session_id"] == "sess-http"
    assert payload["stage"] == "filling"


def test_status_endpoint_pending_no_500(server, sessions_dir) -> None:
    """A missing session returns 200 stage 'pending' — never a 500."""
    code, body, _ = _get(f"{server}/status/ghost-session")
    assert code == HTTPStatus.OK
    payload = json.loads(body)
    assert payload["stage"] == "pending"
    assert payload["error"] is None


def test_status_endpoint_reports_done_with_urls(server, sessions_dir) -> None:
    """A finished session reports stage 'done' and the portrait/qr URLs."""
    folder = sessions_dir / "sess-final"
    _write_status(folder, "done")
    (folder / "portrait.png").write_bytes(b"\x89PNG portrait")
    (folder / "qr.png").write_bytes(b"\x89PNG qr")
    code, body, _ = _get(f"{server}/status/sess-final")
    assert code == HTTPStatus.OK
    payload = json.loads(body)
    assert payload["stage"] == "done"
    assert payload["portrait_url"] == "/sess-final/portrait.png"
    assert payload["qr_url"] == "/sess-final/qr.png"


def test_status_endpoint_error_stage(server, sessions_dir) -> None:
    """An error status.json surfaces stage 'error' and the message."""
    _write_status(sessions_dir / "sess-err", "error", error="render: boom")
    code, body, _ = _get(f"{server}/status/sess-err")
    assert code == HTTPStatus.OK
    payload = json.loads(body)
    assert payload["stage"] == "error"
    assert payload["error"] == "render: boom"


# ---------------------------------------------------------------------------
# Serving the per-session files
# ---------------------------------------------------------------------------


def test_serves_portrait_png(server, sessions_dir) -> None:
    """GET /<id>/portrait.png returns the image bytes as image/png."""
    folder = sessions_dir / "sess-pic"
    folder.mkdir(parents=True)
    payload = b"\x89PNG\r\n the portrait bytes"
    (folder / "portrait.png").write_bytes(payload)
    code, body, ctype = _get(f"{server}/sess-pic/portrait.png")
    assert code == HTTPStatus.OK
    assert ctype == "image/png"
    assert body == payload


def test_serves_qr_png(server, sessions_dir) -> None:
    """GET /<id>/qr.png returns the QR image bytes."""
    folder = sessions_dir / "sess-qr"
    folder.mkdir(parents=True)
    payload = b"\x89PNG\r\n the qr bytes"
    (folder / "qr.png").write_bytes(payload)
    code, body, ctype = _get(f"{server}/sess-qr/qr.png")
    assert code == HTTPStatus.OK
    assert ctype == "image/png"
    assert body == payload


def test_missing_portrait_file_404s(server, sessions_dir) -> None:
    """A request for a portrait that does not exist yet 404s, no crash."""
    (sessions_dir / "sess-empty").mkdir(parents=True)
    code, _, _ = _get(f"{server}/sess-empty/portrait.png")
    assert code == HTTPStatus.NOT_FOUND


def test_portrait_page_served_when_ready(server, sessions_dir) -> None:
    """GET /<id>/ serves the mobile portrait HTML page once a portrait exists."""
    folder = sessions_dir / "sess-page"
    folder.mkdir(parents=True)
    (folder / "portrait.png").write_bytes(b"\x89PNG fake")
    code, body, ctype = _get(f"{server}/sess-page/")
    assert code == HTTPStatus.OK
    assert "text/html" in ctype
    text = body.decode("utf-8")
    assert "<img" in text and 'src="portrait.png"' in text
    assert "download" in text


def test_portrait_page_404_before_ready(server, sessions_dir) -> None:
    """GET /<id>/ before the portrait exists 404s gracefully."""
    (sessions_dir / "sess-waiting").mkdir(parents=True)
    code, _, _ = _get(f"{server}/sess-waiting/")
    assert code == HTTPStatus.NOT_FOUND


def test_unsafe_session_id_rejected(server, sessions_dir) -> None:
    """A path-traversal-ish session id is rejected, not served."""
    code, body, _ = _get(f"{server}/status/..%2F..%2Fetc")
    # urllib normalises some of this; either a 4xx or a pending error shape,
    # but never a 5xx and never a served file.
    assert code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND, HTTPStatus.OK)
    if code == HTTPStatus.OK:
        assert json.loads(body)["stage"] in ("pending", "error")


# ---------------------------------------------------------------------------
# read_live_state — the pure function
# ---------------------------------------------------------------------------

_LIVE_KEYS = {
    "session_id", "phase", "base_questions_completed", "base_questions_total",
    "signals", "leading_template", "chosen_template", "routing_done",
    "turn_count", "updated_at",
}


def test_read_live_state_reports_the_shape(sessions_dir) -> None:
    """read_live_state surfaces the live_state.json contents."""
    _write_live_state(sessions_dir / "sess-l1")
    live = read_live_state("sess-l1", sessions_dir=sessions_dir)
    assert set(live) == _LIVE_KEYS
    assert live["session_id"] == "sess-l1"
    assert live["phase"] == "base_questions"
    assert live["signals"]["iceberg"] == 0.7
    assert live["leading_template"] == "iceberg"
    assert live["turn_count"] == 4


def test_read_live_state_pending_when_missing(sessions_dir) -> None:
    """A session with no live_state.json degrades to phase 'pending'."""
    live = read_live_state("never-interviewed", sessions_dir=sessions_dir)
    assert live["phase"] == "pending"
    assert live["signals"] == {
        "iceberg": 0.0, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0
    }
    assert live["routing_done"] is False
    assert live["chosen_template"] is None
    assert live["updated_at"] is None


def test_read_live_state_corrupt_json_degrades(sessions_dir) -> None:
    """A corrupt live_state.json degrades to the pending default — no raise."""
    folder = sessions_dir / "sess-lbad"
    folder.mkdir(parents=True)
    (folder / "live_state.json").write_text("{not json", encoding="utf-8")
    live = read_live_state("sess-lbad", sessions_dir=sessions_dir)
    assert live["phase"] == "pending"
    assert live["signals"]["iceberg"] == 0.0


def test_read_live_state_unknown_phase_degrades(sessions_dir) -> None:
    """An unrecognised phase value falls back to 'pending', not passed through."""
    _write_live_state(sessions_dir / "sess-lphase", phase="nonsense")
    live = read_live_state("sess-lphase", sessions_dir=sessions_dir)
    assert live["phase"] == "pending"


# ---------------------------------------------------------------------------
# GET /live/<id> over HTTP
# ---------------------------------------------------------------------------


def test_live_endpoint_shape_and_phase(server, sessions_dir) -> None:
    """GET /live/<id> returns the documented JSON shape and phase."""
    _write_live_state(sessions_dir / "sess-lhttp", phase="probing")
    code, body, ctype = _get(f"{server}/live/sess-lhttp")
    assert code == HTTPStatus.OK
    assert "application/json" in ctype
    payload = json.loads(body)
    assert set(payload) == _LIVE_KEYS
    assert payload["session_id"] == "sess-lhttp"
    assert payload["phase"] == "probing"
    assert payload["signals"]["iceberg"] == 0.7


def test_live_endpoint_pending_no_500(server, sessions_dir) -> None:
    """A session with no live_state.json returns 200 phase 'pending' — no 500."""
    code, body, _ = _get(f"{server}/live/ghost-interview")
    assert code == HTTPStatus.OK
    payload = json.loads(body)
    assert payload["phase"] == "pending"
    assert payload["signals"] == {
        "iceberg": 0.0, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0
    }
    assert payload["routing_done"] is False


def test_live_endpoint_complete_phase(server, sessions_dir) -> None:
    """A routed interview reports phase 'complete' and the chosen template."""
    _write_live_state(
        sessions_dir / "sess-lcomplete",
        phase="complete",
        routing_done=True,
        chosen_template="compass",
    )
    code, body, _ = _get(f"{server}/live/sess-lcomplete")
    assert code == HTTPStatus.OK
    payload = json.loads(body)
    assert payload["phase"] == "complete"
    assert payload["routing_done"] is True
    assert payload["chosen_template"] == "compass"


def test_live_endpoint_unsafe_id_no_500(server, sessions_dir) -> None:
    """An unsafe session id on /live still gets the pending shape, never 5xx."""
    code, body, _ = _get(f"{server}/live/..%2F..%2Fetc")
    assert code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND, HTTPStatus.OK)
    if code in (HTTPStatus.OK, HTTPStatus.BAD_REQUEST):
        assert json.loads(body)["phase"] == "pending"


# ---------------------------------------------------------------------------
# CORS — the kiosk fetches /status and /live cross-origin
# ---------------------------------------------------------------------------


def test_cors_header_on_status(server, sessions_dir) -> None:
    """GET /status/<id> sends a permissive Access-Control-Allow-Origin header."""
    _write_status(sessions_dir / "sess-cors-st", "filling")
    code, _, headers = _request(f"{server}/status/sess-cors-st")
    assert code == HTTPStatus.OK
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_cors_header_on_live(server, sessions_dir) -> None:
    """GET /live/<id> sends a permissive Access-Control-Allow-Origin header."""
    _write_live_state(sessions_dir / "sess-cors-live")
    code, _, headers = _request(f"{server}/live/sess-cors-live")
    assert code == HTTPStatus.OK
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_cors_header_on_pending_responses(server, sessions_dir) -> None:
    """The graceful 'pending' responses also carry CORS headers."""
    _, _, status_headers = _request(f"{server}/status/ghost")
    _, _, live_headers = _request(f"{server}/live/ghost")
    assert status_headers.get("Access-Control-Allow-Origin") == "*"
    assert live_headers.get("Access-Control-Allow-Origin") == "*"


def test_options_preflight_answered(server, sessions_dir) -> None:
    """An OPTIONS preflight to /live is answered with CORS headers, no body."""
    code, body, headers = _request(f"{server}/live/sess-preflight", method="OPTIONS")
    assert code == HTTPStatus.NO_CONTENT
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert "GET" in (headers.get("Access-Control-Allow-Methods") or "")
    assert body == b""
