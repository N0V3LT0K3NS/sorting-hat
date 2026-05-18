"""The local kiosk delivery server — the bridge to the kiosk browser.

The offline pipeline (``classify -> fill -> render -> deliver``) runs on the
agent worker and writes ``portrait.png`` / ``qr.png`` / ``status.json`` into
``sessions/<session-id>/``. The kiosk browser has no way to see any of that.
This server closes the gap, KISS — Python stdlib ``http.server`` only, a flat
file server plus a status file the kiosk polls:

* ``GET /<session-id>/portrait.png`` — the rendered portrait image.
* ``GET /<session-id>/qr.png``       — the QR-code image.
* ``GET /<session-id>/``             — a minimal mobile-friendly page showing
  the portrait full-bleed. This is the page the QR points a phone at: scan it,
  land on a clean dark page with your portrait and a download affordance.
* ``GET /status/<session-id>``       — JSON pipeline progress, the endpoint the
  kiosk polls for the stage-by-stage reveal:
  ``{"session_id", "stage", "portrait_url", "qr_url", "error"}``.

It runs on the *kiosk machine* and binds ``0.0.0.0`` so phones on the same
wifi can reach the per-session portrait page the QR encodes.

``status.json`` is written by :func:`agent.session_finalize.write_status` at
each pipeline-stage boundary. A session folder (or status file) that does not
exist yet degrades to stage ``pending`` — never a 500.
"""

from __future__ import annotations

import json
import logging
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sorting-hat.delivery")

# --- Constants --------------------------------------------------------------

#: Default port the server binds. Overridable via ``DELIVERY_SERVER_PORT``.
DEFAULT_PORT = 8808

#: Environment variable naming the sessions root. Mirrors agent.config /
#: pipeline.deliver — a direct env read so this module stays standalone and
#: tests can repoint the root with ``monkeypatch.setenv``.
SESSIONS_DIR_ENV = "SESSIONS_DIR"
DEFAULT_SESSIONS_DIR = "./sessions"

#: Filenames the pipeline writes into each per-session folder.
PORTRAIT_FILENAME = "portrait.png"
QR_FILENAME = "qr.png"
STATUS_FILENAME = "status.json"

#: The pipeline stages a status.json may report. ``pending`` is this server's
#: own degraded value for a session whose status.json does not exist yet.
KNOWN_STAGES = {
    "pending",
    "classifying",
    "filling",
    "rendering",
    "delivering",
    "done",
    "error",
}

#: A session id must be a single safe folder name — no separators, no ``..``.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def sessions_root() -> Path:
    """Return the sessions root from ``SESSIONS_DIR`` (read fresh each call)."""
    raw = (os.environ.get(SESSIONS_DIR_ENV) or "").strip()
    return Path(raw or DEFAULT_SESSIONS_DIR)


def _is_safe_session_id(session_id: str) -> bool:
    """True when ``session_id`` is usable as a single folder name."""
    return (
        bool(session_id)
        and session_id not in (".", "..")
        and _SAFE_SESSION_ID.match(session_id) is not None
    )


def read_status(session_id: str, *, sessions_dir: Optional[Path] = None) -> dict:
    """Return the pipeline-progress dict for ``session_id``.

    Reads ``sessions/<session_id>/status.json`` (written by the pipeline at
    each stage boundary) and returns the kiosk-facing shape::

        {"session_id", "stage", "portrait_url", "qr_url", "error"}

    A missing session folder or missing/corrupt status.json degrades to stage
    ``pending`` — never raises. ``portrait_url`` / ``qr_url`` are filled with
    relative paths once the stage is ``done`` and the files exist on disk.
    """
    root = sessions_dir if sessions_dir is not None else sessions_root()
    folder = Path(root) / session_id
    status_path = folder / STATUS_FILENAME

    stage = "pending"
    error: Optional[str] = None
    if status_path.is_file():
        try:
            raw = json.loads(status_path.read_text(encoding="utf-8"))
            candidate = str(raw.get("stage") or "").strip()
            if candidate in KNOWN_STAGES:
                stage = candidate
            elif candidate:
                # An unrecognised stage value — surface it rather than 500.
                stage = "error"
                error = f"unknown stage {candidate!r} in status.json"
            error = raw.get("error") if error is None else error
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("session %s: status.json unreadable (%s)", session_id, exc)
            stage = "error"
            error = f"status.json unreadable: {exc}"

    portrait_url: Optional[str] = None
    qr_url: Optional[str] = None
    if (folder / PORTRAIT_FILENAME).is_file():
        portrait_url = f"/{session_id}/{PORTRAIT_FILENAME}"
    if (folder / QR_FILENAME).is_file():
        qr_url = f"/{session_id}/{QR_FILENAME}"

    return {
        "session_id": session_id,
        "stage": stage,
        "portrait_url": portrait_url,
        "qr_url": qr_url,
        "error": error,
    }


# --- The per-session portrait page ------------------------------------------

_PORTRAIT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your portrait</title>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; background: #0b0b0d; }}
  body {{
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 1.25rem; padding: 1.25rem;
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  img {{
    max-width: 100%; max-height: 80vh; height: auto;
    border-radius: 10px; box-shadow: 0 8px 40px rgba(0,0,0,0.6);
  }}
  a.download {{
    color: #0b0b0d; background: #f5f5f7; text-decoration: none;
    padding: 0.7rem 1.5rem; border-radius: 999px; font-weight: 600;
    font-size: 1rem;
  }}
  a.download:active {{ opacity: 0.7; }}
</style>
</head>
<body>
  <img src="portrait.png" alt="Your sorting-hat portrait">
  <a class="download" href="portrait.png" download="portrait.png">Download</a>
</body>
</html>
"""


# --- The request handler ----------------------------------------------------


class DeliveryHandler(BaseHTTPRequestHandler):
    """Serves portraits, QR images, the portrait page, and status JSON.

    ``sessions_dir`` is resolved fresh per request via :func:`sessions_root`
    so the env var stays the single source of truth.
    """

    server_version = "sorting-hat-delivery/1.0"

    # Quiet the default noisy stderr logging; route through our logger.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.info("delivery %s - %s", self.address_string(), fmt % args)

    # -- response helpers ----------------------------------------------------

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The kiosk polls this; never let a stale response be cached.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_page(self, status: HTTPStatus, message: str) -> None:
        body = f"<!DOCTYPE html><meta charset=utf-8><h1>{status.value}</h1><p>{message}</p>"
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    # -- routing -------------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        # Strip any query string; we route on the path only.
        path = self.path.split("?", 1)[0]

        # GET /status/<session-id>
        if path.startswith("/status/"):
            session_id = path[len("/status/"):].strip("/")
            self._handle_status(session_id)
            return

        # Everything else is a per-session file route: /<session-id>/...
        parts = [p for p in path.split("/") if p]
        if not parts:
            self._send_error_page(
                HTTPStatus.NOT_FOUND,
                "sorting-hat delivery server. Use /<session-id>/ or "
                "/status/<session-id>.",
            )
            return

        session_id = parts[0]
        remainder = parts[1:]
        if not _is_safe_session_id(session_id):
            self._send_error_page(HTTPStatus.BAD_REQUEST, "invalid session id")
            return

        folder = sessions_root() / session_id

        if not remainder:
            self._handle_portrait_page(session_id, folder)
        elif remainder == [PORTRAIT_FILENAME]:
            self._handle_file(folder / PORTRAIT_FILENAME, "image/png")
        elif remainder == [QR_FILENAME]:
            self._handle_file(folder / QR_FILENAME, "image/png")
        else:
            self._send_error_page(HTTPStatus.NOT_FOUND, "not found")

    # -- handlers ------------------------------------------------------------

    def _handle_status(self, session_id: str) -> None:
        """GET /status/<session-id> — pipeline progress JSON, never 500."""
        if not _is_safe_session_id(session_id):
            # Still answer with the documented shape, not a hard error, so the
            # polling kiosk has a uniform thing to parse.
            self._send_json(
                {
                    "session_id": session_id,
                    "stage": "error",
                    "portrait_url": None,
                    "qr_url": None,
                    "error": "invalid session id",
                },
                HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(read_status(session_id))

    def _handle_portrait_page(self, session_id: str, folder: Path) -> None:
        """GET /<session-id>/ — the mobile portrait page the QR points at."""
        if not (folder / PORTRAIT_FILENAME).is_file():
            self._send_error_page(
                HTTPStatus.NOT_FOUND,
                "Portrait is not ready yet. Hold on a moment and refresh.",
            )
            return
        self._send_bytes(
            _PORTRAIT_PAGE.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _handle_file(self, file_path: Path, content_type: str) -> None:
        """Serve a per-session file, 404 if it does not exist yet."""
        if not file_path.is_file():
            self._send_error_page(HTTPStatus.NOT_FOUND, "file not found")
            return
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            self._send_error_page(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"could not read file: {exc}"
            )
            return
        self._send_bytes(body, content_type)


# --- The server -------------------------------------------------------------


def build_server(
    port: int = DEFAULT_PORT, host: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    """Build (but do not start) the delivery server.

    Binds ``host:port`` — ``0.0.0.0`` by default so phones on the same wifi can
    reach the per-session portrait page. Threading so a slow phone fetch never
    blocks the kiosk's status polling. The caller runs ``serve_forever()`` (or
    use :func:`run`).
    """
    return ThreadingHTTPServer((host, port), DeliveryHandler)


def run(port: int = DEFAULT_PORT, host: str = "0.0.0.0") -> None:
    """Build and run the delivery server until interrupted.

    The programmatic entry point — also what ``python -m delivery.server``
    calls. Blocks in ``serve_forever()``; Ctrl-C shuts it down cleanly.
    """
    server = build_server(port, host)
    logger.info(
        "delivery server on http://%s:%d  (serving %s)",
        host,
        port,
        sessions_root().resolve(),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("delivery server: shutting down")
    finally:
        server.server_close()


def _resolve_port() -> int:
    """Return the port from ``DELIVERY_SERVER_PORT`` env, else the default."""
    raw = (os.environ.get("DELIVERY_SERVER_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "DELIVERY_SERVER_PORT=%r is not an integer — using %d",
            raw,
            DEFAULT_PORT,
        )
        return DEFAULT_PORT


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(port=_resolve_port())
