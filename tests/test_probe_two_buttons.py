"""G5 tests — the Two Buttons probe AgentTask.

Offline only: NO live LLM, no LiveKit room, no network. These prove the
static contract of G5:

1. ``prompts/probe_two_buttons.md`` exists, carries the four probe questions,
   honors the blind-sort rule, and instructs thin-result honesty.
2. ``TwoButtonsProbeTask`` constructs and is an ``AgentTask`` subclass.
3. The ``record_two_buttons_result`` tool produces a valid
   ``TwoButtonsResult`` and completes the task.
4. Thin-result handling works — the probe can report no real tension.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from livekit.agents import AgentTask
from livekit.agents.llm.tool_context import get_function_info
from pydantic import ValidationError

from agent.probes.two_buttons import TwoButtonsProbeTask, load_probe_prompt
from agent.state import TwoButtonsResult

# Repo root: tests/ -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _REPO_ROOT / "prompts" / "probe_two_buttons.md"

# The four probe questions, by the brief. The probe prompt phrases them
# warmly rather than verbatim, so the tests assert the *substance* of each
# question — the distinctive content the prompt must instruct toward.
_PROBE_QUESTION_MARKERS = (
    # Q1 — first pull at full seduction.
    ("first pull at full seduction", ("first", "seduct")),
    # Q2 — second pull, also seductive, not a compromise.
    ("second pull, not a compromise", ("second", "compromise")),
    # Q3 — forced choice, one forever.
    ("forced choice forever", ("forever",)),
    # Q4 — why can't you pick / peace with one choice.
    ("the impossibility / peace", ("can't", "peace")),
)


def _completed_result(task: TwoButtonsProbeTask) -> TwoButtonsResult:
    """Return the typed result a completed probe task resolved to."""
    fut = task._AgentTask__fut  # type: ignore[attr-defined]
    assert fut.done(), "task future is not resolved"
    return fut.result()


# ---------------------------------------------------------------------------
# probe_two_buttons.md — existence and content
# ---------------------------------------------------------------------------


def test_probe_prompt_file_exists() -> None:
    """prompts/probe_two_buttons.md must exist and be non-empty."""
    assert _PROMPT_PATH.is_file(), f"missing probe prompt at {_PROMPT_PATH}"
    assert _PROMPT_PATH.read_text(encoding="utf-8").strip(), "probe prompt is empty"


def test_load_probe_prompt_returns_content() -> None:
    """load_probe_prompt() returns the non-empty prompt text."""
    prompt = load_probe_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200


def test_probe_prompt_covers_all_four_probe_questions() -> None:
    """The prompt instructs toward each of the four probe questions."""
    low = load_probe_prompt().lower()
    for label, markers in _PROBE_QUESTION_MARKERS:
        for marker in markers:
            assert marker in low, (
                f"probe prompt is missing the {label!r} probe question "
                f"(no marker {marker!r})"
            )


def test_probe_prompt_probes_both_pulls_and_impossibility() -> None:
    """The prompt names both pulls and the impossibility explicitly."""
    low = load_probe_prompt().lower()
    assert "first pull" in low
    assert "second pull" in low
    assert "impossibility" in low


def test_probe_prompt_honors_blind_sort() -> None:
    """The probe must never name the taxonomy to the user.

    'two buttons', 'tension', 'template', 'category' must appear only inside
    blind-sort/forbidding language — never as something to say to the person.
    """
    prompt = load_probe_prompt()
    low = prompt.lower()
    assert "blind-sort" in low or "blind sort" in low, (
        "probe prompt must restate the blind-sort rule"
    )
    # 'two buttons' must be named as forbidden, not used as a label.
    assert "two buttons" in low, "probe must name 'two buttons' as forbidden"
    # Every occurrence of the literal taxonomy label 'two buttons' must sit
    # near forbidding language — it is never offered as a thing to say.
    idx = 0
    while True:
        idx = low.find("two buttons", idx)
        if idx == -1:
            break
        window = low[max(0, idx - 240) : idx + 240]
        assert any(
            marker in window
            for marker in (
                "never",
                "do not",
                "don't",
                "forbidden",
                "blind",
                "not name",
                "hidden",
            )
        ), "'two buttons' appears without a forbidding context"
        idx += len("two buttons")


def test_probe_prompt_never_names_two_buttons_as_a_user_label() -> None:
    """The literal phrase is never offered as a thing to call the person."""
    low = load_probe_prompt().lower()
    # No instruction like "tell them ... two buttons".
    assert "you are a two buttons" not in low
    assert 'call them "two buttons"' not in low


def test_probe_prompt_instructs_thin_result_honesty() -> None:
    """A person who can simply choose must be reported, not fabricated into one.

    The prompt must tell the probe: if there is no real tension, say so in
    the result rather than invent a dilemma — the supervisor needs the thin
    signal so it can pivot.
    """
    low = load_probe_prompt().lower()
    # The thin-result instruction is present and uses the result flag.
    assert "tension_is_real" in low, (
        "probe prompt must reference the tension_is_real flag"
    )
    assert "thin" in low, "probe prompt must name the thin-result case"
    # It must forbid fabricating a dilemma.
    assert any(
        phrase in low
        for phrase in ("not manufacture", "not fabricate", "fabricated dilemma")
    ), "probe prompt must forbid fabricating a dilemma"
    # It must explain the pivot rationale.
    assert "pivot" in low, "probe prompt must explain the supervisor pivots"


def test_probe_prompt_honors_follow_up_craft() -> None:
    """The probe inherits the DRILL IN / ZOOM OUT / CROSS follow-up rule."""
    low = load_probe_prompt().lower()
    assert "drill in" in low
    assert "zoom out" in low
    assert "cross" in low


def test_probe_prompt_honors_output_discipline() -> None:
    """The probe restates the persona's output discipline and banned phrasings."""
    low = load_probe_prompt().lower()
    assert "drift check" in low
    assert "banned phrasing" in low
    # One spoken question per turn, kept short.
    assert "one spoken question" in low or "one question" in low
    assert "200 character" in low or "~200" in low


def test_probe_prompt_instructs_the_record_tool() -> None:
    """The prompt tells the LLM to call the recording tool to finish."""
    low = load_probe_prompt().lower()
    assert "record_two_buttons_result" in low


# ---------------------------------------------------------------------------
# TwoButtonsProbeTask — construction
# ---------------------------------------------------------------------------


def test_probe_task_constructs() -> None:
    """TwoButtonsProbeTask constructs without a live session."""
    task = TwoButtonsProbeTask()
    assert task is not None


def test_probe_task_is_an_agent_task_subclass() -> None:
    """TwoButtonsProbeTask subclasses livekit.agents.AgentTask."""
    assert issubclass(TwoButtonsProbeTask, AgentTask)
    assert isinstance(TwoButtonsProbeTask(), AgentTask)


def test_probe_task_loads_prompt_as_instructions() -> None:
    """The task's instructions are the loaded probe prompt."""
    task = TwoButtonsProbeTask()
    assert task.instructions == load_probe_prompt()


def test_probe_task_exposes_the_record_tool() -> None:
    """The task exposes the recording function tool."""
    task = TwoButtonsProbeTask()
    tool_names = {get_function_info(t).name for t in task.tools}
    assert "record_two_buttons_result" in tool_names


def test_probe_task_not_done_before_recording() -> None:
    """A freshly constructed task has not completed."""
    assert TwoButtonsProbeTask().done() is False


# ---------------------------------------------------------------------------
# record_two_buttons_result — produces a valid typed result
# ---------------------------------------------------------------------------


def test_record_tool_produces_valid_two_buttons_result() -> None:
    """Calling the record tool resolves the task to a valid TwoButtonsResult."""
    holder: dict[str, TwoButtonsProbeTask] = {}

    async def run() -> str:
        # Construct inside the running loop: AgentTask binds an asyncio
        # Future at construction, which needs an event loop to exist.
        task = TwoButtonsProbeTask()
        holder["task"] = task
        return await task.record_two_buttons_result(
            None,  # RunContext — unused by the tool body, fine offline.
            button_a_label="Build the company",
            button_a_seduction=(
                "Building it would prove she can make something the world "
                "actually uses, on her own terms."
            ),
            button_b_label="Go back to research",
            button_b_seduction=(
                "Research lets her chase the one hard question that has "
                "haunted her since grad school."
            ),
            impossibility=(
                "Each path needs a decade of undivided attention; choosing "
                "one quietly forecloses the other forever."
            ),
            tension_is_real=True,
            notes="picked the company, then said 'ask me tomorrow'",
        )

    confirmation = asyncio.run(run())
    assert isinstance(confirmation, str) and confirmation

    task = holder["task"]
    assert task.done() is True
    result = _completed_result(task)
    assert isinstance(result, TwoButtonsResult)
    assert result.button_a_label == "Build the company"
    assert result.button_b_label == "Go back to research"
    assert result.impossibility.startswith("Each path")
    # Probe-level metadata rides on the task.
    assert task.tension_is_real is True
    assert task.notes == "picked the company, then said 'ask me tomorrow'"


def test_record_tool_rejects_over_long_fields() -> None:
    """Over-limit field content fails TwoButtonsResult validation.

    The char limits in agent/state.py are the locked render schema; the
    record tool must surface a violation rather than silently truncate.
    """
    holder: dict[str, TwoButtonsProbeTask] = {}

    async def run() -> None:
        task = TwoButtonsProbeTask()
        holder["task"] = task
        await task.record_two_buttons_result(
            None,
            button_a_label="x" * 100,  # > 40-char label cap
            button_a_seduction="A is tempting.",
            button_b_label="B",
            button_b_seduction="B is tempting.",
            impossibility="Cannot have both.",
            tension_is_real=True,
        )

    with pytest.raises(ValidationError):
        asyncio.run(run())
    # A failed recording must not falsely mark the task done.
    assert holder["task"].done() is False


# ---------------------------------------------------------------------------
# Thin-result handling — no real tension
# ---------------------------------------------------------------------------


def test_record_tool_handles_thin_result() -> None:
    """A thin probe records tension_is_real=False so the supervisor can pivot."""
    holder: dict[str, TwoButtonsProbeTask] = {}

    async def run() -> None:
        task = TwoButtonsProbeTask()
        holder["task"] = task
        await task.record_two_buttons_result(
            None,
            button_a_label="Take the promotion",
            button_a_seduction="More money and a title she has earned.",
            button_b_label="Stay in her role",
            button_b_seduction="Comfortable, but she does not really want it.",
            impossibility="There is no real impossibility — she chose cleanly.",
            tension_is_real=False,
            notes="chose the promotion instantly, showed no pull back",
        )

    asyncio.run(run())

    task = holder["task"]
    assert task.done() is True
    # The typed result is still valid and populated.
    result = _completed_result(task)
    assert isinstance(result, TwoButtonsResult)
    # The thin signal is what the supervisor reads to pivot.
    assert task.tension_is_real is False
    assert task.notes is not None
    assert "instantly" in task.notes


def test_thin_result_still_carries_a_valid_typed_result() -> None:
    """Even thin, the probe returns a fully-populated TwoButtonsResult.

    tension_is_real carries the truth; the typed fields still carry texture.
    """
    holder: dict[str, TwoButtonsProbeTask] = {}

    async def run() -> None:
        task = TwoButtonsProbeTask()
        holder["task"] = task
        await task.record_two_buttons_result(
            None,
            button_a_label="Option one",
            button_a_seduction="A genuine-sounding pull.",
            button_b_label="Option two",
            button_b_seduction="An obligation, not a real want.",
            impossibility="The second option was never a true seduction.",
            tension_is_real=False,
        )

    asyncio.run(run())
    task = holder["task"]
    result = _completed_result(task)
    # No field is empty — the render schema is still satisfied.
    for field in (
        result.button_a_label,
        result.button_a_seduction,
        result.button_b_label,
        result.button_b_seduction,
        result.impossibility,
    ):
        assert field.strip()
    assert task.tension_is_real is False
    assert task.notes is None
