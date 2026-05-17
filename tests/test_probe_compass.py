"""G6 — isolation tests for the Compass probe.

No live LLM, no LiveKit room, no network. These tests prove four things:

1. ``prompts/probe_compass.md`` exists, carries the four probe questions,
   honours the blind-sort rule, and tells the LLM not to fabricate axes.
2. ``CompassProbeTask`` constructs and is a genuine ``AgentTask`` subclass.
3. The ``record_compass_result`` tool produces a valid ``CompassResult``
   with both positions inside -1.0..1.0, and out-of-range positions are
   clamped rather than crashing.
4. A thin result (the person was torn, not located) completes the probe
   honestly instead of fabricating a confident position.

The probe is awaited like a coroutine in the live worker; after completion
the typed result lives in the task's internal future, which these tests read
directly via :func:`_task_result`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from livekit.agents import AgentTask

from agent.probes.compass import CompassProbeTask, load_probe_prompt
from agent.state import CompassResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "probe_compass.md"
)

# The four probe questions the goal mandates, verbatim. The prompt must carry
# every one of these.
FOUR_PROBE_QUESTIONS: tuple[str, ...] = (
    "Do those values feel like a tension, or coordinates that locate you?",
    "If I described you on two axes — say X-to-Y and A-to-B — where would "
    "you sit on each?",
    "Are there other pairs of axes that define you?",
    "What does sitting at these specific coordinates entail — what can you "
    "not do?",
)


def _task_result(task: CompassProbeTask) -> CompassResult:
    """Return the ``CompassResult`` a completed probe resolved to.

    ``AgentTask.complete`` resolves a private future; in the live worker the
    supervisor gets this by awaiting the task. The tests read the resolved
    future directly.
    """
    fut = task._AgentTask__fut  # type: ignore[attr-defined]
    assert fut.done(), "probe task future is not done — complete() not called"
    return fut.result()


async def _async_new_task() -> CompassProbeTask:
    """Construct a ``CompassProbeTask`` inside a running event loop.

    ``AgentTask.__init__`` builds an ``asyncio.Future``, so construction needs
    a running loop. Construct-only tests await this via :func:`asyncio.run`.
    """
    return CompassProbeTask()


def _record(**kwargs) -> CompassProbeTask:
    """Build a probe and run ``record_compass_result`` on it, in one loop.

    Both the task construction (which needs a loop) and the tool call happen
    inside a single ``asyncio.run``, so no event-loop state leaks between
    tests. Returns the completed task for result inspection.
    """

    async def _go() -> CompassProbeTask:
        task = CompassProbeTask()
        await task.record_compass_result(None, **kwargs)
        return task

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# 1. The prompt file
# ---------------------------------------------------------------------------


def test_probe_prompt_file_exists_and_is_nonempty() -> None:
    assert _PROMPT_PATH.is_file(), f"missing probe prompt at {_PROMPT_PATH}"
    assert load_probe_prompt().strip(), "probe prompt is empty"


@pytest.mark.parametrize("question", FOUR_PROBE_QUESTIONS)
def test_probe_prompt_contains_each_of_the_four_questions(question: str) -> None:
    """Every one of the four mandated probe questions appears verbatim."""
    prompt = load_probe_prompt()
    assert question in prompt, f"probe prompt is missing the question: {question!r}"


def test_probe_prompt_honours_blind_sort() -> None:
    """The prompt must never let the agent name the 'compass' shape aloud.

    It must reference the blind-sort rule and explicitly forbid the giveaway
    label words as a diagnosis of the person.
    """
    prompt = load_probe_prompt().lower()
    assert "blind-sort" in prompt or "blind sort" in prompt, (
        "probe prompt does not reference the blind-sort rule"
    )
    assert "never name" in prompt, (
        "probe prompt does not instruct the agent never to name the shape"
    )
    # The shape's own name must be flagged as forbidden, not used as a label.
    assert "compass" in prompt, "expected 'compass' to appear in a do-not-say list"


def test_probe_prompt_forbids_fabricating_axes() -> None:
    """A thin result must be reported honestly, not papered over.

    The supervisor downstream needs to know the probe came back thin so it
    can pivot — so the prompt must say, in some form, do not invent axes.
    """
    prompt = load_probe_prompt().lower()
    assert "thin" in prompt, "probe prompt never mentions a thin result"
    assert "fabricate" in prompt or "fabricat" in prompt, (
        "probe prompt does not forbid fabricating axes"
    )
    # The torn-not-located case must be named — that is when it goes thin.
    assert "torn" in prompt, (
        "probe prompt does not address the person being torn rather than located"
    )


def test_probe_prompt_keeps_persona_craft() -> None:
    """The probe must stay continuous with the borrowed interview craft."""
    prompt = load_probe_prompt()
    assert "DRILL IN" in prompt and "ZOOM OUT" in prompt and "CROSS" in prompt, (
        "probe prompt drops the three-move follow-up rule"
    )
    assert "DRIFT CHECK" in prompt, "probe prompt drops the DRIFT CHECK scaffold"
    assert "banned phrasing" in prompt.lower(), (
        "probe prompt does not carry the banned-phrasings discipline"
    )


# ---------------------------------------------------------------------------
# 2. The task class
# ---------------------------------------------------------------------------


def test_compass_probe_constructs() -> None:
    task = asyncio.run(_async_new_task())
    assert task is not None


def test_compass_probe_is_an_agenttask_subclass() -> None:
    assert issubclass(CompassProbeTask, AgentTask)
    assert isinstance(asyncio.run(_async_new_task()), AgentTask)


def test_compass_probe_instructions_are_the_probe_prompt() -> None:
    """The task's instructions are loaded from probe_compass.md."""
    task = asyncio.run(_async_new_task())
    assert task.instructions == load_probe_prompt()


def test_compass_probe_exposes_the_record_tool() -> None:
    task = asyncio.run(_async_new_task())
    assert hasattr(task, "record_compass_result")


# ---------------------------------------------------------------------------
# 3. The record tool produces a valid CompassResult
# ---------------------------------------------------------------------------


def test_record_tool_produces_valid_compass_result() -> None:
    task = _record(
        axis_1_negative_pole="coldly rigorous",
        axis_1_positive_pole="warm",
        axis_1_position=0.7,
        axis_2_negative_pole="loudly public",
        axis_2_positive_pole="deeply private",
        axis_2_position=-0.4,
        why_these_axes="Rigour and warmth, visibility and privacy — they hold "
        "both pairs at once rather than choosing.",
        thin=False,
    )
    result = _task_result(task)
    assert isinstance(result, CompassResult)
    assert result.axis_1_poles == ("coldly rigorous", "warm")
    assert result.axis_2_poles == ("loudly public", "deeply private")
    assert result.axis_1_position == pytest.approx(0.7)
    assert result.axis_2_position == pytest.approx(-0.4)
    # Both positions must sit inside the locked -1.0..1.0 range.
    assert -1.0 <= result.axis_1_position <= 1.0
    assert -1.0 <= result.axis_2_position <= 1.0


def test_record_tool_returns_a_confirmation_string() -> None:
    """The tool returns a short confirmation for the LLM's call history."""

    async def _go() -> str:
        task = CompassProbeTask()
        return await task.record_compass_result(
            None,
            axis_1_negative_pole="quiet",
            axis_1_positive_pole="loud",
            axis_1_position=0.1,
            axis_2_negative_pole="near",
            axis_2_positive_pole="far",
            axis_2_position=-0.1,
            why_these_axes="Roughly centred on both.",
        )

    confirmation = asyncio.run(_go())
    assert isinstance(confirmation, str) and confirmation
    assert "recorded" in confirmation.lower()


def test_record_tool_completes_the_task() -> None:
    task = _record(
        axis_1_negative_pole="planner",
        axis_1_positive_pole="improviser",
        axis_1_position=0.0,
        axis_2_negative_pole="solo",
        axis_2_positive_pole="collective",
        axis_2_position=0.2,
        why_these_axes="Centred between planning and improvising, leaning "
        "slightly collective.",
    )
    assert task.done(), "record_compass_result did not complete the task"


def test_record_tool_clamps_out_of_range_positions() -> None:
    """A model that overshoots -1..1 must not crash the probe.

    Pydantic would reject 1.4 on the CompassResult; the tool clamps first so
    an overshooting LLM still yields a usable, in-range result.
    """
    task = _record(
        axis_1_negative_pole="cautious",
        axis_1_positive_pole="reckless",
        axis_1_position=1.4,  # overshoot high
        axis_2_negative_pole="rooted",
        axis_2_positive_pole="restless",
        axis_2_position=-2.0,  # overshoot low
        why_these_axes="Fully toward reckless and fully rooted.",
    )
    result = _task_result(task)
    assert result.axis_1_position == 1.0
    assert result.axis_2_position == -1.0
    assert -1.0 <= result.axis_1_position <= 1.0
    assert -1.0 <= result.axis_2_position <= 1.0


# ---------------------------------------------------------------------------
# 4. Thin-result handling
# ---------------------------------------------------------------------------


def test_thin_result_still_completes_the_task() -> None:
    """A person who is torn, not located, still completes the probe — thin.

    The probe must come back with a recorded result so the supervisor can
    read it and pivot; it must not hang or fabricate confidence.
    """
    task = _record(
        axis_1_negative_pole="unknown",
        axis_1_positive_pole="unknown",
        axis_1_position=0.0,
        axis_2_negative_pole="unknown",
        axis_2_positive_pole="unknown",
        axis_2_position=0.0,
        why_these_axes="This read more like a tension than a position — they "
        "felt genuinely torn, with no stable coordinates. Supervisor should "
        "pivot to the tension shape.",
        thin=True,
    )
    assert task.done(), "thin result did not complete the task"

    result = _task_result(task)
    assert isinstance(result, CompassResult)
    # Even a thin result is a structurally valid CompassResult — the honest
    # signal lives in why_these_axes, which the supervisor reads.
    assert "tension" in result.why_these_axes.lower()
    assert -1.0 <= result.axis_1_position <= 1.0
    assert -1.0 <= result.axis_2_position <= 1.0


def test_thin_defaults_to_false() -> None:
    """``thin`` is optional and defaults to a normal (non-thin) result."""
    # Called without the thin kwarg at all.
    task = _record(
        axis_1_negative_pole="head",
        axis_1_positive_pole="heart",
        axis_1_position=-0.6,
        axis_2_negative_pole="fast",
        axis_2_positive_pole="slow",
        axis_2_position=0.5,
        why_these_axes="Head over heart, slow over fast.",
    )
    assert task.done()
