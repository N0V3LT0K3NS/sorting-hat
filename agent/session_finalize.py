"""FIX-3 — the missing seam: close the session, persist, run the pipeline.

A live interview ran end-to-end — greeting, base questions, a probe, a
spoken closing — but nothing *closed* it. The :class:`AgentSession` was never
programmatically ended, the mic kept listening, no ``sessions/<id>/`` folder
was written, and the offline analysis pipeline never fired. The transcript
lived only in subprocess memory and was lost on a browser refresh.

This module wires that seam, KISS — no DB, no job queue, no streaming infra:

* :func:`persist_transcript` — write the transcript + :class:`InterviewState`
  as JSON into ``sessions/<session_id>/``. Called **after every turn** during
  the interview (cheap, idempotent rewrite) so a refresh/crash never loses
  what was said, and once more at close for the finalised snapshot.
* :func:`run_offline_pipeline` — run ``classify -> fill -> render -> deliver``
  in sequence on the finished session, writing outputs into the same folder.
  A stage that fails (e.g. a missing API key) is logged and the run
  continues — the transcript is already safe on disk.
* :func:`finalize_session` — the top-level close path: end the session, write
  the final snapshot, then run the pipeline.

The four pipeline functions are imported and *called*, never modified.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from agent.config import config as _config
from agent.state import InterviewState

logger = logging.getLogger("sorting-hat.finalize")

# Filenames written inside each per-session folder. ``portrait.png`` and
# ``qr.png`` are written by pipeline.deliver; the names below are this
# module's own.
TRANSCRIPT_FILENAME = "transcript.json"
STATE_FILENAME = "interview_state.json"
CLASSIFICATION_FILENAME = "classification.json"
RESULT_FILENAME = "result.json"


# ---------------------------------------------------------------------------
# Per-session folder
# ---------------------------------------------------------------------------


def sessions_root() -> Path:
    """Return the sessions root from ``SESSIONS_DIR`` env, read fresh.

    Mirrors :func:`pipeline.deliver.sessions_root` — a direct env read, not
    the frozen :data:`agent.config.config` singleton — so the offline
    pipeline's ``deliver`` stage and this module always agree on the folder,
    and tests can repoint the root with ``monkeypatch.setenv``. Falls back to
    config's resolved default when the env var is unset.
    """
    raw = (os.environ.get("SESSIONS_DIR") or "").strip()
    return Path(raw) if raw else Path(_config.sessions_dir)


def session_dir(session_id: str, sessions_dir: Optional[Path] = None) -> Path:
    """Return (and create) the ``sessions/<session_id>/`` folder.

    ``sessions_dir`` defaults to :func:`sessions_root` (the ``SESSIONS_DIR``
    env value). The folder is created if absent so callers can write into it
    immediately.
    """
    root = sessions_dir if sessions_dir is not None else sessions_root()
    folder = Path(root) / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
# Transcript + state persistence (fix 2)
# ---------------------------------------------------------------------------


def persist_transcript(
    session_id: str,
    state: InterviewState,
    *,
    sessions_dir: Optional[Path] = None,
) -> Path:
    """Write the transcript + full :class:`InterviewState` as JSON to disk.

    Writes two files into ``sessions/<session_id>/``:

    * ``transcript.json`` — the ordered turn log, the shape the offline
      pipeline reads.
    * ``interview_state.json`` — the whole state (chosen_template, probe
      result, signal weights, progress).

    This is a plain, idempotent rewrite — safe to call after **every turn**
    during the interview so a refresh/crash never loses the transcript, and
    once more at close for the finalised snapshot. No DB, no streaming infra.

    Returns the per-session folder path.
    """
    folder = session_dir(session_id, sessions_dir)

    transcript_path = folder / TRANSCRIPT_FILENAME
    transcript_path.write_text(
        json.dumps(list(state.transcript_log), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    state_path = folder / STATE_FILENAME
    # pydantic's model_dump_json serialises the nested typed probe results
    # cleanly; mode is the default so floats/None survive a round trip.
    state_path.write_text(
        state.model_dump_json(indent=2),
        encoding="utf-8",
    )

    logger.debug(
        "session %s: persisted transcript (%d turns) + state to %s",
        session_id,
        len(state.transcript_log),
        folder,
    )
    return folder


# ---------------------------------------------------------------------------
# Offline pipeline (fix 3)
# ---------------------------------------------------------------------------


def run_offline_pipeline(
    session_id: str,
    state: InterviewState,
    *,
    sessions_dir: Optional[Path] = None,
) -> dict:
    """Run ``classify -> fill -> render -> deliver`` on a finished session.

    Plain sequential calls — no job queue. Each stage writes its output into
    ``sessions/<session_id>/``. If a stage fails (a missing OpenRouter key is
    the common case), the failure is logged and the run continues to the next
    stage where it can; the transcript is already safe on disk from
    :func:`persist_transcript`, so a degraded pipeline never loses data.

    Returns a small dict of which stages produced output — handy for tests
    and logging. Never raises: a parlor-game kiosk must not crash because an
    API key is absent.
    """
    # Imported here, not at module load, so importing this module never drags
    # in the pipeline's optional deps (openai SDK, pillow, qrcode).
    from pipeline.classify import classify, wrap_transcript_xml
    from pipeline.deliver import deliver
    from pipeline.fill import fill
    from pipeline.render import render

    folder = session_dir(session_id, sessions_dir)
    summary: dict = {
        "session_id": session_id,
        "classified": False,
        "filled": False,
        "rendered": False,
        "delivered": False,
        "errors": [],
    }

    turns = state.transcript_turns()
    if not turns:
        logger.warning(
            "session %s: transcript is empty — skipping the offline pipeline",
            session_id,
        )
        summary["errors"].append("empty transcript")
        return summary

    transcript_xml = wrap_transcript_xml(turns)

    # --- Stage 1: classify --------------------------------------------------
    template: Optional[str] = None
    try:
        classification = classify(transcript_xml)
        template = classification.template
        (folder / CLASSIFICATION_FILENAME).write_text(
            classification.model_dump_json(indent=2), encoding="utf-8"
        )
        summary["classified"] = True
        summary["template"] = template
        logger.info("session %s: classified as '%s'", session_id, template)
    except Exception as exc:  # missing key, parse failure, network — log + go on
        logger.warning(
            "session %s: classify stage failed (%s) — continuing; "
            "transcript is already safe on disk",
            session_id,
            exc,
        )
        summary["errors"].append(f"classify: {exc}")
        # Fall back to the live interview's own verdict if the probe landed
        # one, so fill/render can still run from the on-device signal.
        template = state.chosen_template
        if template is not None:
            logger.info(
                "session %s: falling back to the live chosen_template '%s'",
                session_id,
                template,
            )

    if template is None:
        logger.warning(
            "session %s: no template available — skipping fill/render/deliver",
            session_id,
        )
        return summary

    # --- Stage 2: fill ------------------------------------------------------
    typed_result = None
    try:
        probe_result = _live_probe_result(state, template)
        typed_result = fill(template, transcript_xml, probe_result)
        (folder / RESULT_FILENAME).write_text(
            typed_result.model_dump_json(indent=2), encoding="utf-8"
        )
        summary["filled"] = True
        logger.info("session %s: filled '%s' slots", session_id, template)
    except Exception as exc:
        logger.warning(
            "session %s: fill stage failed (%s) — continuing", session_id, exc
        )
        summary["errors"].append(f"fill: {exc}")
        # Fall back to the live probe's typed result if one exists, so the
        # renderer can still produce a portrait.
        typed_result = _live_probe_result(state, template)

    if typed_result is None:
        logger.warning(
            "session %s: no typed result — skipping render/deliver", session_id
        )
        return summary

    # --- Stage 3: render ----------------------------------------------------
    portrait_path = folder / "portrait.png"
    try:
        render(typed_result, portrait_path)
        summary["rendered"] = True
        logger.info("session %s: rendered portrait", session_id)
    except Exception as exc:
        logger.warning(
            "session %s: render stage failed (%s) — skipping deliver",
            session_id,
            exc,
        )
        summary["errors"].append(f"render: {exc}")
        return summary

    # --- Stage 4: deliver ---------------------------------------------------
    # The transcript + state JSON are already in this folder from
    # persist_transcript(); deliver writes into the same folder, so they are
    # NOT passed as artifacts (that would copy a file onto itself).
    try:
        delivery = deliver(portrait_path, session_id)
        summary["delivered"] = True
        summary["qr_path"] = delivery.qr_path
        logger.info("session %s: delivered (QR at %s)", session_id, delivery.qr_path)
    except Exception as exc:
        logger.warning(
            "session %s: deliver stage failed (%s)", session_id, exc
        )
        summary["errors"].append(f"deliver: {exc}")

    return summary


def _live_probe_result(state: InterviewState, template: str):
    """Return the live interview's typed probe result for ``template``, if any.

    The live probe runs during the interview and may have landed a typed
    result on :class:`InterviewState`. It is a *head start* for the fill
    stage and a fallback portrait source if fill fails — never gospel.
    """
    field = {
        "iceberg": "iceberg_result",
        "two_buttons": "two_buttons_result",
        "compass": "compass_result",
        "arc": "arc_result",
    }.get(template)
    return getattr(state, field, None) if field else None


# ---------------------------------------------------------------------------
# Top-level close path (fix 1 + 2a + 3)
# ---------------------------------------------------------------------------


async def finalize_session(
    session: object,
    session_id: str,
    state: InterviewState,
    *,
    sessions_dir: Optional[Path] = None,
    run_pipeline: bool = True,
) -> dict:
    """Close the session, persist the finalised snapshot, run the pipeline.

    The single seam the live worker calls once supervisor routing has settled
    (a probe landed, the closing was spoken). In order:

    1. **Close the session.** ``session.aclose()`` is awaited — the LiveKit
       v1.5.9 close API — so the mic stops listening. A session that exposes
       no ``aclose`` is tolerated (tests pass a lightweight fake): closing is
       best-effort and a close failure never blocks persistence.
    2. **Persist** the final transcript + :class:`InterviewState` JSON into
       ``sessions/<session_id>/`` (:func:`persist_transcript`).
    3. **Run the offline pipeline** — ``classify -> fill -> render ->
       deliver`` — over the finished session (:func:`run_offline_pipeline`),
       unless ``run_pipeline`` is False.

    Returns the pipeline summary dict (empty-ish when ``run_pipeline`` is
    False). Never raises — a kiosk must close gracefully whatever fails.
    """
    # --- 1. Close the session — the mic must stop. --------------------------
    aclose = getattr(session, "aclose", None)
    if callable(aclose):
        try:
            result = aclose()
            if hasattr(result, "__await__"):
                await result
            logger.info("session %s: AgentSession closed (mic stopped)", session_id)
        except Exception as exc:
            logger.warning(
                "session %s: session.aclose() raised (%s) — continuing to "
                "persist and run the pipeline anyway",
                session_id,
                exc,
            )
    else:
        logger.warning(
            "session %s: object has no aclose() — skipping close step",
            session_id,
        )

    # --- 2. Persist the finalised snapshot. ---------------------------------
    try:
        persist_transcript(session_id, state, sessions_dir=sessions_dir)
    except Exception as exc:
        logger.warning(
            "session %s: final persist failed (%s)", session_id, exc
        )

    # --- 3. Run the offline pipeline. ---------------------------------------
    if not run_pipeline:
        return {"session_id": session_id, "pipeline_skipped": True}

    return run_offline_pipeline(session_id, state, sessions_dir=sessions_dir)


__all__ = [
    "sessions_root",
    "session_dir",
    "persist_transcript",
    "run_offline_pipeline",
    "finalize_session",
    "TRANSCRIPT_FILENAME",
    "STATE_FILENAME",
    "CLASSIFICATION_FILENAME",
    "RESULT_FILENAME",
]
