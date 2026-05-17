"""Compass probe — the *position* sub-interview.

The brief's step 3, for the 2x2 Compass template. After the five base
questions, when the accumulated signal weights lead toward *position*, the
supervisor (G8) hands the conversation to a :class:`CompassProbeTask`.

Compass is the position shape: the person is not torn (that is Two Buttons),
not arcing (that is Arc), not hiding depth (that is Iceberg) — they are
*located*, sitting at stable coordinates defined by two independent axes.
This probe finds those two axes, names both poles of each, places the person
on each, and learns why those axes are the ones that capture them.

The probe is a thin code wrapper around craft that lives in prose: the
sub-interview prompt is ``prompts/probe_compass.md``, loaded at construction.
The only behaviour in code is the ``record_compass_result`` function tool,
which the LLM calls once it has what it needs — that validates the data into
a :class:`~agent.state.CompassResult` and completes the task.

If the person turns out to be torn rather than located — no stable
coordinates — the prompt instructs the LLM to record the result with
``thin=True`` rather than fabricate axes, so the supervisor knows the probe
missed and can pivot to another shape.
"""

from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import AgentTask, RunContext, function_tool

from agent.state import CompassResult

logger = logging.getLogger("sorting-hat.probe.compass")

# prompts/ sits beside agent/ at the repo root: compass.py -> probes -> agent -> root.
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "probe_compass.md"
)


def load_probe_prompt() -> str:
    """Read and return the Compass probe sub-interview prompt.

    The prompt is the contract for the probe's behaviour, so a missing or
    empty file is a hard error — there is no sensible degraded mode for a
    sub-interview with no instructions.
    """
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(
            f"compass probe prompt not found at {_PROMPT_PATH} — G6 requires "
            "prompts/probe_compass.md to exist"
        )
    text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"compass probe prompt at {_PROMPT_PATH} is empty")
    return text


class CompassProbeTask(AgentTask[CompassResult]):
    """A focused sub-interview that locates the person on two axes.

    A LiveKit ``AgentTask`` parameterised to return a
    :class:`~agent.state.CompassResult`. Its ``instructions`` are the probe
    prompt loaded from ``prompts/probe_compass.md`` — appended to whatever
    conversation context the supervisor passes in, so the probe is continuous
    with the warm interviewer voice rather than a fresh agent.

    The task runs until the LLM calls :meth:`record_compass_result`, which
    validates the gathered data into a ``CompassResult`` and calls
    ``self.complete(result)``. The supervisor awaits the task and reads the
    typed result off it.
    """

    def __init__(self) -> None:
        """Build the probe with the sub-interview prompt as its instructions."""
        super().__init__(instructions=load_probe_prompt())

    # TODO(G8): the supervisor in agent/interviewer.py constructs and awaits
    # this task once the base questions are done and the leading signal is
    # `compass`. It passes the running chat context so the probe continues the
    # same conversation, then writes the returned CompassResult onto
    # InterviewState.compass_result and sets chosen_template = "compass".
    # If the result comes back thin, the supervisor pivots to another probe.

    @function_tool
    async def record_compass_result(
        self,
        ctx: RunContext,
        axis_1_negative_pole: str,
        axis_1_positive_pole: str,
        axis_1_position: float,
        axis_2_negative_pole: str,
        axis_2_positive_pole: str,
        axis_2_position: float,
        why_these_axes: str,
        thin: bool = False,
    ) -> str:
        """Record the two axes and the person's position, completing the probe.

        Call this exactly once, when the conversation has surfaced two
        independent axes — each with both poles named — and the person's
        position on each. Do not announce the call or read the fields back.

        Args:
            axis_1_negative_pole: The pole of axis 1 sitting at position -1.0.
            axis_1_positive_pole: The pole of axis 1 sitting at position +1.0.
            axis_1_position: Where the person sits on axis 1, in -1.0..1.0
                (0.0 is dead centre).
            axis_2_negative_pole: The pole of axis 2 sitting at position -1.0.
            axis_2_positive_pole: The pole of axis 2 sitting at position +1.0.
            axis_2_position: Where the person sits on axis 2, in -1.0..1.0.
            why_these_axes: Up to ~300 chars on why these two axes capture
                this person. If ``thin`` is true, say plainly here what the
                conversation read like instead (a tension, a trajectory, no
                stable position).
            thin: Set true when the person did not come back as a stable
                position — torn rather than located, or no firm coordinates.
                A thin result honestly flagged lets the supervisor pivot;
                fabricated axes do not.

        Returns:
            A short confirmation string for the LLM's tool-call history.
        """
        # Positions are clamped into range before validation so a model that
        # overshoots (-1.4, 1.2) still yields a usable result rather than a
        # pydantic ValidationError that would crash the probe.
        pos_1 = _clamp(axis_1_position)
        pos_2 = _clamp(axis_2_position)

        result = CompassResult(
            axis_1_poles=(axis_1_negative_pole, axis_1_positive_pole),
            axis_1_position=pos_1,
            axis_2_poles=(axis_2_negative_pole, axis_2_positive_pole),
            axis_2_position=pos_2,
            why_these_axes=why_these_axes,
        )

        if thin:
            logger.info(
                "compass probe completing THIN — person did not resolve into "
                "a stable position; supervisor should pivot"
            )
        else:
            logger.info(
                "compass probe complete: axis_1=%r @ %.2f, axis_2=%r @ %.2f",
                result.axis_1_poles,
                result.axis_1_position,
                result.axis_2_poles,
                result.axis_2_position,
            )

        self.complete(result)
        return "compass result recorded; probe complete"


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


__all__ = ["CompassProbeTask", "load_probe_prompt"]
