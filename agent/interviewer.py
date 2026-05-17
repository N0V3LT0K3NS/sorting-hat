"""sorting-hat — G3: the InterviewerAgent persona.

This is the brief's step 2. The :class:`InterviewerAgent` is the warm voice
the interviewee actually talks to. It owns the five base diagnostic
questions, asks them in order, handles follow-ups, and wraps up.

What G3 deliberately does NOT include: probe sub-interviews, classification,
and supervisor routing. Those land in G4–G8. There is a single clearly
marked TODO below where the G8 routing hook attaches.

The persona prompt — the craft this whole project's quality rests on — lives
in ``prompts/persona.md`` and is loaded at construction time. Keeping it in a
file (not a string literal) means it can be iterated, reviewed, and diffed as
prose without touching code.
"""

from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import Agent

from agent.state import InterviewState

logger = logging.getLogger("sorting-hat.interviewer")

# ---------------------------------------------------------------------------
# Persona prompt — loaded from prompts/persona.md
# ---------------------------------------------------------------------------

# prompts/ sits beside agent/ at the repo root: interviewer.py -> agent -> root.
_PERSONA_PATH = Path(__file__).resolve().parent.parent / "prompts" / "persona.md"


# ---------------------------------------------------------------------------
# The five base questions
# ---------------------------------------------------------------------------
# Asked with everyone, in this order. The STRUCTURE of each answer is more
# diagnostic than its content — see prompts/persona.md and docs/borrowed-craft.md.
# This list is the single in-code source of truth for question order; the
# persona prompt carries the same five verbatim for the LLM's benefit.
BASE_QUESTIONS: tuple[str, ...] = (
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

BASE_QUESTION_COUNT: int = len(BASE_QUESTIONS)


def load_persona() -> str:
    """Read and return the InterviewerAgent persona prompt.

    The prompt is the contract for the agent's behaviour, so a missing or
    empty file is a hard error here — unlike a missing API key, there is no
    sensible degraded mode for an interviewer with no persona.
    """
    if not _PERSONA_PATH.is_file():
        raise FileNotFoundError(
            f"persona prompt not found at {_PERSONA_PATH} — G3 requires "
            "prompts/persona.md to exist"
        )
    text = _PERSONA_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"persona prompt at {_PERSONA_PATH} is empty")
    return text


# ---------------------------------------------------------------------------
# InterviewerAgent
# ---------------------------------------------------------------------------


class InterviewerAgent(Agent):
    """The warm voice persona that conducts the interview.

    A LiveKit ``Agent`` whose ``instructions`` are the persona prompt loaded
    from ``prompts/persona.md``. It walks the five base questions in order,
    tracking progress through ``InterviewState.base_questions_completed`` so
    that — once probe routing exists (G8) — the supervisor can tell when the
    base interview is done and a probe should begin.

    Progress lives on the shared :class:`~agent.state.InterviewState`, not on
    the agent, so a future probe ``AgentTask`` and the background classifier
    read the same counter. The state is passed in at construction; in the
    live worker it is the session's typed ``userdata``.
    """

    def __init__(self, state: InterviewState | None = None) -> None:
        """Build the interviewer with the persona prompt as its instructions.

        ``state`` is the shared interview state. When omitted a fresh
        :class:`InterviewState` is created — convenient for tests and for a
        worker that constructs the agent before wiring ``userdata``.
        """
        super().__init__(instructions=load_persona())
        self._state: InterviewState = state if state is not None else InterviewState()

    # --- Shared state ------------------------------------------------------

    @property
    def state(self) -> InterviewState:
        """The shared :class:`InterviewState` this interview is writing to."""
        return self._state

    # --- Base-question progress -------------------------------------------

    @property
    def base_questions_completed(self) -> int:
        """How many base questions have been fully asked-and-answered."""
        return self._state.base_questions_completed

    @property
    def base_questions_done(self) -> bool:
        """True once every base question and its follow-ups are complete."""
        return self._state.base_questions_completed >= BASE_QUESTION_COUNT

    def current_base_question(self) -> str | None:
        """Return the base question to ask next, or ``None`` if all are done.

        ``base_questions_completed`` is the count of *finished* threads, so it
        doubles as the zero-based index of the question currently in play.
        """
        idx = self._state.base_questions_completed
        if idx >= BASE_QUESTION_COUNT:
            return None
        return BASE_QUESTIONS[idx]

    def advance_base_question(self) -> None:
        """Mark the current base-question thread complete.

        Called when a base question and its follow-ups have yielded something
        real and the interview is ready to move on. Advancing past the last
        question is a no-op guarded by an assertion — the supervisor must not
        over-count.
        """
        if self._state.base_questions_completed >= BASE_QUESTION_COUNT:
            logger.warning(
                "advance_base_question() called with all %d base questions "
                "already complete — ignoring",
                BASE_QUESTION_COUNT,
            )
            return
        self._state.base_questions_completed += 1
        logger.info(
            "base question %d/%d complete",
            self._state.base_questions_completed,
            BASE_QUESTION_COUNT,
        )

        # TODO(G8): supervisor routing hook. Once all base questions are
        # complete, G8 wires this point to read InterviewState signal weights
        # (state.leading_template()), invoke the winning probe AgentTask, and
        # set state.chosen_template. G3 stops at the base interview and the
        # warm close — no probe delegation here yet.


__all__ = [
    "InterviewerAgent",
    "BASE_QUESTIONS",
    "BASE_QUESTION_COUNT",
    "load_persona",
]
