"""Stage 4 of the offline pipeline: ``deliver(png, session) -> DeliveryResult``.

Stage 3 (:mod:`pipeline.render`) produces the portrait PNG. This final stage
*persists* the finished session and gives the participant a way to walk away
with their portrait. For a parlor-game kiosk the friction-free path is:

* write everything into a per-session folder under ``SESSIONS_DIR``
  (``sessions/<session_id>/``),
* generate a QR code PNG encoding a session-specific URL or path, so the
  participant can scan it and pull the portrait off the kiosk,
* optionally email the portrait — but only when explicitly enabled *and* a
  SendGrid key is configured. With no key the email step is skipped cleanly
  with a logged note; it never blocks delivery.

This module is **importable and standalone** — no LiveKit, no network unless
email is explicitly enabled and configured. ``qrcode`` and ``pillow`` are
already project dependencies; SendGrid is optional and imported lazily.

Like :mod:`pipeline.classify` and :mod:`pipeline.fill`, failures are typed
(:class:`DeliveryError` and friends) and missing configuration degrades
gracefully rather than crashing.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Union

import qrcode
from PIL import Image
from pydantic import BaseModel, Field

logger = logging.getLogger("sorting-hat.deliver")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Environment variable naming the root directory for per-session output.
#: Mirrors ``agent.config`` — kept as a direct env read so this module stays
#: standalone (no agent import) and tests can point it at a tmp dir.
SESSIONS_DIR_ENV = "SESSIONS_DIR"

#: Default sessions root when ``SESSIONS_DIR`` is unset. Matches config.py.
DEFAULT_SESSIONS_DIR = "./sessions"

#: Environment variable holding the SendGrid API key. Optional — its absence
#: simply disables the email path.
SENDGRID_API_KEY_ENV = "SENDGRID_API_KEY"

#: Environment variable naming the verified "from" address for emailed
#: portraits. Required for the email path alongside the SendGrid key.
DELIVERY_FROM_EMAIL_ENV = "DELIVERY_FROM_EMAIL"

#: Base URL of the local delivery server (delivery/server.py) that serves the
#: per-session portrait page. The QR encodes ``<base>/<session_id>/`` so a
#: phone on the same wifi lands on the participant's portrait. For the scan to
#: work from a phone this MUST be the kiosk's LAN IP, not localhost.
DELIVERY_SERVER_URL_ENV = "DELIVERY_SERVER_URL"

#: Default delivery-server base URL when ``DELIVERY_SERVER_URL`` is unset.
DEFAULT_DELIVERY_SERVER_URL = "http://localhost:8808"

#: Legacy/fallback base URL the QR code points at. Used only when
#: ``DELIVERY_SERVER_URL`` is unset; when both are unset the QR encodes the
#: local session-folder path.
DELIVERY_BASE_URL_ENV = "DELIVERY_BASE_URL"

#: Filenames written inside each per-session folder.
PORTRAIT_FILENAME = "portrait.png"
QR_FILENAME = "qr.png"


# ---------------------------------------------------------------------------
# Typed errors and result
# ---------------------------------------------------------------------------


class DeliveryError(RuntimeError):
    """Base class for any failure in the delivery stage."""


class InvalidPortraitError(DeliveryError):
    """Raised when the portrait argument is neither a PIL image nor a PNG path."""


class InvalidSessionError(DeliveryError):
    """Raised when the session identifier is missing or unusable as a folder name."""


class DeliveryResult(BaseModel):
    """A small typed description of what :func:`deliver` produced.

    Reports the per-session folder, the portrait and QR paths written into
    it, any extra artifact paths persisted alongside, the value encoded in
    the QR code, and whether the optional email step actually sent. ``Path``
    objects are stored as strings so the result serialises cleanly to JSON.
    """

    model_config = {"arbitrary_types_allowed": True}

    session_id: str = Field(..., description="The session identifier used.")
    session_dir: str = Field(..., description="Per-session output folder.")
    portrait_path: str = Field(..., description="The portrait PNG written.")
    qr_path: str = Field(..., description="The QR-code PNG written.")
    qr_payload: str = Field(..., description="The URL/path encoded in the QR.")
    artifact_paths: list[str] = Field(
        default_factory=list,
        description="Any extra transcript/JSON artifacts persisted.",
    )
    email_sent: bool = Field(
        default=False,
        description="True only when a portrait email was actually sent.",
    )
    email_note: str = Field(
        default="email not requested",
        description="Human-readable note on the email path outcome.",
    )


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _safe_session_id(session_id: object) -> str:
    """Return a filesystem-safe session id, or raise :class:`InvalidSessionError`.

    The id becomes a single folder name, so it must be a non-empty string
    that does not try to escape the sessions root (no separators, no ``..``).
    """
    if not isinstance(session_id, str):
        raise InvalidSessionError(
            f"session id must be a string, got {type(session_id).__name__}"
        )
    cleaned = session_id.strip()
    if not cleaned:
        raise InvalidSessionError("session id must be a non-empty string")
    if cleaned in (".", "..") or any(sep in cleaned for sep in ("/", "\\", "\0")):
        raise InvalidSessionError(
            f"session id {session_id!r} is not usable as a folder name "
            "(no path separators, no '.'/'..')"
        )
    return cleaned


def _load_portrait(portrait: Union[str, Path, Image.Image]) -> Image.Image:
    """Return the portrait as a PIL image, accepting an image or a PNG path.

    Raises :class:`InvalidPortraitError` for an unsupported type or a path
    that does not point at a readable image.
    """
    if isinstance(portrait, Image.Image):
        return portrait
    if isinstance(portrait, (str, Path)):
        path = Path(portrait)
        if not path.exists():
            raise InvalidPortraitError(f"portrait file does not exist: {path}")
        try:
            img = Image.open(path)
            img.load()  # force a read now so a bad file fails here, typed.
        except Exception as exc:  # PIL.UnidentifiedImageError and friends
            raise InvalidPortraitError(
                f"portrait file {path} is not a readable image: {exc}"
            ) from exc
        return img
    raise InvalidPortraitError(
        "portrait must be a PIL Image or a path to a PNG, got "
        f"{type(portrait).__name__}"
    )


# ---------------------------------------------------------------------------
# Sessions directory + QR payload
# ---------------------------------------------------------------------------


def sessions_root() -> Path:
    """Return the sessions root directory from ``SESSIONS_DIR`` (or default).

    Read fresh from the environment on every call so tests can repoint it at
    a tmp dir between cases.
    """
    raw = (os.environ.get(SESSIONS_DIR_ENV) or "").strip()
    return Path(raw or DEFAULT_SESSIONS_DIR)


def build_qr_payload(session_id: str, session_dir: Path) -> str:
    """Return the value to encode in the QR code for a session.

    Resolution order:

    * ``DELIVERY_SERVER_URL`` — the local delivery server (delivery/server.py).
      The QR points at the per-session page ``<base>/<session_id>/`` so a phone
      on the same wifi scans straight to the portrait. This is the normal kiosk
      path; the env value must be the kiosk's LAN IP, not localhost.
    * ``DELIVERY_BASE_URL`` — legacy fallback, encodes ``<base>/<session_id>``.
    * neither set — encodes the absolute local session folder path, still a
      useful pointer on a standalone kiosk.
    """
    server = (os.environ.get(DELIVERY_SERVER_URL_ENV) or "").strip()
    if server:
        return f"{server.rstrip('/')}/{session_id}/"
    base = (os.environ.get(DELIVERY_BASE_URL_ENV) or "").strip()
    if base:
        return f"{base.rstrip('/')}/{session_id}"
    return session_dir.resolve().as_uri()


def _write_qr(payload: str, out_path: Path) -> None:
    """Generate a QR-code PNG encoding ``payload`` and save it to ``out_path``."""
    qr = qrcode.QRCode(
        version=None,  # auto-size to fit the payload.
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# Optional email path
# ---------------------------------------------------------------------------


def _email_config() -> Optional[tuple[str, str]]:
    """Return ``(api_key, from_email)`` if both are configured, else ``None``."""
    key = (os.environ.get(SENDGRID_API_KEY_ENV) or "").strip()
    sender = (os.environ.get(DELIVERY_FROM_EMAIL_ENV) or "").strip()
    if key and sender:
        return key, sender
    return None


def _try_send_email(
    portrait_path: Path,
    recipient: str,
    session_id: str,
) -> tuple[bool, str]:
    """Attempt to email the portrait via SendGrid; return ``(sent, note)``.

    Degrades gracefully: a missing key/sender, a missing SendGrid SDK, or an
    API failure all return ``(False, <note>)`` with a logged warning rather
    than raising. Only a confirmed send returns ``(True, ...)``.
    """
    cfg = _email_config()
    if cfg is None:
        note = (
            f"{SENDGRID_API_KEY_ENV}/{DELIVERY_FROM_EMAIL_ENV} not configured "
            "— email skipped, portrait saved to the session folder + QR"
        )
        logger.info("session %s: %s", session_id, note)
        return False, note

    api_key, sender = cfg
    try:
        import base64

        from sendgrid import SendGridAPIClient  # type: ignore
        from sendgrid.helpers.mail import (  # type: ignore
            Attachment,
            Disposition,
            FileContent,
            FileName,
            FileType,
            Mail,
        )
    except ImportError as exc:
        note = f"sendgrid SDK not installed ({exc}) — email skipped"
        logger.warning("session %s: %s", session_id, note)
        return False, note

    try:
        encoded = base64.b64encode(portrait_path.read_bytes()).decode("ascii")
        message = Mail(
            from_email=sender,
            to_emails=recipient,
            subject="Your sorting-hat portrait",
            plain_text_content=(
                "Your portrait from the interview is attached. "
                "Thanks for sitting with us."
            ),
        )
        message.attachment = Attachment(
            FileContent(encoded),
            FileName(PORTRAIT_FILENAME),
            FileType("image/png"),
            Disposition("attachment"),
        )
        SendGridAPIClient(api_key).send(message)
    except Exception as exc:  # network / API / SDK failure
        note = f"SendGrid send failed ({exc}) — portrait still saved + QR"
        logger.warning("session %s: %s", session_id, note)
        return False, note

    note = f"portrait emailed to {recipient}"
    logger.info("session %s: %s", session_id, note)
    return True, note


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def deliver(
    portrait: Union[str, Path, Image.Image],
    session_id: str,
    *,
    artifacts: Optional[dict[str, Union[str, Path, bytes]]] = None,
    email_to: Optional[str] = None,
) -> DeliveryResult:
    """Persist a finished session and produce its participant-facing delivery.

    ``portrait`` is the rendered portrait — either a :class:`PIL.Image.Image`
    (as returned by :func:`pipeline.render.render` with no ``out_path``) or a
    path to an existing PNG. It is written into the per-session folder as
    ``portrait.png``.

    ``session_id`` identifies the interview; it becomes a single folder name
    under ``SESSIONS_DIR`` (``sessions/<session_id>/``). It must be a
    non-empty string with no path separators.

    ``artifacts`` optionally maps filenames to extra session artifacts to
    persist alongside the portrait — e.g. ``{"transcript.xml": <path>,
    "result.json": '{"template": ...}'}``. Each value may be a path to copy,
    a ``str`` to write as text, or ``bytes`` to write raw.

    ``email_to`` optionally requests emailing the portrait. The email path is
    flag-gated *and* config-gated: it runs only when ``email_to`` is given
    **and** ``SENDGRID_API_KEY`` + ``DELIVERY_FROM_EMAIL`` are set. With no
    key the step is skipped cleanly with a logged note — delivery still
    succeeds via the session folder and QR code.

    Returns a :class:`DeliveryResult` describing the folder, the portrait and
    QR paths, any extra artifacts written, the QR payload, and whether an
    email was sent.

    Raises:
        InvalidSessionError: ``session_id`` is missing or unusable.
        InvalidPortraitError: ``portrait`` is not an image or readable PNG.
        DeliveryError: any other delivery-stage failure (e.g. an artifact
            value of an unsupported type).
    """
    sid = _safe_session_id(session_id)
    img = _load_portrait(portrait)

    session_dir = sessions_root() / sid
    session_dir.mkdir(parents=True, exist_ok=True)

    # (a)/(b) Write the portrait PNG into the per-session folder.
    portrait_path = session_dir / PORTRAIT_FILENAME
    rgb = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
    rgb.save(portrait_path, "PNG")

    # (b) Persist any extra transcript / JSON artifacts alongside it.
    artifact_paths: list[str] = []
    for name, value in (artifacts or {}).items():
        dest = session_dir / Path(name).name  # flatten — no nested escape.
        if isinstance(value, bytes):
            dest.write_bytes(value)
        elif isinstance(value, str) and not _looks_like_existing_path(value):
            dest.write_text(value, encoding="utf-8")
        elif isinstance(value, (str, Path)):
            src = Path(value)
            if not src.exists():
                raise DeliveryError(
                    f"artifact {name!r} points at a missing file: {src}"
                )
            shutil.copyfile(src, dest)
        else:
            raise DeliveryError(
                f"artifact {name!r} must be a path, str, or bytes, got "
                f"{type(value).__name__}"
            )
        artifact_paths.append(str(dest))

    # (c) Generate the session-specific QR code.
    qr_payload = build_qr_payload(sid, session_dir)
    qr_path = session_dir / QR_FILENAME
    _write_qr(qr_payload, qr_path)

    # Optional, flag-gated email path.
    if email_to:
        email_sent, email_note = _try_send_email(portrait_path, email_to, sid)
    else:
        email_sent, email_note = False, "email not requested"

    # (d) Return the typed delivery description.
    return DeliveryResult(
        session_id=sid,
        session_dir=str(session_dir),
        portrait_path=str(portrait_path),
        qr_path=str(qr_path),
        qr_payload=qr_payload,
        artifact_paths=artifact_paths,
        email_sent=email_sent,
        email_note=email_note,
    )


def _looks_like_existing_path(value: str) -> bool:
    """True when a string artifact value names a file that exists on disk.

    A short string is treated as a path candidate; long multi-line text is
    assumed to be inline content. Either way the deciding factor is whether
    the file actually exists — so inline JSON/text never accidentally hits
    the filesystem.
    """
    if "\n" in value or len(value) > 4096:
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False
