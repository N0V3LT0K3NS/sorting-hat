"""sorting-hat — G4: the Iceberg probe ``AgentTask``.

The Iceberg is the *depth* shape: a person is who they are because of what is
hidden beneath their normal surface, layered down to floors most people never
see. :class:`IcebergProbeTask` is a focused voice sub-interview that excavates
four layers — surface, first hidden layer, second hidden layer, abyss — and
returns a typed :class:`~agent.state.IcebergResult`.

It is a LiveKit ``AgentTask`` parameterised to that result type. The LLM runs
the probe prompt (``prompts/probe_iceberg.md``), then calls the
``record_iceberg`` function tool exactly once; the tool builds the typed
result and calls ``self.complete()`` to finish the task and hand the result
back to whoever invoked it.

The supervisor in ``agent/interviewer.py`` constructs and runs this task —
``await IcebergProbeTask()`` — once the base questions are done and the
Iceberg signal leads. The supervisor stores the returned ``IcebergResult`` on
``InterviewState.iceberg_result`` and sets ``chosen_template = "iceberg"``. A
result with ``thin_result`` set tells the supervisor the probe did not land so
it can pivot to another shape. This module defines the probe in isolation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import AgentTask, function_tool

from agent.state import IcebergResult

logger = logging.getLogger("sorting-hat.probe.iceberg")

# ---------------------------------------------------------------------------
# Probe prompt — loaded from prompts/probe_iceberg.md
# ---------------------------------------------------------------------------

# prompts/ sits beside agent/ at the repo root: iceberg.py -> probes -> agent -> root.
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "probe_iceberg.md"
)


def load_probe_prompt() -> str:
    """Read and return the Iceberg probe sub-interview prompt.

    The prompt is the contract for the probe's behaviour, so a missing or
    empty file is a hard error — there is no sensible degraded mode for a
    depth probe with no instructions.
    """
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(
            f"Iceberg probe prompt not found at {_PROMPT_PATH} — G4 requires "
            "prompts/probe_iceberg.md to exist"
        )
    text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Iceberg probe prompt at {_PROMPT_PATH} is empty")
    return text


# ---------------------------------------------------------------------------
# IcebergProbeTask
# ---------------------------------------------------------------------------


class IcebergProbeTask(AgentTask[IcebergResult]):
    """The Iceberg probe — a depth-excavating voice sub-interview.

    An ``AgentTask`` parameterised to return an :class:`IcebergResult`. Its
    ``instructions`` are the probe prompt from ``prompts/probe_iceberg.md``;
    its single tool, ``record_iceberg``, is what the LLM calls to finish.

    Run it by awaiting the task from inside another agent::

        result: IcebergResult = await IcebergProbeTask()

    The awaited value is whatever ``record_iceberg`` passed to
    ``self.complete()`` — a fully typed, char-limit-validated result.
    """

    def __init__(self) -> None:
        """Build the probe with the loaded prompt and the record tool."""
        super().__init__(
            instructions=load_probe_prompt(),
            tools=[self._build_record_tool()],
        )

    # --- The completion tool ----------------------------------------------

    def _build_record_tool(self):
        """Build the ``record_iceberg`` function tool bound to this task.

        The tool is defined as a closure over ``self`` so it can call
        :meth:`complete`. It is the single exit path of the probe: the LLM
        calls it once with the four excavated layers, the tool constructs and
        validates an :class:`IcebergResult`, completes the task with it, and
        reports back to the LLM.
        """

        @function_tool
        async def record_iceberg(
            surface: str,
            first_layer: str,
            second_layer: str,
            abyss: str,
            thin_result: bool = False,
            thin_reason: str = "",
        ) -> str:
            """Record the four excavated Iceberg layers and end the probe.

            Call this exactly once, when the depth sub-interview has reached
            the bottom of what the person will give. Each layer is a short
            phrase or sentence (about 120 characters) in your own words,
            faithful to what they said — not a long quote.

            Args:
                surface: What the person shows the world — their public self.
                first_layer: What is lightly hidden, just under the surface.
                second_layer: What is deeper, rarely spoken aloud.
                abyss: The bottom layer — barely admitted, even privately.
                thin_result: Set true if the person would not go deep and
                    some layers are deflections rather than real depth.
                thin_reason: When ``thin_result`` is true, one line on how the
                    probe came back thin so the supervisor can pivot.

            Returns:
                A short confirmation string for the LLM. The probe is over
                once this returns.
            """
            try:
                result = IcebergResult(
                    surface=surface,
                    first_layer=first_layer,
                    second_layer=second_layer,
                    abyss=abyss,
                )
            except Exception as exc:  # pydantic validation (e.g. char limits)
                # Surface the problem to the LLM so it can re-call the tool
                # with shorter layers rather than crashing the probe.
                logger.warning("record_iceberg got an invalid result: %s", exc)
                return (
                    f"That didn't validate ({exc}). Each layer must be about "
                    "120 characters — shorten them and call record_iceberg again."
                )

            if thin_result:
                logger.info(
                    "Iceberg probe completed THIN: %s",
                    thin_reason or "(no reason given)",
                )
            else:
                logger.info("Iceberg probe completed with a full four-layer result")

            self.complete(result)
            return "Recorded. The conversation can close now."

        return record_iceberg


__all__ = ["IcebergProbeTask", "load_probe_prompt"]
