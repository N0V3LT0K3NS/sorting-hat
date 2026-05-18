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

import atexit
import json
import logging
import os
import signal
from datetime import datetime, timezone
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

# Pipeline-progress file. The delivery server and the kiosk both read this to
# show a stage-by-stage reveal; run_offline_pipeline rewrites it at each stage
# boundary. See delivery/server.py.
STATUS_FILENAME = "status.json"

# Live-interview state file. Written each turn (alongside the transcript) so
# the delivery server's GET /live/<id> endpoint can expose the classifier's
# signal weights and the interview's progress while the interview is still
# running. The POST-interview counterpart of status.json. See delivery/server.py.
LIVE_STATE_FILENAME = "live_state.json"


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
# Live-interview state file
# ---------------------------------------------------------------------------


def write_live_state(
    session_id: str,
    state: InterviewState,
    *,
    routing_done: bool = False,
    sessions_dir: Optional[Path] = None,
) -> Path:
    """Write/overwrite ``sessions/<session_id>/live_state.json`` for the kiosk.

    The LIVE (during-interview) counterpart of :func:`write_status`. Called
    after every turn — alongside :func:`persist_transcript` — so the delivery
    server's ``GET /live/<session-id>`` endpoint can expose the background
    classifier's four signal weights and the interview's progress in real time.

    ``routing_done`` is the supervisor's authoritative routing-settled flag (it
    lives on the :class:`~agent.interviewer.InterviewerAgent`, not on
    :class:`InterviewState`); the caller passes it in. The phase is derived:
    ``base_questions`` until the base questions are done, ``probing`` once they
    are done but routing has not settled, ``complete`` once ``routing_done``.

    A plain, idempotent rewrite — no DB, no streaming infra; the kiosk just
    polls the file. Returns the live_state.json path.
    """
    # BASE_QUESTION_COUNT is the interview's base-question total. Imported here,
    # not at module load, so importing this module never drags in the agent
    # package's LiveKit dependency.
    from agent.interviewer import BASE_QUESTION_COUNT

    folder = session_dir(session_id, sessions_dir)

    base_done = state.base_questions_completed >= BASE_QUESTION_COUNT
    if routing_done:
        phase = "complete"
    elif base_done:
        phase = "probing"
    else:
        phase = "base_questions"

    live: dict = {
        "session_id": str(session_id),
        "phase": phase,
        "base_questions_completed": state.base_questions_completed,
        "base_questions_total": BASE_QUESTION_COUNT,
        "signals": state.signal_weights(),
        "leading_template": state.leading_template(),
        "chosen_template": state.chosen_template,
        "routing_done": bool(routing_done),
        "turn_count": len(state.transcript_log),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (folder / LIVE_STATE_FILENAME).write_text(
        json.dumps(live, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug(
        "session %s: live_state -> phase=%s turns=%d",
        session_id,
        phase,
        live["turn_count"],
    )
    return folder / LIVE_STATE_FILENAME


# ---------------------------------------------------------------------------
# Pipeline-progress status file
# ---------------------------------------------------------------------------


# The in-progress pipeline stages — stages where status.json says "still
# working". A status.json frozen at one of these (because the worker process
# died mid-stage) is a soft failure: the kiosk would poll it forever. The
# durability guard below and the delivery server's stale-check both key off
# this set to tell "still working" apart from "this run is dead".
IN_PROGRESS_STAGES = frozenset(
    {"classifying", "filling", "rendering", "delivering"}
)

# Terminal stages — a status.json at one of these is final; the kiosk can stop
# polling. The durability guard never overwrites a terminal status.
TERMINAL_STAGES = frozenset({"done", "error"})


def write_status(
    session_id: str,
    stage: str,
    *,
    sessions_dir: Optional[Path] = None,
    error: Optional[str] = None,
) -> Path:
    """Write/overwrite ``sessions/<session_id>/status.json`` with the stage.

    ``stage`` is one of ``classifying`` / ``filling`` / ``rendering`` /
    ``delivering`` / ``done`` / ``error``. The delivery server reads this file
    to answer ``GET /status/<session-id>`` so the kiosk can show a
    stage-by-stage reveal during generation. A plain, idempotent rewrite — no
    DB, no streaming infra; the kiosk just polls the file.

    An ``updated_at`` ISO-8601 timestamp is stamped on every write so the
    delivery server can age-check a stale in-progress status (a status.json
    frozen at e.g. ``filling`` because the worker process died mid-stage).

    Returns the status.json path.
    """
    folder = session_dir(session_id, sessions_dir)
    status: dict = {
        "session_id": str(session_id),
        "stage": stage,
        "error": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (folder / STATUS_FILENAME).write_text(
        json.dumps(status, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug("session %s: status -> %s", session_id, stage)
    return folder / STATUS_FILENAME


def _status_stage(session_id: str, sessions_dir: Optional[Path]) -> Optional[str]:
    """Return the ``stage`` recorded in status.json, or ``None`` if unreadable.

    A small read-back helper for the durability guard: it must not overwrite a
    status.json that already reached a terminal stage (the pipeline finished,
    or already recorded its own error).
    """
    folder = session_dir(session_id, sessions_dir)
    status_path = folder / STATUS_FILENAME
    if not status_path.is_file():
        return None
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    stage = raw.get("stage")
    return str(stage) if stage is not None else None


def write_interrupted_status(
    session_id: str,
    *,
    sessions_dir: Optional[Path] = None,
    reason: str = "the pipeline process exited before the run completed",
) -> Optional[Path]:
    """Write a terminal ``error`` status.json *only if* the run did not finish.

    The durability backstop for an interrupted pipeline. ``run_offline_pipeline``
    writes status.json optimistically — it sets ``filling`` when fill *starts* —
    so if the worker process is killed (SIGTERM) or otherwise dies mid-stage,
    status.json is frozen forever at an in-progress stage with ``error`` null,
    and the kiosk polls it indefinitely.

    This function is wired to run on process termination (a signal handler /
    ``atexit``) and as the ``finally`` of :func:`run_offline_pipeline`. It reads
    the current status.json and:

    * if the stage is already terminal (``done`` / ``error``) — does nothing;
      the run finished, or already recorded its own failure.
    * if the stage is in-progress (``classifying`` … ``delivering``) — rewrites
      status.json as stage ``error`` with a non-null message, so the kiosk's
      poll resolves to a real terminal state instead of spinning forever.
    * if there is no status.json — does nothing; the pipeline never started, so
      there is nothing to mark as interrupted.

    Returns the status.json path when it wrote one, else ``None``. Never raises:
    a backstop that crashed would defeat its own purpose.
    """
    try:
        stage = _status_stage(session_id, sessions_dir)
        if stage is None or stage in TERMINAL_STAGES:
            return None
        message = f"{reason} (interrupted at stage '{stage}')"
        logger.warning(
            "session %s: pipeline status was '%s' at exit — recording "
            "terminal error so the kiosk does not poll forever",
            session_id,
            stage,
        )
        return write_status(
            session_id, "error", sessions_dir=sessions_dir, error=message
        )
    except Exception as exc:  # a backstop must never crash the exit path
        logger.warning(
            "session %s: write_interrupted_status failed (%s)", session_id, exc
        )
        return None


# ---------------------------------------------------------------------------
# Offline pipeline (fix 3)
# ---------------------------------------------------------------------------


def _install_interruption_guard(
    session_id: str, sessions_dir: Optional[Path]
):
    """Install a SIGTERM/SIGINT + atexit backstop for an interrupted pipeline.

    ``run_offline_pipeline`` writes status.json optimistically. If the worker
    process is killed mid-stage (a LiveKit ``dev`` worker is not durable) the
    file freezes at an in-progress stage with ``error`` null and the kiosk
    polls it forever. This installs two backstops that call
    :func:`write_interrupted_status`:

    * an ``atexit`` hook — covers an uncaught exit / interpreter shutdown;
    * SIGTERM and SIGINT handlers — cover the process being killed (the live
      bug). Each handler records the terminal status, then re-raises the
      default disposition so the process still terminates.

    Returns a ``remove()`` callable the ``finally`` block calls once the
    pipeline has finished cleanly, so the guard never fires for a completed
    run and never leaks across pipeline invocations. Signal handlers are only
    installed when running on the main thread (``signal.signal`` requires it);
    off the main thread the ``atexit`` hook and the ``finally`` block still
    cover the common cases.
    """
    def _backstop(*_args) -> None:
        write_interrupted_status(session_id, sessions_dir=sessions_dir)

    atexit.register(_backstop)

    installed: list = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(signum)

            def _handler(sig, frame, _prev=previous):
                _backstop()
                # Restore and re-raise so the process still terminates as it
                # normally would for this signal.
                signal.signal(sig, _prev)
                if callable(_prev) and _prev not in (
                    signal.SIG_DFL, signal.SIG_IGN
                ):
                    _prev(sig, frame)
                else:
                    os.kill(os.getpid(), sig)

            signal.signal(signum, _handler)
            installed.append((signum, previous))
        except (ValueError, OSError):
            # signal.signal raises ValueError off the main thread — the
            # atexit hook + the finally block still cover the run there.
            pass

    def remove() -> None:
        try:
            atexit.unregister(_backstop)
        except Exception:
            pass
        for signum, previous in installed:
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass

    return remove


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

    **Durability against an interrupted process.** status.json is written
    optimistically — ``filling`` is set when fill *starts*. If the worker
    process is killed mid-stage, the file would freeze at an in-progress stage
    with ``error`` null and the kiosk would poll it forever. So the whole run
    is wrapped: a SIGTERM/SIGINT + ``atexit`` guard, plus a ``finally`` block,
    both call :func:`write_interrupted_status`, which rewrites a non-terminal
    status.json as a real terminal ``error``. A run that reaches ``done`` or
    ``error`` normally is already terminal, so the backstop is a no-op for it.

    Returns a small dict of which stages produced output — handy for tests
    and logging. Never raises: a parlor-game kiosk must not crash because an
    API key is absent.
    """
    remove_guard = _install_interruption_guard(session_id, sessions_dir)
    try:
        return _run_offline_pipeline_inner(
            session_id, state, sessions_dir=sessions_dir
        )
    finally:
        # The pipeline reached its own terminal status (done or error) for
        # every path through _run_offline_pipeline_inner, *unless* it raised or
        # the process is dying. write_interrupted_status only writes when the
        # status is still non-terminal, so this is a no-op for a clean run and
        # the honest terminal-status write for an interrupted one.
        write_interrupted_status(session_id, sessions_dir=sessions_dir)
        remove_guard()


def _run_offline_pipeline_inner(
    session_id: str,
    state: InterviewState,
    *,
    sessions_dir: Optional[Path] = None,
) -> dict:
    """The pipeline body — see :func:`run_offline_pipeline` for the contract.

    Split out so :func:`run_offline_pipeline` can wrap it in the interruption
    guard without the durability plumbing cluttering the stage logic.
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
        write_status(session_id, "error", sessions_dir=sessions_dir,
                     error="empty transcript")
        return summary

    transcript_xml = wrap_transcript_xml(turns)

    # --- Stage 1: classify --------------------------------------------------
    write_status(session_id, "classifying", sessions_dir=sessions_dir)
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
        write_status(session_id, "error", sessions_dir=sessions_dir,
                     error="no template available")
        return summary

    # --- Stage 2: fill ------------------------------------------------------
    write_status(session_id, "filling", sessions_dir=sessions_dir)
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
        write_status(session_id, "error", sessions_dir=sessions_dir,
                     error="no typed result")
        return summary

    # --- Stage 3: render ----------------------------------------------------
    write_status(session_id, "rendering", sessions_dir=sessions_dir)
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
        write_status(session_id, "error", sessions_dir=sessions_dir,
                     error=f"render: {exc}")
        return summary

    # --- Stage 4: deliver ---------------------------------------------------
    # The transcript + state JSON are already in this folder from
    # persist_transcript(); deliver writes into the same folder, so they are
    # NOT passed as artifacts (that would copy a file onto itself).
    write_status(session_id, "delivering", sessions_dir=sessions_dir)
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
        write_status(session_id, "error", sessions_dir=sessions_dir,
                     error=f"deliver: {exc}")
        return summary

    write_status(session_id, "done", sessions_dir=sessions_dir)
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
    "write_live_state",
    "write_status",
    "write_interrupted_status",
    "run_offline_pipeline",
    "finalize_session",
    "IN_PROGRESS_STAGES",
    "TERMINAL_STAGES",
    "TRANSCRIPT_FILENAME",
    "STATE_FILENAME",
    "CLASSIFICATION_FILENAME",
    "RESULT_FILENAME",
    "STATUS_FILENAME",
    "LIVE_STATE_FILENAME",
]
