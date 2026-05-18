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

from agent.interviewer import (
    BASE_QUESTION_COUNT,
    INTERVIEWEE_TURNS_PER_BASE_QUESTION,
    InterviewerAgent,
    ProbeOutcome,
)
from agent.session_finalize import (
    CLASSIFICATION_FILENAME,
    IN_PROGRESS_STAGES,
    LIVE_STATE_FILENAME,
    RESULT_FILENAME,
    STATE_FILENAME,
    STATUS_FILENAME,
    TERMINAL_STAGES,
    TRANSCRIPT_FILENAME,
    finalize_session,
    persist_transcript,
    run_offline_pipeline,
    session_dir,
    write_interrupted_status,
    write_live_state,
    write_status,
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
# 1b. The bug fix — an EARLY / forced close still runs the offline pipeline
# ---------------------------------------------------------------------------


def test_finalize_on_close_runs_pipeline_when_routing_not_done(
    sessions_dir, monkeypatch
) -> None:
    """An early End (routing_done False) still triggers the offline pipeline.

    The bug: the per-turn ``_maybe_finalize`` only fires once the supervisor's
    routing has settled. A visitor who presses End before then closes the
    session with a transcript on disk but the pipeline never run. The
    ``finalize_on_close`` shutdown callback closes that gap.
    """
    _mock_pipeline(monkeypatch)
    from agent.main import wire_session_finalize

    state = _peopled_state()
    # An early end: base questions NOT complete, routing NOT settled. Wipe the
    # natural-completion signal so routing_done is unambiguously False.
    state.chosen_template = None
    agent = InterviewerAgent(state=state)
    assert agent.routing_done is False

    session = FakeSession()
    session.on = lambda event, cb: None  # type: ignore

    finalize_on_close = wire_session_finalize(
        session, agent, state, "sess-early-end"
    )
    asyncio.run(finalize_on_close())

    # The session was closed and the pipeline ran on the thin transcript.
    assert session.closed is True
    folder = sessions_dir / "sess-early-end"
    assert (folder / TRANSCRIPT_FILENAME).is_file()
    assert (folder / CLASSIFICATION_FILENAME).is_file()
    assert (folder / "portrait.png").is_file()
    # status.json is written so the kiosk Complete screen resolves instead of
    # timing out.
    assert (folder / STATUS_FILENAME).is_file()
    assert json.loads((folder / STATUS_FILENAME).read_text())["stage"] == "done"


def test_finalize_on_close_writes_status_on_early_end(
    sessions_dir, monkeypatch
) -> None:
    """An early end leaves a status.json — the kiosk Complete screen resolves."""
    _mock_pipeline(monkeypatch)
    from agent.main import wire_session_finalize

    state = _peopled_state()
    state.chosen_template = None
    agent = InterviewerAgent(state=state)
    session = FakeSession()
    session.on = lambda event, cb: None  # type: ignore

    finalize_on_close = wire_session_finalize(
        session, agent, state, "sess-early-status"
    )
    asyncio.run(finalize_on_close())

    status = json.loads(
        (sessions_dir / "sess-early-status" / STATUS_FILENAME).read_text()
    )
    assert status["session_id"] == "sess-early-status"
    # A terminal stage — the kiosk's poll resolves rather than timing out.
    assert status["stage"] in {"done", "error"}


def test_finalize_on_close_is_noop_after_natural_completion(
    sessions_dir, monkeypatch
) -> None:
    """The pipeline never runs twice — the close callback shares the guard.

    When a natural completion has already finalized the session, the
    ``finalize_on_close`` shutdown callback must be a no-op: the offline
    pipeline runs exactly once per session.
    """
    from agent.main import wire_session_finalize

    pipeline_runs: list[str] = []

    async def counting_finalize(session, session_id, state, **kwargs):
        pipeline_runs.append(session_id)
        await session.aclose()
        return {"session_id": session_id}

    monkeypatch.setattr("agent.main.finalize_session", counting_finalize)

    # Routing settled — a natural completion.
    state = _peopled_state()
    agent = InterviewerAgent(state=state)
    assert agent.routing_done is True

    session = FakeSession()
    captured: list = []
    session.on = lambda event, cb: captured.append(cb)  # type: ignore

    finalize_on_close = wire_session_finalize(
        session, agent, state, "sess-no-double"
    )
    handler = captured[0]

    class FakeItem:
        def __init__(self, role: str, text: str) -> None:
            self.role = role
            self.text_content = text

    class FakeEvent:
        def __init__(self, item: object) -> None:
            self.item = item

    async def drive() -> None:
        # A turn fires the natural-completion finalize (routing already done).
        handler(FakeEvent(FakeItem("user", "An answer.")))
        for _ in range(3):
            await asyncio.sleep(0)
        # The shutdown callback then runs — it must NOT finalize again.
        await finalize_on_close()
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(drive())

    # Exactly one pipeline run, despite both seams firing.
    assert pipeline_runs == ["sess-no-double"]


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


def test_wired_handler_drives_supervisor_turn_progress_and_routes_once(
    sessions_dir, monkeypatch
) -> None:
    """The live conversation observer advances state and routes without tools."""
    from agent import main as main_mod

    route_log: list[str] = []
    finalize_calls: list[str] = []

    async def fake_iceberg_runner() -> ProbeOutcome:
        route_log.append("iceberg")
        return ProbeOutcome(
            template="iceberg",
            result=IcebergResult(
                surface="The calm one.",
                first_layer="Privately carrying the room.",
                second_layer="Unsure where usefulness ends.",
                abyss="Afraid there is no self beneath it.",
            ),
            thin=False,
        )

    async def fake_finalize(session, session_id, state):
        finalize_calls.append(session_id)
        await session.aclose()
        return {"pipeline_skipped": True}

    monkeypatch.setattr(main_mod, "finalize_session", fake_finalize)

    async def run_wired_turns() -> tuple[InterviewState, FakeSession]:
        state = InterviewState()
        agent = InterviewerAgent(
            state=state,
            probe_runners={"iceberg": fake_iceberg_runner},
        )
        session = FakeSession()
        captured: list = []
        session.on = lambda event, cb: captured.append(cb)  # type: ignore
        main_mod.wire_session_finalize(session, agent, state, "sess-supervised")
        handler = captured[0]

        class FakeItem:
            def __init__(self, role: str, text: str) -> None:
                self.role = role
                self.text_content = text

        class FakeEvent:
            def __init__(self, item: object) -> None:
                self.item = item

        turns_needed = INTERVIEWEE_TURNS_PER_BASE_QUESTION * BASE_QUESTION_COUNT
        for idx in range(turns_needed):
            handler(FakeEvent(FakeItem("user", f"Interviewee answer {idx}.")))

        for _ in range(3):
            await asyncio.sleep(0)

        # A later turn must not route or finalize a second time.
        handler(FakeEvent(FakeItem("user", "One more answer after routing.")))
        for _ in range(3):
            await asyncio.sleep(0)

        assert agent.base_questions_completed == BASE_QUESTION_COUNT
        assert agent.routing_done is True
        return state, session

    state, session = asyncio.run(run_wired_turns())

    assert route_log == ["iceberg"]
    assert state.chosen_template == "iceberg"
    assert isinstance(state.iceberg_result, IcebergResult)
    assert finalize_calls == ["sess-supervised"]
    assert session.closed is True

    saved_state = json.loads(
        (sessions_dir / "sess-supervised" / STATE_FILENAME).read_text()
    )
    assert saved_state["base_questions_completed"] == BASE_QUESTION_COUNT
    assert saved_state["chosen_template"] == "iceberg"


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


# ---------------------------------------------------------------------------
# 5. status.json — the delivery server's pipeline-progress file
# ---------------------------------------------------------------------------


def _read_status(folder) -> dict:
    return json.loads((folder / STATUS_FILENAME).read_text())


def test_write_status_writes_the_stage(sessions_dir) -> None:
    """write_status drops a status.json carrying the named stage."""
    write_status("sess-st", "rendering", sessions_dir=sessions_dir)
    status = _read_status(sessions_dir / "sess-st")
    assert status["session_id"] == "sess-st"
    assert status["stage"] == "rendering"
    assert status["error"] is None


def test_pipeline_status_ends_done(sessions_dir, monkeypatch) -> None:
    """A full pipeline run finishes with status.json at stage 'done'."""
    _mock_pipeline(monkeypatch)
    state = _peopled_state()
    run_offline_pipeline("sess-st-done", state, sessions_dir=sessions_dir)
    status = _read_status(sessions_dir / "sess-st-done")
    assert status["stage"] == "done"
    assert status["error"] is None


def test_pipeline_writes_status_at_each_stage(sessions_dir, monkeypatch) -> None:
    """run_offline_pipeline rewrites status.json at every stage boundary.

    write_status is wrapped so the sequence of stages it was called with is
    captured — proving classifying -> filling -> rendering -> delivering ->
    done all fire, in order.
    """
    _mock_pipeline(monkeypatch)
    from agent import session_finalize as sf

    seen: list[str] = []
    real_write_status = sf.write_status

    def spy(session_id, stage, **kwargs):
        seen.append(stage)
        return real_write_status(session_id, stage, **kwargs)

    monkeypatch.setattr(sf, "write_status", spy)

    state = _peopled_state()
    run_offline_pipeline("sess-stages", state, sessions_dir=sessions_dir)

    assert seen == ["classifying", "filling", "rendering", "delivering", "done"]


def test_pipeline_status_records_error_stage(sessions_dir, monkeypatch) -> None:
    """A render failure leaves status.json at stage 'error' with a message."""
    _mock_pipeline(monkeypatch)
    from pipeline import render as render_mod

    def boom_render(*args, **kwargs):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(render_mod, "render", boom_render)

    state = _peopled_state()
    run_offline_pipeline("sess-st-err", state, sessions_dir=sessions_dir)

    status = _read_status(sessions_dir / "sess-st-err")
    assert status["stage"] == "error"
    assert "render" in (status["error"] or "")


def test_empty_transcript_writes_error_status(sessions_dir) -> None:
    """An empty transcript is reported via status.json as stage 'error'."""
    state = InterviewState()  # no turns
    run_offline_pipeline("sess-st-empty", state, sessions_dir=sessions_dir)
    status = _read_status(sessions_dir / "sess-st-empty")
    assert status["stage"] == "error"
    assert "empty transcript" in (status["error"] or "")


# ---------------------------------------------------------------------------
# 6. live_state.json — the delivery server's during-interview state file
# ---------------------------------------------------------------------------


def _read_live_state(folder) -> dict:
    return json.loads((folder / LIVE_STATE_FILENAME).read_text())


def test_write_live_state_base_questions_phase(sessions_dir) -> None:
    """Mid base interview: phase 'base_questions', signals + counts surfaced."""
    state = InterviewState(
        iceberg_signal=0.7, two_buttons_signal=0.2, base_questions_completed=2
    )
    state.record_turn("interviewer", "Question one.")
    state.record_turn("interviewee", "Answer one.")

    write_live_state("sess-live-base", state, sessions_dir=sessions_dir)
    live = _read_live_state(sessions_dir / "sess-live-base")

    assert live["session_id"] == "sess-live-base"
    assert live["phase"] == "base_questions"
    assert live["base_questions_completed"] == 2
    assert live["base_questions_total"] == BASE_QUESTION_COUNT
    assert live["signals"]["iceberg"] == 0.7
    assert live["signals"]["two_buttons"] == 0.2
    assert live["leading_template"] == "iceberg"
    assert live["chosen_template"] is None
    assert live["routing_done"] is False
    assert live["turn_count"] == 2
    assert live["updated_at"]


def test_write_live_state_probing_phase(sessions_dir) -> None:
    """Base questions done, routing not settled -> phase 'probing'."""
    state = InterviewState(
        compass_signal=0.5, base_questions_completed=BASE_QUESTION_COUNT
    )
    write_live_state("sess-live-probe", state, sessions_dir=sessions_dir)
    live = _read_live_state(sessions_dir / "sess-live-probe")
    assert live["phase"] == "probing"
    assert live["routing_done"] is False


def test_write_live_state_complete_phase(sessions_dir) -> None:
    """routing_done -> phase 'complete', chosen_template carried through."""
    state = InterviewState(
        arc_signal=0.9, base_questions_completed=BASE_QUESTION_COUNT
    )
    state.chosen_template = "arc"
    write_live_state(
        "sess-live-done", state, routing_done=True, sessions_dir=sessions_dir
    )
    live = _read_live_state(sessions_dir / "sess-live-done")
    assert live["phase"] == "complete"
    assert live["routing_done"] is True
    assert live["chosen_template"] == "arc"


def test_write_live_state_is_idempotent_rewrite(sessions_dir) -> None:
    """Calling write_live_state again reflects the latest mutated state."""
    state = InterviewState(iceberg_signal=0.1)
    write_live_state("sess-live-rw", state, sessions_dir=sessions_dir)
    first = _read_live_state(sessions_dir / "sess-live-rw")
    assert first["signals"]["iceberg"] == 0.1

    state.iceberg_signal = 0.8
    state.base_questions_completed = 3
    write_live_state("sess-live-rw", state, sessions_dir=sessions_dir)
    second = _read_live_state(sessions_dir / "sess-live-rw")
    assert second["signals"]["iceberg"] == 0.8
    assert second["base_questions_completed"] == 3


def test_wired_hook_writes_live_state_each_turn(sessions_dir) -> None:
    """The worker's per-turn hook writes live_state.json with the right shape."""
    from agent.main import wire_session_finalize

    state = InterviewState(two_buttons_signal=0.6)
    agent = InterviewerAgent(state=state)
    session = FakeSession()
    captured: list = []
    session.on = lambda event, cb: captured.append(cb)  # type: ignore

    wire_session_finalize(session, agent, state, "sess-live-wired")
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

    live = _read_live_state(sessions_dir / "sess-live-wired")
    assert live["phase"] == "base_questions"
    assert live["signals"]["two_buttons"] == 0.6
    assert live["turn_count"] == 2
    assert live["base_questions_total"] == BASE_QUESTION_COUNT


# ---------------------------------------------------------------------------
# 7. Durability — an interrupted pipeline never leaves a session silently stuck
# ---------------------------------------------------------------------------
#
# The live bug: the pipeline ran classify, advanced status.json to "filling",
# then the worker PROCESS STOPPED mid-fill. status.json froze at
# {"stage": "filling", "error": null} forever — a soft failure the kiosk
# polls indefinitely. These tests prove status.json reaches an honest terminal
# state whatever interrupts the run.


def test_write_status_stamps_updated_at(sessions_dir) -> None:
    """Every status.json write carries an updated_at timestamp.

    The delivery server's stale-run detection ages the file off this field, so
    it must always be present.
    """
    write_status("sess-ts", "filling", sessions_dir=sessions_dir)
    status = _read_status(sessions_dir / "sess-ts")
    assert status["updated_at"]
    # An ISO-8601 string that parses.
    from datetime import datetime

    datetime.fromisoformat(status["updated_at"])


def test_write_interrupted_status_marks_a_frozen_in_progress_status(
    sessions_dir,
) -> None:
    """A status.json frozen at an in-progress stage is rewritten as 'error'.

    Simulates the live bug directly: status.json says "filling" with error
    null (the worker died after write_status("filling") but before fill
    finished). write_interrupted_status must turn it into a terminal error.
    """
    write_status("sess-frozen", "filling", sessions_dir=sessions_dir)
    pre = _read_status(sessions_dir / "sess-frozen")
    assert pre["stage"] == "filling" and pre["error"] is None

    written = write_interrupted_status("sess-frozen", sessions_dir=sessions_dir)
    assert written is not None

    post = _read_status(sessions_dir / "sess-frozen")
    assert post["stage"] == "error"
    assert post["error"]  # non-null — explains the run did not complete
    assert "filling" in post["error"]


def test_write_interrupted_status_leaves_a_terminal_status_alone(
    sessions_dir,
) -> None:
    """A status.json already at 'done' or 'error' is not touched."""
    write_status("sess-done", "done", sessions_dir=sessions_dir)
    assert write_interrupted_status("sess-done", sessions_dir=sessions_dir) is None
    assert _read_status(sessions_dir / "sess-done")["stage"] == "done"

    write_status("sess-already-err", "error", sessions_dir=sessions_dir,
                 error="render: original failure")
    assert (
        write_interrupted_status("sess-already-err", sessions_dir=sessions_dir)
        is None
    )
    # The original error message survives — the backstop did not clobber it.
    assert (
        _read_status(sessions_dir / "sess-already-err")["error"]
        == "render: original failure"
    )


def test_write_interrupted_status_noop_when_no_status_file(sessions_dir) -> None:
    """No status.json at all -> nothing written (the pipeline never started)."""
    assert (
        write_interrupted_status("sess-never-ran", sessions_dir=sessions_dir)
        is None
    )
    assert not (sessions_dir / "sess-never-ran" / STATUS_FILENAME).exists()


def test_pipeline_finally_marks_status_when_inner_is_interrupted(
    sessions_dir, monkeypatch
) -> None:
    """A pipeline interrupted mid-stage ends with a terminal error status.

    Simulates the worker process being killed mid-fill: the fill stage raises
    a BaseException (SystemExit — what process termination raises), which the
    stage's ``except Exception`` does NOT catch, so it propagates out of the
    inner pipeline body. run_offline_pipeline's try/finally backstop must then
    rewrite status.json — frozen at "filling" — as a terminal 'error'.
    """
    from pipeline import classify as classify_mod
    from pipeline import fill as fill_mod
    from pipeline.classify import ClassificationResult

    def fake_classify(transcript_xml, **kwargs):
        return ClassificationResult(
            template="iceberg", confidence=0.9, reasoning="clear."
        )

    def killed_fill(*args, **kwargs):
        # Process termination — not an ordinary Exception, so the stage's
        # ``except Exception`` does not swallow it.
        raise SystemExit("worker process terminated mid-fill")

    monkeypatch.setattr(classify_mod, "classify", fake_classify)
    monkeypatch.setattr(fill_mod, "fill", killed_fill)

    state = _peopled_state()
    # The interruption propagates; run_offline_pipeline's finally still runs.
    with pytest.raises(SystemExit):
        run_offline_pipeline("sess-killed", state, sessions_dir=sessions_dir)

    # status.json is NOT frozen at "filling" — the backstop made it terminal.
    status = _read_status(sessions_dir / "sess-killed")
    assert status["stage"] == "error"
    assert status["error"]  # non-null message
    assert status["stage"] not in IN_PROGRESS_STAGES


def test_pipeline_never_ends_in_progress_with_null_error(
    sessions_dir, monkeypatch
) -> None:
    """After any run ends, status.json is never in-progress with error null.

    The contract Fix 1 guarantees: a frozen {"stage": "filling", "error": null}
    is exactly the soft failure. Whatever path the run takes — clean, a caught
    stage failure, or a hard interruption — the final status.json is either a
    terminal stage or carries a non-null error.
    """
    from pipeline import classify as classify_mod
    from pipeline import fill as fill_mod
    from pipeline.classify import ClassificationResult

    def fake_classify(transcript_xml, **kwargs):
        return ClassificationResult(
            template="iceberg", confidence=0.9, reasoning="clear."
        )

    def killed_fill(*args, **kwargs):
        raise KeyboardInterrupt("ctrl-c mid-fill")

    monkeypatch.setattr(classify_mod, "classify", fake_classify)
    monkeypatch.setattr(fill_mod, "fill", killed_fill)

    state = _peopled_state()
    with pytest.raises(KeyboardInterrupt):
        run_offline_pipeline("sess-no-lie", state, sessions_dir=sessions_dir)

    status = _read_status(sessions_dir / "sess-no-lie")
    # The soft failure must not survive: not in-progress, or error non-null.
    assert not (status["stage"] in IN_PROGRESS_STAGES and status["error"] is None)
    assert status["stage"] in TERMINAL_STAGES


def test_clean_pipeline_run_leaves_a_done_status_untouched(
    sessions_dir, monkeypatch
) -> None:
    """The backstop is a no-op for a clean run — status.json stays 'done'.

    The interruption guard's finally calls write_interrupted_status on every
    run; a run that reached 'done' is already terminal, so it is left alone.
    """
    _mock_pipeline(monkeypatch)
    state = _peopled_state()
    summary = run_offline_pipeline("sess-clean", state, sessions_dir=sessions_dir)
    assert summary["delivered"] is True
    status = _read_status(sessions_dir / "sess-clean")
    assert status["stage"] == "done"
    assert status["error"] is None
