"""G5 — the Two Buttons probe ``AgentTask``.

Two Buttons is the *tension* template: a person defined by an unresolved
pull between two pseudo-equivalent options they cannot collapse into one
choice. This probe is a short sub-interview that deepens into that pull and
returns a :class:`~agent.state.TwoButtonsResult`.

The probe is a LiveKit :class:`~livekit.agents.AgentTask`. Its instructions
are the prose prompt in ``prompts/probe_two_buttons.md`` — kept in a file,
like the persona, so the craft can be iterated and diffed without touching
code. The LLM ends the probe by calling the single ``record_two_buttons_result``
function tool, which validates the arguments into a ``TwoButtonsResult`` and
calls :meth:`AgentTask.complete`.

A probe may come back *thin*: if the person can simply choose, there is no
real tension, and the probe must say so rather than fabricate a dilemma. The
``tension_is_real`` flag on the result carries that signal so the G8
supervisor can pivot to a different template instead of trusting a false
portrait.

TODO(G8): the supervisor in ``agent/interviewer.py`` constructs this task
after the base questions when ``two_buttons`` leads the signal weights, runs
it with ``await TwoButtonsProbeTask()``, and stores the returned
``TwoButtonsResult`` on ``InterviewState.two_buttons_result`` (pivoting if
``tension_is_real`` is false). Nothing invokes this probe yet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from livekit.agents import AgentTask, RunContext, function_tool

from agent.state import TwoButtonsResult

logger = logging.getLogger("sorting-hat.probes.two_buttons")

# prompts/ sits beside agent/ at the repo root:
# two_buttons.py -> probes -> agent -> root.
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "prompts"
    / "probe_two_buttons.md"
)


def load_probe_prompt() -> str:
    """Read and return the Two Buttons probe sub-interview prompt.

    The prompt is the contract for the probe's behaviour, so a missing or
    empty file is a hard error — there is no sensible degraded mode for a
    probe with no instructions.
    """
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(
            f"Two Buttons probe prompt not found at {_PROMPT_PATH} — G5 "
            "requires prompts/probe_two_buttons.md to exist"
        )
    text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"probe prompt at {_PROMPT_PATH} is empty")
    return text


class TwoButtonsProbeTask(AgentTask[TwoButtonsResult]):
    """The Two Buttons probe — a focused sub-interview on an unresolved pull.

    Subclasses :class:`~livekit.agents.AgentTask` parametrised on
    :class:`~agent.state.TwoButtonsResult`: awaiting the task yields exactly
    that typed result. The task's instructions are loaded from
    ``prompts/probe_two_buttons.md`` and it exposes one function tool,
    ``record_two_buttons_result``, which the LLM calls once to record what it
    found and complete the probe.
    """

    def __init__(self) -> None:
        """Build the probe with the prompt file as its instructions.

        The probe inherits STT/LLM/TTS from the live ``AgentSession`` it runs
        inside (the supervisor's session), so nothing model-related is wired
        here — only the instructions and the single recording tool.
        """
        super().__init__(
            instructions=load_probe_prompt(),
            tools=[self.record_two_buttons_result],
        )

    @function_tool
    async def record_two_buttons_result(
        self,
        ctx: RunContext,
        button_a_label: str,
        button_a_seduction: str,
        button_b_label: str,
        button_b_seduction: str,
        impossibility: str,
        tension_is_real: bool,
        notes: Optional[str] = None,
    ) -> str:
        """Record the Two Buttons probe result and end the probe.

        The LLM calls this exactly once, when it has understood both pulls
        and the impossibility — or when it has confirmed the tension is not
        real. It validates the arguments into a :class:`TwoButtonsResult` and
        completes the task with it.

        Args:
            button_a_label: Short name for the first pull (~4 words).
            button_a_seduction: One sentence on what makes A genuinely tempting.
            button_b_label: Short name for the second pull (~4 words).
            button_b_seduction: One sentence on what makes B genuinely
                tempting — at full strength, never as the compromise.
            impossibility: Why this person cannot simply have both or pick
                one and be at peace.
            tension_is_real: True if a genuine unresolved pull was found;
                False if the person chose cleanly and the dilemma did not
                hold up — the signal the supervisor uses to pivot.
            notes: Optional context for the supervisor — confidence, what the
                forced choice surfaced, or what was found instead on a thin
                result.

        Returns:
            A short confirmation string handed back to the LLM.
        """
        result = TwoButtonsResult(
            button_a_label=button_a_label,
            button_a_seduction=button_a_seduction,
            button_b_label=button_b_label,
            button_b_seduction=button_b_seduction,
            impossibility=impossibility,
        )

        if tension_is_real:
            logger.info(
                "Two Buttons probe complete — genuine tension between "
                "%r and %r",
                button_a_label,
                button_b_label,
            )
        else:
            logger.info(
                "Two Buttons probe came back THIN — no real tension "
                "(notes: %s); supervisor should pivot",
                notes or "(none given)",
            )

        # Carry the thin-result signal alongside the typed result. The
        # supervisor inspects these to decide whether to trust the portrait
        # or pivot; TwoButtonsResult itself is the locked render schema and
        # does not change, so the probe-level metadata rides on the task.
        self.tension_is_real = tension_is_real
        self.notes = notes

        self.complete(result)
        return "Recorded. Thank them warmly and let the conversation land."


__all__ = ["TwoButtonsProbeTask", "load_probe_prompt"]
