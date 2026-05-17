"""G3 tests — InterviewerAgent persona + the five base questions.

Scripted-replay tests only: NO live LLM is called. They prove the static
contract of G3:

1. ``prompts/persona.md`` exists, carries the five base questions verbatim,
   and encodes the banned-phrasing rules.
2. The persona BANS its banned phrasings — it does not itself instruct the
   agent to ask using "in what way", "the process", or "the approach".
3. The ``InterviewerAgent`` constructs and loads the persona as instructions.
4. ``base_questions_completed`` advances correctly and saturates at five.

These run fully offline — no network, no LiveKit room.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent.interviewer import (
    BASE_QUESTION_COUNT,
    BASE_QUESTIONS,
    InterviewerAgent,
    load_persona,
)
from agent.state import InterviewState

# Repo root: tests/ -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PERSONA_PATH = _REPO_ROOT / "prompts" / "persona.md"

# The five base questions, exactly as the goal brief specifies them. The test
# owns its own copy so a typo in agent/interviewer.py cannot silently pass.
_EXPECTED_QUESTIONS = (
    "Tell me about something you're known for — something people notice "
    "about you right away.",
    "What's something most people don't realize about you?",
    "If you had to describe yourself in terms of tensions or contradictions "
    "— things that pull you in different directions — what would those be?",
    "What's a realization you've had about yourself that changed how you "
    "see things? Walk me through how that happened.",
    "What are you actually optimizing for in your life right now? What "
    "would you rank highest?",
)


# ---------------------------------------------------------------------------
# persona.md — existence and content
# ---------------------------------------------------------------------------


def test_persona_file_exists() -> None:
    """prompts/persona.md must exist and be non-empty."""
    assert _PERSONA_PATH.is_file(), f"missing persona prompt at {_PERSONA_PATH}"
    assert _PERSONA_PATH.read_text(encoding="utf-8").strip(), "persona is empty"


def test_persona_contains_all_five_questions_verbatim() -> None:
    """Every base question appears verbatim in the persona prompt."""
    persona = load_persona()
    for i, question in enumerate(_EXPECTED_QUESTIONS, start=1):
        assert question in persona, f"base question {i} missing from persona.md"


def test_persona_questions_appear_in_order() -> None:
    """The five questions appear in persona.md in their fixed asking order."""
    persona = load_persona()
    positions = [persona.index(q) for q in _EXPECTED_QUESTIONS]
    assert positions == sorted(positions), "base questions out of order in persona.md"


def test_persona_encodes_banned_phrasing_rules() -> None:
    """The persona explicitly bans the interviewer-tell phrasings."""
    persona = load_persona().lower()
    assert "banned phrasing" in persona, "no banned-phrasing section in persona.md"
    # The three named banned phrasings must each be called out as banned.
    assert "in what way" in persona
    assert "the process" in persona
    assert "the approach" in persona


def test_persona_encodes_drill_in_zoom_out_rule() -> None:
    """The DRILL IN / ZOOM OUT follow-up rule is present."""
    persona = load_persona().lower()
    assert "drill in" in persona
    assert "zoom out" in persona


def test_persona_encodes_drift_check() -> None:
    """The DRIFT CHECK self-scaffold is present."""
    assert "drift check" in load_persona().lower()


def test_persona_encodes_cross_follow_up_move() -> None:
    """The CROSS move is a named third follow-up option in persona.md.

    G3.1: the DRILL IN / ZOOM OUT binary is not exhaustive. CROSS — the
    lateral move (pit one thing against another, the negative case, the
    source) — must be present by name and as part of the follow-up rule.
    """
    persona = load_persona()
    assert "CROSS" in persona, "persona.md must name the CROSS follow-up move"
    # The follow-up rule heading must cover all three moves, not two.
    low = persona.lower()
    assert "drill in, zoom out, or cross" in low, (
        "the follow-up rule heading must list all three moves"
    )
    # CROSS is described as one of three things a follow-up does.
    assert "of three things" in low, (
        "the follow-up rule must say a follow-up does one of THREE things"
    )


def test_persona_drift_check_references_three_moves_not_two() -> None:
    """The DRIFT CHECK treats CROSS as a valid move, not as noise.

    G3.1: the old DRIFT CHECK flagged any non-drill/non-zoom follow-up as
    'probably noise'. It must now check for a clean DRILL IN, ZOOM OUT, OR
    CROSS, and only call it noise if it is none of the three.
    """
    persona = load_persona()
    low = persona.lower()
    # Locate the DRIFT CHECK section and inspect only its text.
    start = low.index("## drift check")
    end = low.index("\n## ", start + 1)
    drift = persona[start:end]
    drift_low = drift.lower()
    # All three moves must be named inside the DRIFT CHECK.
    assert "drill in" in drift_low
    assert "zoom out" in drift_low
    assert "cross" in drift_low, "DRIFT CHECK must reference the CROSS move"
    # 'none of the three' — noise is the residual after three valid moves.
    assert "none of the three" in drift_low, (
        "DRIFT CHECK must treat noise as the residual of three moves, not two"
    )


def test_persona_drops_fixed_drill_then_zoom_default() -> None:
    """The 'usually drill in before it zooms out' default is gone.

    G3.1: a fixed order is wrong for the tension and trajectory shapes. The
    persona must instead say the move depends on what the thread owes.
    """
    low = load_persona().lower()
    assert "usually drill in before it zooms out" not in low, (
        "the fixed drill-then-zoom default must be removed"
    )
    assert "what the thread still owes" in low, (
        "persona must say the move depends on what the thread still owes"
    )


def test_persona_honors_blind_sort_rule() -> None:
    """The persona forbids naming any template to the user (blind sort)."""
    persona = load_persona().lower()
    assert "blind-sort" in persona or "blind sort" in persona
    # The four template names must be named only as forbidden words.
    for name in ("iceberg", "compass", "arc", "two buttons"):
        assert name in persona, f"persona must name '{name}' as forbidden"


def test_persona_does_not_instruct_banned_phrasings_as_questions() -> None:
    """The persona bans the tells — it never instructs the agent to USE them.

    A banned phrasing may appear only inside the banned-phrasing rules (where
    it is quoted as forbidden). It must never appear as an actual instruction
    to ask a question. We check that each banned phrasing appears only on
    lines that also carry banning language ('banned', 'do not', 'never',
    quotes), and never inside an example question the agent is told to ask.
    """
    banned = ("in what way", "the process", "the approach")
    lines = load_persona().splitlines()

    # Lines that are part of the banned-phrasing section get a pass: that
    # section is bounded by its heading and the next top-level heading.
    in_banned_section = False
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("## banned phrasings"):
            in_banned_section = True
            continue
        if in_banned_section and stripped.startswith("## "):
            in_banned_section = False

        for phrase in banned:
            if phrase in low:
                # Allowed only inside the banned section, or on a line whose
                # job is plainly to forbid it.
                forbidding = any(
                    marker in low
                    for marker in ("banned", "do not", "never", "instead", "not ")
                )
                assert in_banned_section or forbidding, (
                    f"banned phrasing {phrase!r} used as instruction outside "
                    f"the banned-phrasing section: {stripped!r}"
                )


def test_persona_never_reveals_taxonomy_as_label() -> None:
    """No template name is ever presented as something to call the user.

    The four names must appear only as forbidden words. They must not show up
    as instruction text telling the agent to label or place the person.
    """
    persona = load_persona().lower()
    # Crude but effective: there must be no instruction pattern like
    # "tell them ... iceberg". Every occurrence of a template name should sit
    # near banning language.
    for name in ("iceberg", "compass", "two buttons"):
        for match in re.finditer(re.escape(name), persona):
            window = persona[max(0, match.start() - 200) : match.end() + 200]
            assert any(
                marker in window
                for marker in ("never", "do not", "forbidden", "blind", "not name", "hint")
            ), f"template name {name!r} appears without a banning context"


# ---------------------------------------------------------------------------
# BASE_QUESTIONS — the in-code source of truth
# ---------------------------------------------------------------------------


def test_base_questions_match_brief_verbatim() -> None:
    """agent.interviewer.BASE_QUESTIONS matches the brief, in order."""
    assert BASE_QUESTIONS == _EXPECTED_QUESTIONS
    assert BASE_QUESTION_COUNT == 5


# ---------------------------------------------------------------------------
# InterviewerAgent — construction and persona loading
# ---------------------------------------------------------------------------


def test_interviewer_constructs_and_loads_persona() -> None:
    """InterviewerAgent constructs and uses the persona file as instructions."""
    agent = InterviewerAgent()
    # The Agent's instructions are exactly the loaded persona prompt.
    assert agent.instructions == load_persona()
    assert "DRILL IN" in agent.instructions
    assert BASE_QUESTIONS[0] in agent.instructions


def test_interviewer_is_a_livekit_agent() -> None:
    """InterviewerAgent is a subclass of livekit.agents.Agent."""
    from livekit.agents import Agent

    assert issubclass(InterviewerAgent, Agent)
    assert isinstance(InterviewerAgent(), Agent)


def test_interviewer_accepts_shared_state() -> None:
    """A passed-in InterviewState is the one the agent reads and writes."""
    state = InterviewState()
    agent = InterviewerAgent(state=state)
    assert agent.state is state


def test_interviewer_creates_fresh_state_when_none_given() -> None:
    """With no state passed, the agent owns a fresh zeroed InterviewState."""
    agent = InterviewerAgent()
    assert isinstance(agent.state, InterviewState)
    assert agent.base_questions_completed == 0


# ---------------------------------------------------------------------------
# base_questions_completed — progress advances correctly
# ---------------------------------------------------------------------------


def test_base_questions_completed_starts_at_zero() -> None:
    """A fresh interview has completed no base questions."""
    agent = InterviewerAgent()
    assert agent.base_questions_completed == 0
    assert agent.base_questions_done is False
    assert agent.current_base_question() == BASE_QUESTIONS[0]


def test_advance_walks_the_five_questions_in_order() -> None:
    """advance_base_question() steps through all five questions in order."""
    state = InterviewState()
    agent = InterviewerAgent(state=state)

    for i, expected in enumerate(BASE_QUESTIONS):
        # Before advancing, the current question is the i-th one.
        assert agent.current_base_question() == expected
        assert agent.base_questions_completed == i
        assert agent.base_questions_done is False
        agent.advance_base_question()

    # All five done: counter saturated, no current question, done is True.
    assert agent.base_questions_completed == BASE_QUESTION_COUNT
    assert agent.base_questions_done is True
    assert agent.current_base_question() is None


def test_advance_writes_through_to_shared_state() -> None:
    """Advancing updates the shared InterviewState, not a private copy."""
    state = InterviewState()
    agent = InterviewerAgent(state=state)
    agent.advance_base_question()
    agent.advance_base_question()
    assert state.base_questions_completed == 2


def test_advance_saturates_and_does_not_overcount(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Advancing past the last question is a guarded no-op, not an over-count."""
    agent = InterviewerAgent()
    for _ in range(BASE_QUESTION_COUNT):
        agent.advance_base_question()
    assert agent.base_questions_completed == BASE_QUESTION_COUNT

    with caplog.at_level("WARNING", logger="sorting-hat.interviewer"):
        agent.advance_base_question()  # one too many

    assert agent.base_questions_completed == BASE_QUESTION_COUNT
    assert any("already complete" in rec.message for rec in caplog.records)
