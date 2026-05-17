"""sorting-hat — G7: the Arc probe sub-interview.

This is one of the brief's step-3 probes. The :class:`ArcProbeTask` is a
focused sub-interview that takes over the live session after the five base
questions, deepens into the *trajectory* template (the Anakin/Padmé four-panel
Arc), and returns a typed :class:`~agent.state.ArcResult`.

Arc is the trajectory shape: the person is defined by an escalating
realization — a **before**-state, a **catalyst**, a **middle** they are living
now, and an **after** they sense coming. The probe draws out that four-step
sequence using the DRILL IN / ZOOM OUT / CROSS follow-up craft, never naming
"arc" to the interviewee (the sort is blind).

A LiveKit ``AgentTask`` is an awaitable sub-task: it temporarily owns the
session, runs its own instructions and tools, and resolves — handing control
back to the parent agent — when ``self.complete(result)`` is called. The LLM
ends this probe by calling the ``record_arc`` function tool with the four
panels (and a thin-result flag); that tool builds the ``ArcResult`` and
completes the task.

The probe prompt — the craft this depends on — lives in
``prompts/probe_arc.md`` and is loaded at construction time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import AgentTask, RunContext, function_tool

from agent.state import ArcResult

logger = logging.getLogger("sorting-hat.probes.arc")

# ---------------------------------------------------------------------------
# Probe prompt — loaded from prompts/probe_arc.md
# ---------------------------------------------------------------------------

# prompts/ sits beside agent/ at the repo root: arc.py -> probes -> agent -> root.
ARC_PROBE_PROMPT_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "probe_arc.md"
)


def load_arc_probe_prompt() -> str:
    """Read and return the Arc probe sub-interview prompt.

    The prompt is the contract for the probe's behaviour, so a missing or
    empty file is a hard error here — there is no sensible degraded mode for a
    probe with no instructions.
    """
    if not ARC_PROBE_PROMPT_PATH.is_file():
        raise FileNotFoundError(
            f"Arc probe prompt not found at {ARC_PROBE_PROMPT_PATH} — G7 "
            "requires prompts/probe_arc.md to exist"
        )
    text = ARC_PROBE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Arc probe prompt at {ARC_PROBE_PROMPT_PATH} is empty")
    return text


# ---------------------------------------------------------------------------
# ArcProbeTask
# ---------------------------------------------------------------------------


class ArcProbeTask(AgentTask[ArcResult]):
    """The Arc probe — a sub-interview that returns a typed :class:`ArcResult`.

    Subclasses :class:`~livekit.agents.AgentTask` parameterised by its result
    type. Awaited inside the supervisor's routing flow, it takes over the
    session, runs the four trajectory questions with follow-ups, and resolves
    to an ``ArcResult`` when the LLM calls :meth:`record_arc`.

    The probe deepens into the *trajectory* shape — before, catalyst, middle,
    after — without ever naming "arc" to the interviewee. If the person turns
    out to have no real trajectory (a stable self, not a passage), the result
    is recorded honestly with ``thread_came_back_thin`` set, so the supervisor
    can pivot rather than ship a fabricated transformation.
    """

    def __init__(self) -> None:
        """Build the probe with the Arc probe prompt as its instructions."""
        super().__init__(instructions=load_arc_probe_prompt())

    async def on_enter(self) -> None:
        """Open the probe by continuing the conversation into the change.

        The probe is *continuous* with the base interview — no hand-off is
        announced — so the opening reply simply leans into the trajectory
        thread with the first of the four questions.
        """
        self.session.generate_reply(
            instructions=(
                "Warmly continue the conversation — no preamble, no hand-off. "
                "Lean into the change this person described and ask them to "
                "walk you through it: what came first, what came next."
            )
        )

    @function_tool
    async def record_arc(
        self,
        context: RunContext,
        before: str,
        catalyst: str,
        middle: str,
        after: str,
        thread_came_back_thin: bool = False,
    ) -> str:
        """Record the four-panel arc and finish the probe.

        Call this once the trajectory thread has given you all four steps —
        or once you have honestly concluded the person has no real arc.

        Args:
            before: One sentence — how things were before the change. If the
                person describes a stable self with no distinct earlier
                version, say exactly that here.
            catalyst: One sentence — the event or moment that made them see it
                differently. If there was no catalyst, say so plainly.
            middle: One sentence — the in-between the person is living now;
                what the passage cost or what was given up.
            after: One sentence — where the trajectory is heading, or whether
                it has already landed.
            thread_came_back_thin: Set ``True`` when the person described a
                stable state rather than a genuine trajectory — no real
                before/catalyst/movement. The supervisor reads this flag to
                decide whether to pivot to a different shape. Defaults to
                ``False``: a clean, fully-populated arc.
        """
        result = ArcResult(
            before=before,
            catalyst=catalyst,
            middle=middle,
            after=after,
        )
        if thread_came_back_thin:
            logger.info(
                "Arc probe came back thin — no genuine trajectory; "
                "supervisor should pivot."
            )
        else:
            logger.info("Arc probe complete — four-panel trajectory recorded.")

        # Resolve the AgentTask awaitable: hand control back to the parent
        # agent with the typed ArcResult as the value.
        self.complete(result)
        return "Arc recorded."


# TODO(G8): the supervisor (agent/interviewer.py) invokes this probe. After the
# five base questions, G8 reads InterviewState signal weights — when
# state.leading_template() == "arc", it awaits ArcProbeTask() from within the
# supervisor's routing flow, then stores the returned ArcResult on
# InterviewState.arc_result and sets InterviewState.chosen_template. If the
# result comes back with thread_came_back_thin set, G8's routing pivots to the
# next-strongest template instead of shipping a thin arc. G7 builds the probe
# in isolation; no probe delegation is wired here.


__all__ = [
    "ArcProbeTask",
    "ARC_PROBE_PROMPT_PATH",
    "load_arc_probe_prompt",
]
