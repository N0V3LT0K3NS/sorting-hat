"""G14 tests — the background signal classifier (observer pattern).

No test in this file makes a live LLM call. The OpenRouter client is
always mocked or absent. The classifier is the brief's elegant part: a
parallel observer fired on each user turn that silently scores the four
template signals and nudges the InterviewState signal weights — and that
must NEVER stall or crash the interview.

Coverage:
* a well-formed model response yields four valid ``0.0 .. 1.0`` scores;
* a simulated timeout returns no-op scores WITHOUT raising;
* an exception from the LLM call returns no-op scores WITHOUT raising;
* a missing API key degrades gracefully to no-op scores;
* scores accumulate correctly into the InterviewState signal weights;
* firing the classifier as a background task does not block — the
  turn handler returns before the classifier resolves.

Every test runs fully offline.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.classifier import (
    API_KEY_ENV,
    NOOP_SCORES,
    SIGNAL_NAMES,
    apply_scores,
    classify_turn,
    get_openrouter_client,
    parse_scores,
)
from agent.interviewer import InterviewerAgent
from agent.state import InterviewState

# ---------------------------------------------------------------------------
# Helpers — a fake async OpenAI-compatible client
# ---------------------------------------------------------------------------


def _completion(content: str) -> SimpleNamespace:
    """Wrap raw text where the real SDK puts the assistant message."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _fake_client(content: str) -> MagicMock:
    """An async client whose chat completion resolves to ``content`` as text."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion(content))
    return client


def _slow_client(content: str, delay: float) -> MagicMock:
    """An async client whose chat completion resolves only after ``delay`` s."""

    async def _slow_create(*args, **kwargs):
        await asyncio.sleep(delay)
        return _completion(content)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_slow_create)
    return client


def _raising_client(exc: Exception) -> MagicMock:
    """An async client whose chat completion raises ``exc``."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=exc)
    return client


WELL_FORMED = '{"iceberg": 0.8, "two_buttons": 0.1, "compass": 0.2, "arc": 0.4}'


# ---------------------------------------------------------------------------
# parse_scores — pure parsing
# ---------------------------------------------------------------------------


def test_parse_scores_well_formed():
    """A clean JSON object parses into the four named float scores."""
    scores = parse_scores(WELL_FORMED)
    assert set(scores) == set(SIGNAL_NAMES)
    assert scores == {
        "iceberg": 0.8,
        "two_buttons": 0.1,
        "compass": 0.2,
        "arc": 0.4,
    }


def test_parse_scores_clamps_and_fills_missing():
    """Out-of-range values are clamped; missing/bad keys become 0.0."""
    scores = parse_scores('{"iceberg": 1.7, "two_buttons": -0.5, "arc": "x"}')
    assert scores["iceberg"] == 1.0  # clamped down
    assert scores["two_buttons"] == 0.0  # clamped up
    assert scores["arc"] == 0.0  # non-numeric -> 0.0
    assert scores["compass"] == 0.0  # missing key -> 0.0


def test_parse_scores_tolerates_code_fence():
    """A ```json fenced response still parses."""
    fenced = f"```json\n{WELL_FORMED}\n```"
    assert parse_scores(fenced)["iceberg"] == 0.8


def test_parse_scores_rejects_non_json():
    """Text that is not JSON raises — the caller treats it as an error."""
    with pytest.raises(ValueError):
        parse_scores("not json at all")


# ---------------------------------------------------------------------------
# classify_turn — well-formed response yields four valid scores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_turn_returns_four_valid_scores():
    """A well-formed response yields exactly the four signals, each 0..1."""
    client = _fake_client(WELL_FORMED)
    scores = await classify_turn("I keep things hidden under the surface.", client=client)

    assert set(scores) == set(SIGNAL_NAMES)
    for name in SIGNAL_NAMES:
        assert isinstance(scores[name], float)
        assert 0.0 <= scores[name] <= 1.0
    assert scores["iceberg"] == 0.8


@pytest.mark.asyncio
async def test_classify_turn_empty_response_is_noop():
    """An empty/whitespace turn short-circuits to no-op scores, no LLM call."""
    client = _fake_client(WELL_FORMED)
    scores = await classify_turn("   ", client=client)
    assert scores == NOOP_SCORES
    client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# classify_turn — a timeout returns no-op scores WITHOUT raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_turn_timeout_returns_noop_without_raising():
    """A slow LLM call is abandoned at the timeout; no-op scores, no raise."""
    # The client takes far longer than the timeout we pass.
    client = _slow_client(WELL_FORMED, delay=5.0)

    # This must NOT raise asyncio.TimeoutError into the caller.
    scores = await classify_turn(
        "A long, thoughtful answer.", client=client, timeout=0.05
    )
    assert scores == NOOP_SCORES


# ---------------------------------------------------------------------------
# classify_turn — an exception returns no-op scores WITHOUT raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_turn_llm_error_returns_noop_without_raising():
    """Any exception from the LLM call degrades to no-op, never raises."""
    client = _raising_client(RuntimeError("OpenRouter is on fire"))
    scores = await classify_turn("Something I said.", client=client)
    assert scores == NOOP_SCORES


@pytest.mark.asyncio
async def test_classify_turn_bad_json_returns_noop_without_raising():
    """A response that is not parseable JSON degrades to no-op, never raises."""
    client = _fake_client("the model rambled instead of returning JSON")
    scores = await classify_turn("Something I said.", client=client)
    assert scores == NOOP_SCORES


@pytest.mark.asyncio
async def test_classify_turn_empty_content_returns_noop_without_raising():
    """An empty assistant message degrades to no-op, never raises."""
    client = _fake_client("")
    scores = await classify_turn("Something I said.", client=client)
    assert scores == NOOP_SCORES


# ---------------------------------------------------------------------------
# classify_turn — a missing API key degrades gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_turn_missing_api_key_is_noop(monkeypatch):
    """With no OPENROUTER_API_KEY and no injected client, classify is a no-op."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    # No client passed -> the classifier must build one from the env, find
    # no key, and degrade silently rather than raise.
    scores = await classify_turn("I keep things hidden.")
    assert scores == NOOP_SCORES


def test_get_openrouter_client_returns_none_without_key(monkeypatch):
    """get_openrouter_client returns None (not a raise) when no key is set."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert get_openrouter_client() is None


def test_get_openrouter_client_builds_with_key(monkeypatch):
    """With a key present, a client is constructed (no network at build)."""
    monkeypatch.setenv(API_KEY_ENV, "test-key-not-real")
    client = get_openrouter_client()
    assert client is not None


# ---------------------------------------------------------------------------
# apply_scores — accumulation into InterviewState signal weights
# ---------------------------------------------------------------------------


def test_apply_scores_accumulates_into_state():
    """Scores add onto the matching *_signal fields of InterviewState."""
    state = InterviewState()
    assert state.iceberg_signal == 0.0

    apply_scores(state, {"iceberg": 0.8, "two_buttons": 0.1, "compass": 0.2, "arc": 0.4})
    assert state.iceberg_signal == 0.8
    assert state.two_buttons_signal == 0.1
    assert state.compass_signal == 0.2
    assert state.arc_signal == 0.4


def test_apply_scores_is_additive_across_turns():
    """Successive turns accumulate — the supervisor sees the running total."""
    state = InterviewState()
    apply_scores(state, {"iceberg": 0.3, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0})
    apply_scores(state, {"iceberg": 0.5, "two_buttons": 0.0, "compass": 0.0, "arc": 0.2})

    assert state.iceberg_signal == pytest.approx(0.8)
    assert state.arc_signal == pytest.approx(0.2)
    # The accumulated evidence makes iceberg the leading template.
    assert state.leading_template() == "iceberg"


def test_apply_scores_noop_leaves_weights_unchanged():
    """All-zero (no-op) scores leave the signal weights untouched."""
    state = InterviewState()
    state.compass_signal = 0.6
    apply_scores(state, NOOP_SCORES)
    assert state.compass_signal == 0.6
    assert state.iceberg_signal == 0.0


def test_apply_scores_never_raises_on_bad_input():
    """A malformed scores value is logged and skipped, never raised."""
    state = InterviewState()
    apply_scores(state, "not a dict")  # type: ignore[arg-type]
    apply_scores(state, {"iceberg": "bad", "nonsense_signal": 1.0})
    # State is untouched and nothing raised.
    assert state.iceberg_signal == 0.0


# ---------------------------------------------------------------------------
# Observer pattern — firing the classifier as a background task does not block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_user_turn_does_not_block(monkeypatch):
    """on_user_turn returns BEFORE the classifier resolves — it is parallel.

    This is the heart of the observer pattern: the user-turn handler fires
    the classifier as a background task and returns immediately, so the
    next agent turn is never delayed by a slow classifier.
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    state = InterviewState()
    agent = InterviewerAgent(state=state)

    # A sentinel that flips only once the classifier coroutine has run.
    resolved: list[bool] = [False]

    async def _slow_classifier(user_response, **kwargs):
        await asyncio.sleep(0.2)
        resolved[0] = True
        return {"iceberg": 0.5, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}

    monkeypatch.setattr("agent.interviewer.classify_turn", _slow_classifier)

    # Fire the observer. on_user_turn is synchronous and must return at once.
    task = agent.on_user_turn("Something I just said in the interview.")

    # The classifier has NOT resolved yet — the handler did not await it.
    assert task is not None
    assert resolved[0] is False
    assert not task.done()
    assert task in agent.classifier_tasks

    # Let the background task finish; only now does it resolve.
    await task
    assert resolved[0] is True


@pytest.mark.asyncio
async def test_on_user_turn_absorbs_scores_into_state(monkeypatch):
    """When the background task completes, its scores land in the state."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    state = InterviewState()
    agent = InterviewerAgent(state=state)

    async def _fake_classifier(user_response, **kwargs):
        return {"iceberg": 0.0, "two_buttons": 0.7, "compass": 0.0, "arc": 0.1}

    monkeypatch.setattr("agent.interviewer.classify_turn", _fake_classifier)

    task = agent.on_user_turn("I'm torn between two things I want.")
    assert task is not None
    # Not absorbed yet — the done-callback has not run.
    assert state.two_buttons_signal == 0.0

    await task
    # The done-callback fires synchronously after the task completes; give
    # the loop one tick so add_done_callback callbacks are dispatched.
    await asyncio.sleep(0)

    assert state.two_buttons_signal == pytest.approx(0.7)
    assert state.arc_signal == pytest.approx(0.1)
    assert state.leading_template() == "two_buttons"
    # The task was cleaned out of the tracking set on completion.
    assert task not in agent.classifier_tasks


@pytest.mark.asyncio
async def test_on_user_turn_empty_response_fires_nothing(monkeypatch):
    """An empty user turn fires no background task at all."""
    state = InterviewState()
    agent = InterviewerAgent(state=state)
    assert agent.on_user_turn("   ") is None
    assert agent.on_user_turn("") is None
    assert agent.classifier_tasks == ()


@pytest.mark.asyncio
async def test_classifier_task_failure_cannot_crash_session(monkeypatch):
    """A background classifier task that raises is swallowed, not propagated.

    classify_turn is built never to raise, but the observer's done-callback
    is the belt-and-braces guard: even a task that somehow raises must not
    crash the session or corrupt the signal weights.
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    state = InterviewState()
    agent = InterviewerAgent(state=state)

    async def _exploding_classifier(user_response, **kwargs):
        raise RuntimeError("classifier blew up despite its guards")

    monkeypatch.setattr("agent.interviewer.classify_turn", _exploding_classifier)

    task = agent.on_user_turn("A response that triggers a broken classifier.")
    assert task is not None

    # Awaiting the task itself surfaces the exception, but the session's
    # done-callback handles it without re-raising. Drain it defensively.
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)  # let the done-callback run

    # The session is intact: weights unchanged, task cleaned up.
    assert state.iceberg_signal == 0.0
    assert task not in agent.classifier_tasks


@pytest.mark.asyncio
async def test_aclose_classifiers_cancels_in_flight(monkeypatch):
    """Session shutdown cancels and drains any pending classifier tasks."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    state = InterviewState()
    agent = InterviewerAgent(state=state)

    async def _never_finishes(user_response, **kwargs):
        await asyncio.sleep(3600)
        return dict(NOOP_SCORES)

    monkeypatch.setattr("agent.interviewer.classify_turn", _never_finishes)

    task = agent.on_user_turn("A turn whose classifier hangs forever.")
    assert task is not None
    assert not task.done()

    await agent.aclose_classifiers()

    assert task.cancelled()
    assert agent.classifier_tasks == ()
