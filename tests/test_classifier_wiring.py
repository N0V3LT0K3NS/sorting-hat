"""FIX-4A tests — the realtime background classifier is wired onto user turns.

A confirmed live bug: the background classifier (``agent/classifier.py``)
scores every user turn and nudges the four signal weights on
:class:`InterviewState`, but nothing called :meth:`InterviewerAgent.on_user_turn`,
so after a 20-minute interview all four signals were still 0.0.

These tests prove FIX-4A's wiring in ``agent/main.py``:

* :func:`wire_classifier` registers a handler on the session's
  ``user_input_transcribed`` event;
* the handler calls ``on_user_turn`` with the FINAL transcript text;
* interim/partial transcripts do NOT fire the classifier;
* :meth:`InterviewerAgent.aclose_classifiers` is registered as a job
  shutdown callback in :func:`entrypoint`.

No network and no LiveKit room are touched — the session events and the
classifier are mocked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.interviewer import InterviewerAgent
from agent.state import InterviewState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSession:
    """A minimal AgentSession stand-in that records ``on()`` registrations.

    ``emit`` replays a captured event to every handler registered for it —
    the test driver for the session's event bus.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, cb) -> None:
        self.handlers.setdefault(event, []).append(cb)

    def emit(self, event: str, payload: object) -> None:
        for cb in self.handlers.get(event, []):
            cb(payload)


def _transcribed_event(transcript: str, *, is_final: bool):
    """Build a stand-in UserInputTranscribedEvent (transcript + is_final)."""
    return SimpleNamespace(
        type="user_input_transcribed",
        transcript=transcript,
        is_final=is_final,
    )


# ---------------------------------------------------------------------------
# wire_classifier — handler registration
# ---------------------------------------------------------------------------


def test_wire_classifier_registers_user_input_transcribed_handler() -> None:
    """wire_classifier registers a handler on the user_input_transcribed event."""
    from agent.main import wire_classifier

    agent = InterviewerAgent(state=InterviewState())
    session = FakeSession()

    wire_classifier(session, agent)

    assert "user_input_transcribed" in session.handlers
    assert len(session.handlers["user_input_transcribed"]) == 1


# ---------------------------------------------------------------------------
# Final transcript fires on_user_turn
# ---------------------------------------------------------------------------


def test_final_transcript_calls_on_user_turn_with_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FINAL transcript event calls agent.on_user_turn with the transcript."""
    from agent.main import wire_classifier

    agent = InterviewerAgent(state=InterviewState())
    session = FakeSession()
    wire_classifier(session, agent)

    seen: list[str] = []
    monkeypatch.setattr(agent, "on_user_turn", lambda text: seen.append(text))

    session.emit(
        "user_input_transcribed",
        _transcribed_event("People say I'm the calm one.", is_final=True),
    )

    assert seen == ["People say I'm the calm one."]


def test_interim_transcript_does_not_call_on_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interim/partial transcript must NOT fire the classifier."""
    from agent.main import wire_classifier

    agent = InterviewerAgent(state=InterviewState())
    session = FakeSession()
    wire_classifier(session, agent)

    seen: list[str] = []
    monkeypatch.setattr(agent, "on_user_turn", lambda text: seen.append(text))

    # Interim partials as the utterance is still being recognised.
    session.emit(
        "user_input_transcribed",
        _transcribed_event("People say", is_final=False),
    )
    session.emit(
        "user_input_transcribed",
        _transcribed_event("People say I'm the", is_final=False),
    )

    assert seen == []  # nothing fired on partials

    # The final transcript of the same turn fires exactly once.
    session.emit(
        "user_input_transcribed",
        _transcribed_event("People say I'm the calm one.", is_final=True),
    )
    assert seen == ["People say I'm the calm one."]


def test_empty_final_transcript_does_not_call_on_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/whitespace FINAL transcript is a no-op — no classifier fired."""
    from agent.main import wire_classifier

    agent = InterviewerAgent(state=InterviewState())
    session = FakeSession()
    wire_classifier(session, agent)

    seen: list[str] = []
    monkeypatch.setattr(agent, "on_user_turn", lambda text: seen.append(text))

    session.emit(
        "user_input_transcribed", _transcribed_event("   ", is_final=True)
    )

    assert seen == []


# ---------------------------------------------------------------------------
# End-to-end: a final transcript nudges the SHARED InterviewState signals
# ---------------------------------------------------------------------------


def test_final_transcript_nudges_shared_interview_state_signals() -> None:
    """A FINAL transcript scores a turn into the same InterviewState userdata.

    Mocks classify_turn so no network is touched. Proves the wiring closes the
    loop end-to-end: the score lands on the SAME InterviewState the supervisor
    routes on and session_finalize persists — so a real interview's
    interview_state.json ends with non-zero signal weights.
    """
    import agent.interviewer as interviewer_mod
    from agent.main import wire_classifier

    async def fake_classify_turn(user_response: str, **_kwargs):
        # A response that scores strongly for the iceberg signal.
        return {"iceberg": 0.7, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}

    async def drive() -> InterviewState:
        state = InterviewState()
        agent = InterviewerAgent(state=state)
        # on_user_turn calls classify_turn via the interviewer module name.
        original = interviewer_mod.classify_turn
        interviewer_mod.classify_turn = fake_classify_turn
        try:
            session = FakeSession()
            wire_classifier(session, agent)

            # Signals start at zero — the live bug's symptom.
            assert state.iceberg_signal == 0.0

            session.emit(
                "user_input_transcribed",
                _transcribed_event(
                    "On the surface I'm fine, but underneath there's a whole "
                    "hidden layer I never show anyone.",
                    is_final=True,
                ),
            )

            # on_user_turn fired a background task; let it resolve so its
            # done-callback absorbs the scores into the shared state.
            assert agent.classifier_tasks, "no background classifier task fired"
            await asyncio.gather(*agent.classifier_tasks, return_exceptions=True)
        finally:
            interviewer_mod.classify_turn = original
        return state

    state = asyncio.run(drive())

    # The classifier's score landed on the shared InterviewState.
    assert state.iceberg_signal == pytest.approx(0.7)
    assert state.leading_template() == "iceberg"


def test_interim_transcript_leaves_signals_at_zero() -> None:
    """Interim transcripts never reach classify_turn — signals stay 0.0."""
    import agent.interviewer as interviewer_mod
    from agent.main import wire_classifier

    called: list[str] = []

    async def fake_classify_turn(user_response: str, **_kwargs):
        called.append(user_response)
        return {"iceberg": 0.9, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}

    state = InterviewState()
    agent = InterviewerAgent(state=state)
    original = interviewer_mod.classify_turn
    interviewer_mod.classify_turn = fake_classify_turn
    try:
        session = FakeSession()
        wire_classifier(session, agent)
        session.emit(
            "user_input_transcribed",
            _transcribed_event("partial words here", is_final=False),
        )
    finally:
        interviewer_mod.classify_turn = original

    assert called == []  # classify_turn never invoked for a partial
    assert agent.classifier_tasks == ()
    assert state.iceberg_signal == 0.0


# ---------------------------------------------------------------------------
# aclose_classifiers is wired into job shutdown
# ---------------------------------------------------------------------------


def test_entrypoint_registers_aclose_classifiers_as_shutdown_callback() -> None:
    """entrypoint() registers agent.aclose_classifiers as a job shutdown callback.

    The entrypoint is driven with a fake JobContext: ctx.connect() raises so
    the run stops right after the wiring (before any live room work). The
    native turn detector — which needs a real job's inference executor — is
    stubbed out so the entrypoint reaches the shutdown-callback registration
    offline. The test then asserts the registered callback is the agent's
    aclose_classifiers bound method.
    """
    import agent.main as main_mod

    shutdown_callbacks: list = []
    registered_agents: list = []

    class FakeRoom:
        name = "sess-shutdown"

    class StopHere(RuntimeError):
        """Raised by the fake ctx.connect() to halt entrypoint after wiring."""

    class FakeCtx:
        def __init__(self) -> None:
            self.room = FakeRoom()

        def add_shutdown_callback(self, cb) -> None:
            shutdown_callbacks.append(cb)

        async def connect(self) -> None:
            raise StopHere

    # Capture the InterviewerAgent entrypoint builds so we can compare its
    # aclose_classifiers against what got registered.
    real_agent_cls = main_mod.InterviewerAgent

    def tracking_agent(*args, **kwargs):
        agent = real_agent_cls(*args, **kwargs)
        registered_agents.append(agent)
        return agent

    # The native turn detector needs a real job's inference executor; stub it
    # so the entrypoint reaches the shutdown-callback registration offline.
    real_english_model = main_mod.EnglishModel

    main_mod.InterviewerAgent = tracking_agent  # type: ignore[misc]
    main_mod.EnglishModel = lambda *a, **k: object()  # type: ignore[misc]
    try:
        with pytest.raises(StopHere):
            asyncio.run(main_mod.entrypoint(FakeCtx()))
    finally:
        main_mod.InterviewerAgent = real_agent_cls  # type: ignore[misc]
        main_mod.EnglishModel = real_english_model  # type: ignore[misc]

    assert registered_agents, "entrypoint did not build an InterviewerAgent"
    agent = registered_agents[0]

    # aclose_classifiers (a bound method of the built agent) was registered.
    assert any(
        getattr(cb, "__func__", None) is real_agent_cls.aclose_classifiers
        and getattr(cb, "__self__", None) is agent
        for cb in shutdown_callbacks
    ), "agent.aclose_classifiers was not registered as a shutdown callback"


def test_aclose_classifiers_drains_in_flight_tasks() -> None:
    """The wired shutdown callback cancels/drains in-flight classifier tasks."""
    import agent.interviewer as interviewer_mod

    async def slow_classify_turn(user_response: str, **_kwargs):
        # A classifier still waiting on a slow LLM hop at shutdown time.
        await asyncio.sleep(10)
        return {"iceberg": 0.0, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}

    async def drive() -> InterviewerAgent:
        agent = InterviewerAgent(state=InterviewState())
        original = interviewer_mod.classify_turn
        interviewer_mod.classify_turn = slow_classify_turn
        try:
            agent.on_user_turn("a turn whose classifier is still in flight")
            assert agent.classifier_tasks, "no background task to drain"
            # The shutdown seam — what entrypoint registers.
            await agent.aclose_classifiers()
        finally:
            interviewer_mod.classify_turn = original
        return agent

    agent = asyncio.run(drive())

    # All tracked tasks were drained — the tracking set is empty.
    assert agent.classifier_tasks == ()
