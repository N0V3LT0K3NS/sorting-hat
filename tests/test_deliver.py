"""Tests for stage 4 of the offline pipeline: :func:`pipeline.deliver.deliver`.

All tests run with NO network. ``SESSIONS_DIR`` is repointed at a pytest tmp
dir; the optional SendGrid email path is exercised only in its skipped
(no-key) form, so nothing ever touches the wire.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from pipeline.deliver import (
    DeliveryError,
    DeliveryResult,
    InvalidPortraitError,
    InvalidSessionError,
    deliver,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sessions_dir(tmp_path, monkeypatch):
    """Point SESSIONS_DIR at a tmp dir and clear delivery/email config.

    DELIVERY_SERVER_URL and DELIVERY_BASE_URL are both cleared so the QR
    payload tests are isolated from any ambient .env value — otherwise a
    real DELIVERY_SERVER_URL in the environment overrides the per-test
    expectation and these tests fail only under the full suite.
    """
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("SESSIONS_DIR", str(sessions))
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("DELIVERY_FROM_EMAIL", raising=False)
    monkeypatch.delenv("DELIVERY_BASE_URL", raising=False)
    monkeypatch.delenv("DELIVERY_SERVER_URL", raising=False)
    return sessions


@pytest.fixture
def portrait_image():
    """A small in-memory portrait image, as :func:`render` returns one."""
    return Image.new("RGB", (1080, 1350), (40, 40, 48))


@pytest.fixture
def portrait_png(tmp_path, portrait_image):
    """The same portrait persisted to a PNG path on disk."""
    path = tmp_path / "rendered.png"
    portrait_image.save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# Core delivery: folder, portrait, QR, typed result
# ---------------------------------------------------------------------------


def test_deliver_creates_per_session_folder(portrait_image, _sessions_dir):
    """deliver() creates sessions/<session_id>/."""
    result = deliver(portrait_image, "sess-001")

    session_dir = _sessions_dir / "sess-001"
    assert session_dir.is_dir()
    assert result.session_dir == str(session_dir)


def test_portrait_png_lands_in_folder(portrait_image, _sessions_dir):
    """The portrait PNG is written into the per-session folder."""
    result = deliver(portrait_image, "sess-002")

    portrait_path = _sessions_dir / "sess-002" / "portrait.png"
    assert portrait_path.is_file()
    assert result.portrait_path == str(portrait_path)
    # The written file is a real, openable PNG of the expected size.
    with Image.open(portrait_path) as img:
        assert img.format == "PNG"
        assert img.size == (1080, 1350)


def test_accepts_portrait_as_path(portrait_png, _sessions_dir):
    """A portrait passed as a PNG path is loaded and persisted just the same."""
    result = deliver(portrait_png, "sess-path")

    portrait_path = _sessions_dir / "sess-path" / "portrait.png"
    assert portrait_path.is_file()
    assert result.portrait_path == str(portrait_path)


def test_generates_valid_qr_png(portrait_image, _sessions_dir):
    """A valid QR PNG is generated and is openable by Pillow."""
    result = deliver(portrait_image, "sess-003")

    qr_path = _sessions_dir / "sess-003" / "qr.png"
    assert qr_path.is_file()
    assert result.qr_path == str(qr_path)
    with Image.open(qr_path) as img:
        assert img.format == "PNG"
        # A QR code is a non-trivial square image.
        assert img.size[0] > 0 and img.size[0] == img.size[1]


def test_result_reports_correct_paths(portrait_image, _sessions_dir):
    """The typed result reports the folder, portrait, and QR paths correctly."""
    result = deliver(portrait_image, "sess-004")

    assert isinstance(result, DeliveryResult)
    assert result.session_id == "sess-004"
    session_dir = _sessions_dir / "sess-004"
    assert result.session_dir == str(session_dir)
    assert result.portrait_path == str(session_dir / "portrait.png")
    assert result.qr_path == str(session_dir / "qr.png")
    # The QR payload, by default, is a file:// URI of the session folder.
    assert result.qr_payload.startswith("file://")
    assert "sess-004" in result.qr_payload


def test_qr_payload_uses_base_url_when_set(portrait_image, monkeypatch):
    """When DELIVERY_BASE_URL is set, the QR encodes a session-specific URL."""
    monkeypatch.setenv("DELIVERY_BASE_URL", "https://kiosk.example/portraits/")
    result = deliver(portrait_image, "sess-url")
    assert result.qr_payload == "https://kiosk.example/portraits/sess-url"


# ---------------------------------------------------------------------------
# Email path: skipped gracefully with no key
# ---------------------------------------------------------------------------


def test_email_skipped_when_no_key(portrait_image):
    """Email is skipped cleanly when no SendGrid key is configured."""
    result = deliver(portrait_image, "sess-005", email_to="guest@example.com")

    assert result.email_sent is False
    assert "not configured" in result.email_note


def test_email_not_requested_by_default(portrait_image):
    """With no email_to, the email path is simply not requested."""
    result = deliver(portrait_image, "sess-006")

    assert result.email_sent is False
    assert result.email_note == "email not requested"


def test_delivery_succeeds_despite_no_email(portrait_image, _sessions_dir):
    """A no-key email request never blocks folder + QR delivery."""
    result = deliver(portrait_image, "sess-007", email_to="guest@example.com")

    assert (_sessions_dir / "sess-007" / "portrait.png").is_file()
    assert (_sessions_dir / "sess-007" / "qr.png").is_file()


# ---------------------------------------------------------------------------
# Extra artifacts
# ---------------------------------------------------------------------------


def test_persists_inline_string_artifacts(portrait_image, _sessions_dir):
    """Inline str artifacts (e.g. JSON) are written into the session folder."""
    payload = json.dumps({"template": "iceberg", "confidence": 0.91})
    result = deliver(
        portrait_image,
        "sess-008",
        artifacts={"result.json": payload, "transcript.xml": "<transcript/>"},
    )

    json_path = _sessions_dir / "sess-008" / "result.json"
    xml_path = _sessions_dir / "sess-008" / "transcript.xml"
    assert json_path.is_file()
    assert xml_path.is_file()
    assert json.loads(json_path.read_text())["template"] == "iceberg"
    assert xml_path.read_text() == "<transcript/>"
    assert set(result.artifact_paths) == {str(json_path), str(xml_path)}


def test_persists_file_path_artifacts(portrait_image, _sessions_dir, tmp_path):
    """Artifacts given as existing file paths are copied into the folder."""
    src = tmp_path / "source-transcript.xml"
    src.write_text("<transcript><interviewee>hi</interviewee></transcript>")

    result = deliver(
        portrait_image, "sess-009", artifacts={"transcript.xml": src}
    )

    dest = _sessions_dir / "sess-009" / "transcript.xml"
    assert dest.is_file()
    assert dest.read_text() == src.read_text()
    assert result.artifact_paths == [str(dest)]


def test_persists_bytes_artifacts(portrait_image, _sessions_dir):
    """Artifacts given as bytes are written raw."""
    result = deliver(
        portrait_image, "sess-010", artifacts={"data.bin": b"\x00\x01\x02"}
    )

    dest = _sessions_dir / "sess-010" / "data.bin"
    assert dest.is_file()
    assert dest.read_bytes() == b"\x00\x01\x02"
    assert result.artifact_paths == [str(dest)]


def test_no_artifacts_yields_empty_list(portrait_image):
    """With no artifacts passed, artifact_paths is an empty list."""
    result = deliver(portrait_image, "sess-011")
    assert result.artifact_paths == []


def test_missing_artifact_file_raises(portrait_image, tmp_path):
    """An artifact path pointing at a missing file raises a typed error."""
    missing = tmp_path / "does-not-exist.xml"
    with pytest.raises(DeliveryError):
        deliver(portrait_image, "sess-012", artifacts={"t.xml": missing})


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_rejects_empty_session_id(portrait_image):
    """An empty session id raises InvalidSessionError."""
    with pytest.raises(InvalidSessionError):
        deliver(portrait_image, "   ")


def test_rejects_session_id_with_separators(portrait_image):
    """A session id containing path separators is rejected (no folder escape)."""
    with pytest.raises(InvalidSessionError):
        deliver(portrait_image, "../escape")


def test_rejects_bad_portrait_type(_sessions_dir):
    """A portrait that is neither an image nor a path raises a typed error."""
    with pytest.raises(InvalidPortraitError):
        deliver(12345, "sess-013")


def test_rejects_missing_portrait_path(tmp_path):
    """A portrait path that does not exist raises InvalidPortraitError."""
    with pytest.raises(InvalidPortraitError):
        deliver(tmp_path / "nope.png", "sess-014")


def test_result_serialises_to_json(portrait_image):
    """The typed result round-trips through JSON (paths stored as strings)."""
    result = deliver(portrait_image, "sess-015")
    dumped = json.loads(result.model_dump_json())
    assert dumped["session_id"] == "sess-015"
    assert dumped["email_sent"] is False
