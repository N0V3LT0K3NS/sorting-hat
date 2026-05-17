"""G7 tests — the Arc probe sub-interview.

Offline tests only: NO live LLM, no LiveKit room, no network. They prove the
static contract of G7:

1. ``prompts/probe_arc.md`` exists, carries the four trajectory questions, and
   honors the blind-sort rule (never names "arc" to the interviewee) plus the
   borrowed craft (DRILL IN / ZOOM OUT / CROSS, banned phrasings, output
   discipline, DRIFT CHECK).
2. ``ArcProbeTask`` constructs and is a real ``livekit.agents.AgentTask``.
3. The ``record_arc`` function tool produces a valid ``ArcResult`` and
   completes the task.
4. Thin-result handling works: a stable-state person is recorded honestly
   with the thin flag set, not fabricated into a transformation.

The ``record_arc`` tool is exercised directly — the LLM that would call it in
production is not involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.probes import ArcProbeTask, load_arc_probe_prompt
from agent.probes.arc import ARC_PROBE_PROMPT_PATH
from agent.state import ArcResult

# Repo root: tests/ -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROBE_PROMPT_PATH = _REPO_ROOT / "prompts" / "probe_arc.md"

# The four probe questions, exactly as the goal brief specifies them. The test
# owns its own copy so a typo in prompts/probe_arc.md cannot silently pass.
_EXPECTED_QUESTIONS = (
    "Walk me through the change — what came first, what came next?",
    "What was the catalyst — what made you see it differently?",
    "What had to die in the old version for the new one to exist?",
    "Are you still in that arc, or has it landed? What's the next arc you sense?",
)


# ---------------------------------------------------------------------------
# Test helper — read a completed AgentTask's result without a live session
# ---------------------------------------------------------------------------


def _completed_result(task: ArcProbeTask) -> object:
    """Return the value an AgentTask was completed with.

    ``AgentTask`` stores its result on a private future and only delivers it
    by being awaited inside a live session. Offline, we read that future
    directly — the future is the same object ``self.complete()`` resolves.
    """
    fut = getattr(task, "_AgentTask__fut")
    assert fut.done(), "task is not complete — record_arc did not call complete()"
    return fut.result()


# ---------------------------------------------------------------------------
# probe_arc.md — existence and content
# ---------------------------------------------------------------------------


def test_probe_prompt_file_exists() -> None:
    """prompts/probe_arc.md must exist and be non-empty."""
    assert _PROBE_PROMPT_PATH.is_file(), f"missing probe prompt at {_PROBE_PROMPT_PATH}"
    assert _PROBE_PROMPT_PATH.read_text(encoding="utf-8").strip(), "probe prompt is empty"
    # The module path constant points at the same file.
    assert ARC_PROBE_PROMPT_PATH == _PROBE_PROMPT_PATH


def test_probe_prompt_contains_all_four_questions_verbatim() -> None:
    """Every Arc probe question appears verbatim in probe_arc.md."""
    prompt = load_arc_probe_prompt()
    for i, question in enumerate(_EXPECTED_QUESTIONS, start=1):
        assert question in prompt, f"probe question {i} missing from probe_arc.md"


def test_probe_questions_appear_in_order() -> None:
    """The four questions appear in probe_arc.md in their fixed asking order."""
    prompt = load_arc_probe_prompt()
    positions = [prompt.index(q) for q in _EXPECTED_QUESTIONS]
    assert positions == sorted(positions), "probe questions out of order in probe_arc.md"


def test_probe_prompt_honors_blind_sort() -> None:
    """The probe never names the taxonomy as a label for the interviewee."""
    prompt = load_arc_probe_prompt()
    low = prompt.lower()
    # The blind-sort rule must be stated.
    assert "blind-sort" in low or "blind sort" in low, "no blind-sort rule in probe_arc.md"
    # All four template names must be present only as forbidden words: every
    # occurrence sits near banning language.
    for name in ("iceberg", "compass", "two buttons"):
        idx = low.find(name)
        assert idx != -1, f"probe must name '{name}' as forbidden"
        window = low[max(0, idx - 220) : idx + 220]
        assert any(
            marker in window
            for marker in ("never", "do not", "forbidden", "blind", "hidden")
        ), f"template name {name!r} appears without a banning context"


def test_probe_prompt_does_not_name_arc_as_a_label() -> None:
    """'arc' may appear only inside instructions forbidding it as a label.

    The probe is the trajectory probe, but the word 'arc' must never be
    presented as something to say to the interviewee. Every occurrence of the
    bare word must sit in banning context — except inside the four verbatim
    probe questions, which the brief specifies use the word 'arc' but are
    spoken as ordinary conversation, never as a category label.
    """
    prompt = load_arc_probe_prompt()
    # Remove the verbatim probe questions; the brief fixes their wording.
    scrubbed = prompt
    for q in _EXPECTED_QUESTIONS:
        scrubbed = scrubbed.replace(q, "")
    low = scrubbed.lower()
    idx = 0
    while True:
        idx = low.find("arc", idx)
        if idx == -1:
            break
        window = low[max(0, idx - 200) : idx + 200]
        assert any(
            marker in window
            for marker in ("never", "do not", "not name", "forbidden", "blind", "hidden")
        ), f"'arc' appears outside a banning context near: {scrubbed[idx - 40 : idx + 40]!r}"
        idx += 3


def test_probe_prompt_encodes_three_move_followup_rule() -> None:
    """DRILL IN / ZOOM OUT / CROSS — all three follow-up moves are present."""
    low = load_arc_probe_prompt().lower()
    assert "drill in" in low
    assert "zoom out" in low
    assert "cross" in low, "probe_arc.md must carry the CROSS follow-up move"
    assert "of three things" in low, "the follow-up rule must name all three moves"


def test_probe_prompt_encodes_banned_phrasings() -> None:
    """The probe bans the interviewer-tell phrasings."""
    low = load_arc_probe_prompt().lower()
    assert "banned phrasing" in low, "no banned-phrasing section in probe_arc.md"
    assert "in what way" in low
    assert "the process" in low
    assert "the approach" in low


def test_probe_prompt_encodes_output_discipline_and_drift_check() -> None:
    """Output discipline and the DRIFT CHECK self-scaffold are present."""
    low = load_arc_probe_prompt().lower()
    assert "drift check" in low, "no DRIFT CHECK in probe_arc.md"
    assert "one spoken question" in low, "no output-discipline rule in probe_arc.md"


def test_probe_prompt_instructs_thin_result_handling() -> None:
    """The probe must tell the LLM to report a thin result, not fabricate one.

    If the person describes a stable state rather than a trajectory, the probe
    must record that honestly so the supervisor can pivot.
    """
    low = load_arc_probe_prompt().lower()
    assert "stable state" in low, "probe must address the no-real-arc case"
    assert "thread_came_back_thin" in low, "probe must name the thin-result flag"
    # It must explicitly forbid manufacturing a transformation.
    assert "do not manufacture" in low or "do not fabricate" in low, (
        "probe must forbid fabricating a transformation"
    )


def test_probe_prompt_names_the_four_arc_steps() -> None:
    """The probe extracts a four-step sequence: before, catalyst, middle, after."""
    low = load_arc_probe_prompt().lower()
    for step in ("before", "catalyst", "middle", "after"):
        assert step in low, f"probe_arc.md must name the '{step}' step"
    # The record tool is named so the LLM knows how to finish.
    assert "record_arc" in load_arc_probe_prompt(), "probe must name the record_arc tool"


# ---------------------------------------------------------------------------
# ArcProbeTask — construction and type
# ---------------------------------------------------------------------------


def test_probe_task_constructs() -> None:
    """ArcProbeTask constructs and loads the probe prompt as its instructions."""
    task = ArcProbeTask()
    assert task.instructions == load_arc_probe_prompt()
    assert "DRILL IN" in task.instructions


def test_probe_task_is_a_livekit_agent_task() -> None:
    """ArcProbeTask is a subclass of livekit.agents.AgentTask."""
    from livekit.agents import AgentTask

    assert issubclass(ArcProbeTask, AgentTask)
    assert isinstance(ArcProbeTask(), AgentTask)


def test_probe_task_starts_not_done() -> None:
    """A freshly constructed probe is not yet complete."""
    task = ArcProbeTask()
    assert task.done() is False


def test_probe_task_exposes_record_tool() -> None:
    """The record_arc function tool is registered on the task."""
    task = ArcProbeTask()
    tool_names = {getattr(t, "name", None) or t.info.name for t in task.tools}
    assert "record_arc" in tool_names, "record_arc is not registered as a tool"


# ---------------------------------------------------------------------------
# record_arc — produces a valid ArcResult and completes the task
# ---------------------------------------------------------------------------


async def test_record_arc_produces_valid_result_and_completes() -> None:
    """record_arc builds a valid ArcResult and completes the AgentTask."""
    task = ArcProbeTask()
    ret = await task.record_arc(
        None,
        before="She built her whole identity around being the dependable one nobody worried about.",
        catalyst="A burnout collapse at thirty-one forced her to admit dependability had become a cage.",
        middle="She is learning, awkwardly, to disappoint people on purpose and survive it.",
        after="She is heading toward a self defined by what she wants, not by what others can count on.",
    )
    assert isinstance(ret, str)  # tool returns a short ack string for the LLM

    assert task.done() is True, "record_arc must complete the task"
    result = _completed_result(task)
    assert isinstance(result, ArcResult), "completed value must be an ArcResult"
    assert result.before.startswith("She built her whole identity")
    assert result.catalyst.startswith("A burnout collapse")
    assert result.middle.startswith("She is learning")
    assert result.after.startswith("She is heading toward")


async def test_record_arc_result_respects_char_limits() -> None:
    """The ArcResult built by record_arc honors the per-field 200-char cap."""
    task = ArcProbeTask()
    await task.record_arc(
        None,
        before="Quiet kid.",
        catalyst="A teacher noticed.",
        middle="Still figuring it out.",
        after="Becoming louder.",
    )
    result = _completed_result(task)
    for field in (result.before, result.catalyst, result.middle, result.after):
        assert len(field) <= 200


# ---------------------------------------------------------------------------
# Thin-result handling — a stable self is recorded honestly, not fabricated
# ---------------------------------------------------------------------------


async def test_record_arc_handles_thin_result() -> None:
    """A stable-state person is recorded with the thin flag, not a fake arc.

    When thread_came_back_thin is True the probe still produces a valid typed
    ArcResult — the supervisor reads the honest fields and the flag together
    to decide whether to pivot to a different shape.
    """
    task = ArcProbeTask()
    await task.record_arc(
        None,
        before="No distinct earlier self — describes themselves as consistent across time.",
        catalyst="No catalyst named; nothing they point to as a turning point.",
        middle="Not in a passage — reports a steady, settled present.",
        after="No felt trajectory; expects to remain much as they are.",
        thread_came_back_thin=True,
    )
    assert task.done() is True
    result = _completed_result(task)
    assert isinstance(result, ArcResult)
    # The honest thin fields are preserved verbatim for the supervisor.
    assert "consistent across time" in result.before
    assert "No catalyst" in result.catalyst


async def test_record_arc_thin_flag_defaults_false() -> None:
    """thread_came_back_thin defaults to False — a clean arc needs no flag."""
    task = ArcProbeTask()
    # Called without the flag: a normal, fully-populated arc.
    await task.record_arc(
        None,
        before="He treated every job as a step on a ladder to somewhere else.",
        catalyst="Getting laid off twice in a year broke his faith in the ladder.",
        middle="He is building something of his own, slower and far less certain.",
        after="He is heading toward work measured by its own worth, not its rung.",
    )
    result = _completed_result(task)
    assert isinstance(result, ArcResult)
    assert task.done() is True


def test_probe_prompt_module_loader_rejects_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_arc_probe_prompt raises if the prompt file is missing.

    The prompt is the probe's behavioural contract — there is no degraded
    mode for a probe with no instructions.
    """
    import agent.probes.arc as arc_mod

    monkeypatch.setattr(arc_mod, "ARC_PROBE_PROMPT_PATH", tmp_path / "nope.md")
    with pytest.raises(FileNotFoundError):
        arc_mod.load_arc_probe_prompt()
