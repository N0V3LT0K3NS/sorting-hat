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
* ``GET /live/<session-id>``         — JSON live interview state, the endpoint
  the kiosk polls *during* an interview for the classifier signal weights and
  the interview's progress (developer view + smart End button).
* ``GET /sessions``                  — JSON index of *every* session folder on
  this machine, one summary per interview; the dev dashboard's session list.
* ``GET /<session-id>/<file>``        — the known per-session JSON artifacts
  (transcript.json, interview_state.json, classification.json, result.json,
  live_state.json, status.json) so a dev detail view can fetch a past
  interview's transcript. Allowlisted filenames only; no path escape.

It runs on the *kiosk machine* and binds ``0.0.0.0`` so phones on the same
wifi can reach the per-session portrait page the QR encodes.

``status.json`` is written by :func:`agent.session_finalize.write_status` at
each pipeline-stage boundary; ``live_state.json`` is written by
:func:`agent.session_finalize.write_live_state` after every interview turn.
A session folder (or either JSON file) that does not exist yet degrades to a
graceful default — ``pending`` — never a 500.

The JSON endpoints (``/status`` and ``/live``) send permissive CORS headers
(``Access-Control-Allow-Origin: *``) and answer an ``OPTIONS`` preflight, so
the kiosk browser app — served from a different origin/port — can fetch them.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
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
#: Live-interview state file, written each turn by agent.session_finalize.
LIVE_STATE_FILENAME = "live_state.json"
#: Transcript + interview-state files, written each turn by persist_transcript.
TRANSCRIPT_FILENAME = "transcript.json"
STATE_FILENAME = "interview_state.json"
#: Offline-pipeline artifacts, written by run_offline_pipeline.
CLASSIFICATION_FILENAME = "classification.json"
RESULT_FILENAME = "result.json"

#: The image artifacts a session folder may hold, mapped to their MIME type.
_IMAGE_ARTIFACTS = {
    PORTRAIT_FILENAME: "image/png",
    QR_FILENAME: "image/png",
}

#: The JSON artifacts a dev detail view may fetch from a session folder. An
#: explicit allowlist — only these names are served, never an arbitrary path,
#: so a ``../`` escape can never reach outside the session folder.
_JSON_ARTIFACTS = frozenset(
    {
        TRANSCRIPT_FILENAME,
        STATE_FILENAME,
        CLASSIFICATION_FILENAME,
        RESULT_FILENAME,
        LIVE_STATE_FILENAME,
        STATUS_FILENAME,
    }
)

#: The interview phases a live_state.json may report. ``pending`` is this
#: server's own degraded value for a session whose live_state.json does not
#: exist yet (the interview has not produced any state).
KNOWN_PHASES = {
    "pending",
    "base_questions",
    "probing",
    "complete",
}

#: The four classifier signals, zeroed — the graceful default ``signals`` block
#: for a session with no live_state.json yet.
_ZERO_SIGNALS = {"iceberg": 0.0, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}

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


def read_live_state(session_id: str, *, sessions_dir: Optional[Path] = None) -> dict:
    """Return the live interview-state dict for ``session_id``.

    Reads ``sessions/<session_id>/live_state.json`` (written by
    :func:`agent.session_finalize.write_live_state` after every interview turn)
    and returns the kiosk-facing shape::

        {"session_id", "phase", "base_questions_completed",
         "base_questions_total", "signals", "leading_template",
         "chosen_template", "routing_done", "turn_count", "updated_at"}

    A missing session folder or missing/corrupt live_state.json degrades to a
    graceful default — phase ``pending``, zeroed signals — and never raises, so
    the polling kiosk always has a uniform shape to parse.
    """
    root = sessions_dir if sessions_dir is not None else sessions_root()
    folder = Path(root) / session_id
    live_path = folder / LIVE_STATE_FILENAME

    default = {
        "session_id": session_id,
        "phase": "pending",
        "base_questions_completed": 0,
        "base_questions_total": 0,
        "signals": dict(_ZERO_SIGNALS),
        "leading_template": None,
        "chosen_template": None,
        "routing_done": False,
        "turn_count": 0,
        "updated_at": None,
    }

    if not live_path.is_file():
        return default

    try:
        raw = json.loads(live_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "session %s: live_state.json unreadable (%s)", session_id, exc
        )
        return default
    if not isinstance(raw, dict):
        logger.warning("session %s: live_state.json is not an object", session_id)
        return default

    # Build the response from the defaults, overlaying recognised fields. The
    # phase is validated against KNOWN_PHASES; an unknown value degrades to the
    # default rather than being passed through.
    result = dict(default)
    for key in (
        "base_questions_completed",
        "base_questions_total",
        "leading_template",
        "chosen_template",
        "routing_done",
        "turn_count",
        "updated_at",
    ):
        if key in raw:
            result[key] = raw[key]
    phase = str(raw.get("phase") or "").strip()
    if phase in KNOWN_PHASES:
        result["phase"] = phase
    signals = raw.get("signals")
    if isinstance(signals, dict):
        result["signals"] = {
            label: signals.get(label, 0.0) for label in _ZERO_SIGNALS
        }
    return result


def _read_json(path: Path) -> Optional[dict]:
    """Return the parsed JSON object at ``path``, or ``None`` if absent/bad.

    A missing file, unreadable file, malformed JSON, or a JSON value that is
    not an object all degrade to ``None`` — a session-index summary must never
    500 on a partial or corrupt folder.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s unreadable (%s)", path, exc)
        return None
    return raw if isinstance(raw, dict) else None


def _summarize_session(folder: Path) -> dict:
    """Build the index summary for one session folder.

    Reads whatever JSON files exist (gracefully — a folder may hold only a
    transcript, or the full pipeline output) and rolls them into a uniform
    summary. Never raises; an empty/partial folder yields sensible defaults.
    """
    session_id = folder.name

    live = _read_json(folder / LIVE_STATE_FILENAME)
    status = _read_json(folder / STATUS_FILENAME)
    state = _read_json(folder / STATE_FILENAME)
    transcript = folder / TRANSCRIPT_FILENAME
    has_transcript = transcript.is_file()
    has_portrait = (folder / PORTRAIT_FILENAME).is_file()
    has_classification = (folder / CLASSIFICATION_FILENAME).is_file()

    # phase — from live_state.json, validated; 'unknown' when there is no
    # live_state (a phase the kiosk never writes, distinct from 'pending').
    phase = "unknown"
    if live is not None:
        candidate = str(live.get("phase") or "").strip()
        if candidate in KNOWN_PHASES:
            phase = candidate

    # pipeline_stage — from status.json, validated; null when no status.json.
    pipeline_stage: Optional[str] = None
    if status is not None:
        candidate = str(status.get("stage") or "").strip()
        pipeline_stage = candidate if candidate in KNOWN_STAGES else "error"

    # turn_count — live_state's own count first, else count transcript turns.
    turn_count = 0
    if live is not None and isinstance(live.get("turn_count"), int):
        turn_count = live["turn_count"]
    elif has_transcript:
        turns = _read_json_list(transcript)
        turn_count = len(turns) if turns is not None else 0

    # chosen_template — live_state first, then interview_state.
    chosen_template: Optional[str] = None
    if live is not None and live.get("chosen_template"):
        chosen_template = live["chosen_template"]
    elif state is not None and state.get("chosen_template"):
        chosen_template = state["chosen_template"]

    # updated_at — live_state's timestamp if present, else the folder's most
    # recent mtime (the folder itself or any file in it) as an ISO string.
    updated_at: Optional[str] = None
    if live is not None and isinstance(live.get("updated_at"), str):
        updated_at = live["updated_at"]
    if not updated_at:
        updated_at = _folder_mtime_iso(folder)

    portrait_url = f"/{session_id}/{PORTRAIT_FILENAME}" if has_portrait else None

    return {
        "session_id": session_id,
        "phase": phase,
        "pipeline_stage": pipeline_stage,
        "turn_count": turn_count,
        "chosen_template": chosen_template,
        "has_transcript": has_transcript,
        "has_portrait": has_portrait,
        "has_classification": has_classification,
        "updated_at": updated_at,
        "portrait_url": portrait_url,
    }


def _read_json_list(path: Path) -> Optional[list]:
    """Return the parsed JSON array at ``path``, or ``None`` if absent/bad."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s unreadable (%s)", path, exc)
        return None
    return raw if isinstance(raw, list) else None


def _folder_mtime_iso(folder: Path) -> Optional[str]:
    """Return the most-recent mtime of ``folder`` or any file in it, as ISO.

    Best-available timestamp for a session with no live_state.json. Falls back
    gracefully (``None``) if the folder cannot be stat'd at all.
    """
    mtimes = []
    try:
        mtimes.append(folder.stat().st_mtime)
    except OSError:
        pass
    try:
        for child in folder.iterdir():
            try:
                mtimes.append(child.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    if not mtimes:
        return None
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).isoformat()


def read_sessions_index(*, sessions_dir: Optional[Path] = None) -> dict:
    """Return the index of every session folder under the sessions root.

    Enumerates each immediate sub-directory of ``SESSIONS_DIR`` whose name is a
    safe session id, builds a :func:`_summarize_session` summary for each, and
    returns ``{"sessions": [...]}`` sorted most-recent-first by ``updated_at``.

    A missing or empty sessions root degrades to ``{"sessions": []}`` — never
    raises, so the dev dashboard always has a uniform shape to parse.
    """
    root = sessions_dir if sessions_dir is not None else sessions_root()
    root = Path(root)

    summaries: list[dict] = []
    try:
        children = list(root.iterdir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return {"sessions": []}

    for child in children:
        if not child.is_dir():
            continue
        if not _is_safe_session_id(child.name):
            continue
        try:
            summaries.append(_summarize_session(child))
        except Exception as exc:  # a partial folder must never break the index
            logger.warning("session %s: summary failed (%s)", child.name, exc)

    # Most-recent first. ``updated_at`` is an ISO-8601 string (or None) — ISO
    # strings sort chronologically lexically; None sorts last.
    summaries.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return {"sessions": summaries}


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

    def _send_cors_headers(self) -> None:
        """Emit permissive CORS headers for the JSON endpoints.

        The kiosk is a browser app served from a different origin/port, so it
        needs ``Access-Control-Allow-Origin: *`` to fetch ``/status`` and
        ``/live``. Called after ``send_response`` and before ``end_headers``.
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The kiosk polls this; never let a stale response be cached.
        self.send_header("Cache-Control", "no-store")
        # The kiosk fetches this cross-origin — permissive CORS.
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        cors: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The dev detail view fetches per-session JSON artifacts cross-origin;
        # callers serving those pass cors=True for the permissive headers.
        if cors:
            self._send_cors_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_page(self, status: HTTPStatus, message: str) -> None:
        body = f"<!DOCTYPE html><meta charset=utf-8><h1>{status.value}</h1><p>{message}</p>"
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    # -- routing -------------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Answer a CORS preflight — 204 with the permissive CORS headers.

        The kiosk's cross-origin ``fetch`` of ``/status`` or ``/live`` may
        trigger an ``OPTIONS`` preflight; answer it so the real request is
        allowed through.
        """
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        # Strip any query string; we route on the path only.
        path = self.path.split("?", 1)[0]

        # GET /sessions — the index of every session folder on this machine.
        if path.rstrip("/") == "/sessions":
            self._handle_sessions_index()
            return

        # GET /status/<session-id>
        if path.startswith("/status/"):
            session_id = path[len("/status/"):].strip("/")
            self._handle_status(session_id)
            return

        # GET /live/<session-id>
        if path.startswith("/live/"):
            session_id = path[len("/live/"):].strip("/")
            self._handle_live(session_id)
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
        elif len(remainder) == 1 and remainder[0] in _IMAGE_ARTIFACTS:
            # An image artifact — portrait.png / qr.png.
            self._handle_file(folder / remainder[0], _IMAGE_ARTIFACTS[remainder[0]])
        elif len(remainder) == 1 and remainder[0] in _JSON_ARTIFACTS:
            # A known JSON artifact — transcript.json, classification.json, etc.
            # The filename is checked against the allowlist, so it is a single
            # plain name with no separators: a ``../`` escape cannot reach here.
            self._handle_file(
                folder / remainder[0],
                "application/json; charset=utf-8",
                cors=True,
            )
        else:
            self._send_error_page(HTTPStatus.NOT_FOUND, "not found")

    # -- handlers ------------------------------------------------------------

    def _handle_sessions_index(self) -> None:
        """GET /sessions — the index of every session folder, never 500.

        A missing/empty sessions root degrades to ``{"sessions": []}``. Sends
        the same permissive CORS headers as /status and /live so the dev
        dashboard can fetch it cross-origin.
        """
        self._send_json(read_sessions_index())

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

    def _handle_live(self, session_id: str) -> None:
        """GET /live/<session-id> — live interview-state JSON, never 500.

        Mirrors :meth:`_handle_status`: an unsafe session id still gets the
        documented shape (the graceful ``pending`` default) so the polling
        kiosk has a uniform thing to parse, never a hard error.
        """
        if not _is_safe_session_id(session_id):
            self._send_json(
                {
                    "session_id": session_id,
                    "phase": "pending",
                    "base_questions_completed": 0,
                    "base_questions_total": 0,
                    "signals": dict(_ZERO_SIGNALS),
                    "leading_template": None,
                    "chosen_template": None,
                    "routing_done": False,
                    "turn_count": 0,
                    "updated_at": None,
                },
                HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(read_live_state(session_id))

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

    def _handle_file(
        self, file_path: Path, content_type: str, *, cors: bool = False
    ) -> None:
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
        self._send_bytes(body, content_type, cors=cors)


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
