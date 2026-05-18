"""FIX-3 tests — close the session, persist the transcript, run the pipeline.

Covers the three fixes the goal must prove, all fully offline (no live LLM,
no LiveKit room, no network):

1. **Close on routing complete.** Once supervisor routing has settled,
   ``finalize_session`` calls the session's ``aclose()`` — the mic stops.
2. **Transcript persistence.** ``persist_transcript`` writes the transcript +
   ``InterviewState`` JSON into ``sessions/<id>/``.
3. **Incremental write.** A turn recorded mid-interview is on disk before the
   interview ends — a refresh/crash never loses it.
4. **Pipeline triggered on close.** ``finalize_session`` runs
   ``classify -> fill -> render -> deliver`` after closing; the LLM stages are
   mocked, and a missing key degrades gracefully without losing the transcript.

The session is a lightweight fake exposing just ``aclose`` and ``on``; the
pipeline's two LLM stages are monkeypatched so nothing touches the wire.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.interviewer import BASE_QUESTION_COUNT, InterviewerAgent
from agent.session_finalize import (
    CLASSIFICATION_FILENAME,
    RESULT_FILENAME,
    STATE_FILENAME,
    TRANSCRIPT_FILENAME,
    finalize_session,
    persist_transcript,
    run_offline_pipeline,
    session_dir,
)
from agent.state import IcebergResult, InterviewState

# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------


class FakeSession:
    """A minimal stand-in for AgentSession — records that aclose() ran."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _peopled_state() -> InterviewState:
    """An InterviewState with a few recorded turns and a landed probe."""
    state = InterviewState(iceberg_signal=0.9)
    state.record_turn("interviewer", "Tell me about something you're known for.")
    state.record_turn("interviewee", "People say I'm the one who stays calm.")
    state.record_turn("interviewer", "What's under that calm?")
    state.record_turn("interviewee", "Honestly? I'm not sure there's anything there.")
    state.chosen_template = "iceberg"
    state.iceberg_result = IcebergResult(
        surface="The calm, capable one.",
        first_layer="Tired of being depended on.",
        second_layer="Suspects the competence is a costume.",
        abyss="Afraid there is nothing underneath.",
    )
    return state


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """A tmp sessions root, also exported as SESSIONS_DIR for pipeline.deliver."""
    root = tmp_path / "sessions"
    monkeypatch.setenv("SESSIONS_DIR", str(root))
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("DELIVERY_FROM_EMAIL", raising=False)
    monkeypatch.delenv("DELIVERY_BASE_URL", raising=False)
    return root


# ---------------------------------------------------------------------------
# 1. Close on routing complete
# ---------------------------------------------------------------------------


def test_finalize_closes_the_session(sessions_dir) -> None:
    """finalize_session calls session.aclose() — the mic stops."""
    session = FakeSession()
    state = _peopled_state()

    asyncio.run(
        finalize_session(
            session, "sess-close", state,
            sessions_dir=sessions_dir, run_pipeline=False,
        )
    )

    assert session.closed is True


def test_finalize_persists_even_if_close_raises(sessions_dir) -> None:
    """A failing aclose() never blocks transcript persistence."""

    class BrokenSession:
        async def aclose(self) -> None:
            raise RuntimeError("transport already gone")

    state = _peopled_state()
    asyncio.run(
        finalize_session(
            BrokenSession(), "sess-broken", state,
            sessions_dir=sessions_dir, run_pipeline=False,
        )
    )

    # The transcript still landed on disk despite the close failure.
    transcript = sessions_dir / "sess-broken" / TRANSCRIPT_FILENAME
    assert transcript.is_file()


def test_close_fires_once_when_routing_done() -> None:
    """The worker's wiring schedules finalize exactly once, after routing."""
    from agent.main import wire_session_finalize

    state = InterviewState(iceberg_signal=1.0)
    agent = InterviewerAgent(state=state)
    # Drive routing to done.
    for _ in range(BASE_QUESTION_COUNT):
        agent.advance_base_question()
    state.chosen_template = "iceberg"  # routing_done is now True
    assert agent.routing_done is True

    session = FakeSession()
    handlers: list = []
    session.on = lambda event, cb: handlers.append((event, cb))  # type: ignore

    wire_session_finalize(session, agent, state, "sess-once")
    assert handlers and handlers[0][0] == "conversation_item_added"


# ---------------------------------------------------------------------------
# 2. Transcript persistence
# ---------------------------------------------------------------------------


def test_persist_transcript_writes_both_files(sessions_dir) -> None:
    """persist_transcript writes transcript.json + interview_state.json."""
    state = _peopled_state()
    folder = persist_transcript("sess-persist", state, sessions_dir=sessions_dir)

    transcript_path = folder / TRANSCRIPT_FILENAME
    state_path = folder / STATE_FILENAME
    assert transcript_path.is_file()
    assert state_path.is_file()

    turns = json.loads(transcript_path.read_text())
    assert len(turns) == 4
    assert turns[0]["speaker"] == "interviewer"
    assert turns[1]["text"].startswith("People say")

    saved_state = json.loads(state_path.read_text())
    assert saved_state["chosen_template"] == "iceberg"
    assert saved_state["iceberg_result"]["surface"] == "The calm, capable one."


def test_persist_transcript_uses_sessions_dir(sessions_dir) -> None:
    """The per-session folder lands under the configured SESSIONS_DIR root."""
    state = _peopled_state()
    folder = persist_transcript("sess-abc", state, sessions_dir=sessions_dir)
    assert folder == sessions_dir / "sess-abc"
    assert folder.is_dir()


def test_record_turn_skips_empty_text() -> None:
    """An empty/whitespace turn is not recorded — no empty transcript padding."""
    state = InterviewState()
    state.record_turn("interviewee", "   ")
    state.record_turn("interviewer", "")
    state.record_turn("interviewee", "Something real.")
    assert len(state.transcript_log) == 1


def test_interviewer_record_turn_appends_to_state() -> None:
    """InterviewerAgent.record_turn writes onto the shared InterviewState."""
    state = InterviewState()
    agent = InterviewerAgent(state=state)
    agent.record_turn("interviewer", "First question.")
    agent.record_turn("interviewee", "First answer.")
    assert state.transcript_turns() == [
        ("interviewer", "First question."),
        ("interviewee", "First answer."),
    ]


# ---------------------------------------------------------------------------
# 3. Incremental write — a refresh mid-interview never loses the transcript
# ---------------------------------------------------------------------------


def test_incremental_write_persists_after_each_turn(sessions_dir) -> None:
    """Persisting after every turn means a crash mid-interview loses nothing."""
    state = InterviewState()

    state.record_turn("interviewer", "Question one.")
    persist_transcript("sess-incr", state, sessions_dir=sessions_dir)
    after_one = json.loads(
        (sessions_dir / "sess-incr" / TRANSCRIPT_FILENAME).read_text()
    )
    assert len(after_one) == 1  # one turn already safe on disk

    state.record_turn("interviewee", "Answer one.")
    persist_transcript("sess-incr", state, sessions_dir=sessions_dir)
    after_two = json.loads(
        (sessions_dir / "sess-incr" / TRANSCRIPT_FILENAME).read_text()
    )
    assert len(after_two) == 2  # the rewrite grew with the interview


def test_wired_handler_records_and_persists_each_turn(sessions_dir) -> None:
    """The worker's conversation_item handler records + persists every turn."""
    from agent.main import wire_session_finalize

    state = InterviewState()
    agent = InterviewerAgent(state=state)
    session = FakeSession()
    captured: list = []
    session.on = lambda event, cb: captured.append(cb)  # type: ignore

    wire_session_finalize(session, agent, state, "sess-wired")
    handler = captured[0]

    class FakeItem:
        def __init__(self, role: str, text: str) -> None:
            self.role = role
            self.text_content = text

    class FakeEvent:
        def __init__(self, item: object) -> None:
            self.item = item

    handler(FakeEvent(FakeItem("assistant", "An interviewer question.")))
    handler(FakeEvent(FakeItem("user", "An interviewee answer.")))

    # Both turns recorded, mapped onto interviewer/interviewee.
    assert state.transcript_turns() == [
        ("interviewer", "An interviewer question."),
        ("interviewee", "An interviewee answer."),
    ]
    # And both already persisted incrementally.
    on_disk = json.loads(
        (sessions_dir / "sess-wired" / TRANSCRIPT_FILENAME).read_text()
    )
    assert len(on_disk) == 2


# ---------------------------------------------------------------------------
# 4. Pipeline triggered on close
# ---------------------------------------------------------------------------


def _mock_pipeline(monkeypatch) -> None:
    """Monkeypatch classify + fill so no LLM/network call is made."""
    from pipeline import classify as classify_mod
    from pipeline import fill as fill_mod
    from agent import session_finalize as sf

    from pipeline.classify import ClassificationResult

    def fake_classify(transcript_xml, **kwargs):
        return ClassificationResult(
            template="iceberg",
            confidence=0.88,
            reasoning="Clear vertical layering — a hidden bottom.",
        )

    def fake_fill(template_label, transcript, probe_result=None, **kwargs):
        return IcebergResult(
            surface="The dependable one.",
            first_layer="Quietly worn out.",
            second_layer="Suspects the competence is performance.",
            abyss="Afraid of the empty underneath.",
        )

    # finalize imports classify/fill locally inside run_offline_pipeline, so
    # patch the names on their home modules.
    monkeypatch.setattr(classify_mod, "classify", fake_classify)
    monkeypatch.setattr(fill_mod, "fill", fake_fill)


def test_pipeline_runs_on_finalize(sessions_dir, monkeypatch) -> None:
    """finalize_session runs classify -> fill -> render -> deliver after close."""
    _mock_pipeline(monkeypatch)
    session = FakeSession()
    state = _peopled_state()

    summary = asyncio.run(
        finalize_session(session, "sess-pipe", state, sessions_dir=sessions_dir)
    )

    assert session.closed is True
    assert summary["classified"] is True
    assert summary["filled"] is True
    assert summary["rendered"] is True
    assert summary["delivered"] is True

    folder = sessions_dir / "sess-pipe"
    assert (folder / TRANSCRIPT_FILENAME).is_file()
    assert (folder / STATE_FILENAME).is_file()
    assert (folder / CLASSIFICATION_FILENAME).is_file()
    assert (folder / RESULT_FILENAME).is_file()
    assert (folder / "portrait.png").is_file()
    assert (folder / "qr.png").is_file()


def test_pipeline_degrades_when_classify_key_missing(
    sessions_dir, monkeypatch
) -> None:
    """A missing API key degrades gracefully — transcript stays safe."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # fill stays unmocked too; classify will raise MissingAPIKeyError. The
    # live chosen_template ('iceberg') + probe result let render still run.
    session = FakeSession()
    state = _peopled_state()

    summary = asyncio.run(
        finalize_session(session, "sess-nokey", state, sessions_dir=sessions_dir)
    )

    # Classify failed but was caught — the run did not crash.
    assert summary["classified"] is False
    assert any("classify" in e for e in summary["errors"])
    # The transcript is on disk regardless — fix 2a's guarantee.
    assert (sessions_dir / "sess-nokey" / TRANSCRIPT_FILENAME).is_file()
    # Render still ran from the live probe's typed result fallback.
    assert summary["rendered"] is True


def test_pipeline_skips_on_empty_transcript(sessions_dir) -> None:
    """An empty transcript is reported, not crashed on."""
    state = InterviewState()  # no turns
    summary = run_offline_pipeline(
        "sess-empty", state, sessions_dir=sessions_dir
    )
    assert summary["classified"] is False
    assert any("empty transcript" in e for e in summary["errors"])


def test_finalize_can_skip_pipeline(sessions_dir) -> None:
    """run_pipeline=False closes + persists but does not run the pipeline."""
    session = FakeSession()
    state = _peopled_state()
    summary = asyncio.run(
        finalize_session(
            session, "sess-skip", state,
            sessions_dir=sessions_dir, run_pipeline=False,
        )
    )
    assert summary.get("pipeline_skipped") is True
    assert (sessions_dir / "sess-skip" / TRANSCRIPT_FILENAME).is_file()
    assert not (sessions_dir / "sess-skip" / CLASSIFICATION_FILENAME).exists()
