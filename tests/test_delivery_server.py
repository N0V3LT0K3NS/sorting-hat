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

from delivery.server import build_server, read_status

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
