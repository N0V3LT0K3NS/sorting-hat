"""G4 tests — the Iceberg probe ``AgentTask``.

Scripted tests only: NO live LLM is called. They prove the static contract of
G4:

1. ``prompts/probe_iceberg.md`` exists, carries the four excavation questions
   verbatim, and honours the blind-sort (the word "iceberg" never appears as
   a label spoken to the person).
2. ``IcebergProbeTask`` constructs and is a ``livekit.agents.AgentTask``
   subclass parameterised to return an ``IcebergResult``.
3. The ``record_iceberg`` tool builds a valid ``IcebergResult`` and completes
   the task with it.
4. A thin / evasive result is handled honestly — the layers reached are
   recorded and the task still completes, without fabricating depth.
5. Over-long layers fail validation rather than silently truncating.

These run fully offline — no network, no LiveKit room, no model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from livekit.agents import AgentTask

from agent.probes import IcebergProbeTask
from agent.probes.iceberg import load_probe_prompt
from agent.state import IcebergResult

# Repo root: tests/ -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _REPO_ROOT / "prompts" / "probe_iceberg.md"

# The four excavation questions, exactly as the goal brief specifies them. The
# test owns its own copy so a typo in the prompt cannot silently pass.
_EXCAVATION_QUESTIONS = (
    "When you're alone, when no one's watching, is there a version of you "
    "that's different?",
    "What's something you believe that would surprise people who know you?",
    "Is there something about yourself you've only told a small number of "
    "people?",
    "What's the thing you've never said out loud, or barely admitted to "
    "yourself?",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed_result(task: IcebergProbeTask) -> object:
    """Return the result an awaited :class:`IcebergProbeTask` would yield.

    ``AgentTask`` can only be ``await``-ed inside a live session, so for
    offline tests we read the result straight off the task's completion
    future. ``complete()`` resolves that future; this is the same value the
    supervisor (G8) receives when it awaits the task in a real interview.
    """
    assert task.done(), "task is not complete — record tool was never called"
    fut = getattr(task, "_AgentTask__fut")
    return fut.result()


def _build_and_record(**kwargs) -> tuple[IcebergProbeTask, str]:
    """Construct a probe and drive its ``record_iceberg`` tool, all in one loop.

    ``AgentTask.__init__`` allocates an ``asyncio.Future`` and so must run with
    a live event loop; the record tool is async. Doing both inside a single
    ``asyncio.run`` keeps the task and its future bound to the same loop —
    exactly as they are in a real session.
    """

    async def _run() -> tuple[IcebergProbeTask, str]:
        task = IcebergProbeTask()
        tool = task.tools[0]
        assert tool.info.name == "record_iceberg"
        reply = await tool(**kwargs)
        return task, reply

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# 1. The probe prompt
# ---------------------------------------------------------------------------


def test_probe_prompt_file_exists_and_is_nonempty() -> None:
    """prompts/probe_iceberg.md must exist and carry content."""
    assert _PROMPT_PATH.is_file(), f"missing probe prompt at {_PROMPT_PATH}"
    assert _PROMPT_PATH.read_text(encoding="utf-8").strip(), "probe prompt is empty"


def test_load_probe_prompt_returns_text() -> None:
    """load_probe_prompt() returns the prompt body as non-empty text."""
    text = load_probe_prompt()
    assert isinstance(text, str)
    assert len(text) > 200


def test_prompt_contains_all_four_excavation_questions() -> None:
    """All four excavation questions appear verbatim in the probe prompt."""
    prompt = load_probe_prompt()
    for question in _EXCAVATION_QUESTIONS:
        assert question in prompt, f"excavation question missing: {question!r}"


def test_prompt_names_the_four_layers() -> None:
    """The prompt names the four IcebergResult layers it must excavate."""
    prompt = load_probe_prompt().lower()
    for layer in ("surface", "first_layer", "second_layer", "abyss"):
        assert layer in prompt, f"layer not referenced in prompt: {layer}"


def test_prompt_honours_blind_sort() -> None:
    """The prompt must forbid saying 'iceberg' to the person, never instruct it.

    The word may appear in the prompt's own framing (it is the probe's name),
    but only inside the explicit blind-sort prohibition — never as something
    the agent is told to say aloud. We assert the prohibition is present and
    that no line tells the agent to *say* the word to the interviewee.
    """
    prompt = load_probe_prompt()
    lower = prompt.lower()
    # The blind-sort rule must be explicit.
    assert "blind-sort" in lower
    assert 'never say the word "iceberg"' in lower
    # No instruction to speak the label to the person.
    for banned in ('say "iceberg"', "say 'iceberg'", "tell them iceberg"):
        # the only allowed occurrence is the negated 'never say "iceberg"'
        idx = lower.find(banned)
        if idx != -1:
            preceding = lower[max(0, idx - 12):idx]
            assert "never " in preceding, (
                f"prompt appears to instruct saying the label: ...{lower[idx-12:idx+20]}..."
            )


def test_prompt_carries_followup_and_drift_craft() -> None:
    """The probe inherits the three-move follow-up rule and DRIFT CHECK."""
    prompt = load_probe_prompt()
    for token in ("DRILL IN", "ZOOM OUT", "CROSS", "DRIFT CHECK"):
        assert token in prompt, f"borrowed craft missing from probe: {token}"
    # Banned phrasings are carried.
    assert "In what way" in prompt


def test_prompt_instructs_honest_thin_handling() -> None:
    """The prompt must tell the probe to report thinness, not fabricate depth."""
    prompt = load_probe_prompt()
    lower = prompt.lower()
    assert "thin" in lower
    assert "thin_result" in prompt
    # It must explicitly warn against inventing depth.
    assert "do not invent depth" in lower or "fabricat" in lower


# ---------------------------------------------------------------------------
# 2. The IcebergProbeTask class
# ---------------------------------------------------------------------------


def test_probe_task_constructs() -> None:
    """IcebergProbeTask() builds without a live session."""
    task = IcebergProbeTask()
    assert task is not None


def test_probe_task_is_agent_task_subclass() -> None:
    """IcebergProbeTask subclasses livekit.agents.AgentTask."""
    assert issubclass(IcebergProbeTask, AgentTask)
    assert isinstance(IcebergProbeTask(), AgentTask)


def test_probe_task_loads_prompt_as_instructions() -> None:
    """The task's instructions are the probe prompt."""
    task = IcebergProbeTask()
    assert task.instructions == load_probe_prompt()


def test_probe_task_exposes_single_record_tool() -> None:
    """The task carries exactly one tool, named record_iceberg."""
    task = IcebergProbeTask()
    assert len(task.tools) == 1
    assert task.tools[0].info.name == "record_iceberg"


def test_fresh_task_is_not_done() -> None:
    """A freshly constructed task has not completed."""
    assert IcebergProbeTask().done() is False


# ---------------------------------------------------------------------------
# 3. The record tool produces a valid IcebergResult
# ---------------------------------------------------------------------------


def test_record_tool_completes_task_with_iceberg_result() -> None:
    """Calling record_iceberg builds a valid IcebergResult and completes."""
    task, reply = _build_and_record(
        surface="The capable, unflappable one everyone leans on at work.",
        first_layer="Quietly exhausted by being the dependable one.",
        second_layer="Suspects the competence is a costume worn since childhood.",
        abyss="Afraid that without the usefulness there is nothing underneath.",
    )
    assert isinstance(reply, str) and reply
    assert task.done() is True

    result = _completed_result(task)
    assert isinstance(result, IcebergResult)
    assert result.surface.startswith("The capable")
    assert result.first_layer.startswith("Quietly exhausted")
    assert result.second_layer.startswith("Suspects")
    assert result.abyss.startswith("Afraid")


def test_record_tool_rejects_overlong_layer() -> None:
    """An over-long layer fails validation; the task does not complete."""
    task, reply = _build_and_record(
        surface="x" * 200,  # over the ~120-char IcebergResult cap
        first_layer="ok",
        second_layer="ok",
        abyss="ok",
    )
    # The tool reports the failure back to the LLM rather than crashing.
    assert isinstance(reply, str)
    assert "120" in reply or "validate" in reply.lower()
    # The task is NOT completed on a validation failure.
    assert task.done() is False


# ---------------------------------------------------------------------------
# 4. Thin-result handling
# ---------------------------------------------------------------------------


def test_thin_result_completes_without_fabricated_depth() -> None:
    """A thin/evasive run still completes — with honest empty-layer notes."""
    task, reply = _build_and_record(
        surface="Friendly, easy-going, keeps things light.",
        first_layer="Deflected — no real first layer offered.",
        second_layer="Stayed on the surface; would not go below it.",
        abyss="Would not go there.",
        thin_result=True,
        thin_reason="Interviewee stayed light and deflected every descent.",
    )
    assert isinstance(reply, str) and reply
    assert task.done() is True

    # A thin result is still a valid, fully typed IcebergResult.
    result = _completed_result(task)
    assert isinstance(result, IcebergResult)
    assert "Deflected" in result.first_layer
    assert "surface" in result.second_layer.lower()


def test_record_tool_defaults_to_non_thin() -> None:
    """thin_result is optional and defaults to a normal (non-thin) result."""
    task, _reply = _build_and_record(
        surface="surface text",
        first_layer="first layer text",
        second_layer="second layer text",
        abyss="abyss text",
    )
    assert task.done() is True
    assert isinstance(_completed_result(task), IcebergResult)
