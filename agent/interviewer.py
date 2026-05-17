"""sorting-hat — G3 persona + G8 supervisor routing.

This is the brief's step 2 and step 4. The :class:`InterviewerAgent` is the
warm voice the interviewee actually talks to. It owns the five base
diagnostic questions, asks them in order, handles follow-ups — and, once the
base interview is done, acts as the **supervisor**: it reads the accumulated
signal weights, invokes the matching probe sub-interview, pivots to the
next-strongest probe on a thin result, and closes when a probe lands.

G3 built the base interview. G4–G7 built the four probe ``AgentTask``s. G8
(this file's :meth:`InterviewerAgent.route_to_probe`) wires them together:
the supervisor logic that turns five questions and four probes into one
interview that sorts a person and closes gracefully.

The persona prompt — the craft this whole project's quality rests on — lives
in ``prompts/persona.md`` and is loaded at construction time. Keeping it in a
file (not a string literal) means it can be iterated, reviewed, and diffed as
prose without touching code. Routing is logic, not dialogue: the probes carry
their own voice, continuous with the persona, so nothing here speaks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from livekit.agents import Agent

from agent.classifier import apply_scores, classify_turn
from agent.probes import (
    ArcProbeTask,
    CompassProbeTask,
    IcebergProbeTask,
    TwoButtonsProbeTask,
)
from agent.state import (
    TEMPLATE_SIGNALS,
    ArcResult,
    CompassResult,
    IcebergResult,
    InterviewState,
    TwoButtonsResult,
)

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
# Supervisor routing — probe invocation, thin detection, pivoting
# ---------------------------------------------------------------------------

# The four template labels, in the fixed fallback order the supervisor walks
# when there is no signal to lead on (every weight still at 0.0). Order matches
# TEMPLATE_SIGNALS so it is the same tie-break order leading_template() uses.
TEMPLATE_ORDER: tuple[str, ...] = tuple(TEMPLATE_SIGNALS.values())

# How many probes the supervisor will run before it stops pivoting and closes
# with the best result it has. Capped well below the four templates so a run
# of thin probes cannot loop through every shape endlessly — after this many
# attempts the interview closes regardless. Three attempts means: lead probe,
# then up to two pivots.
MAX_PROBE_ATTEMPTS: int = 3

# Map each template label to the InterviewState field its typed result lands
# on, so the supervisor can store whichever probe came back full.
_RESULT_FIELD: dict[str, str] = {
    "iceberg": "iceberg_result",
    "two_buttons": "two_buttons_result",
    "compass": "compass_result",
    "arc": "arc_result",
}


@dataclass
class ProbeOutcome:
    """What running one probe sub-interview produced.

    A probe returns its locked typed result (an :class:`IcebergResult`,
    :class:`TwoButtonsResult`, :class:`CompassResult` or :class:`ArcResult`)
    plus a ``thin`` flag the supervisor reads to decide whether to trust the
    portrait or pivot. The four probes each signal thinness differently — one
    flag here normalises that for the routing logic.

    Attributes:
        template: The short template label this probe deepened into.
        result: The probe's typed result. Always a valid, fully typed model,
            even on a thin run (a thin run records honest deflection notes
            rather than fabricated depth).
        thin: True when the probe did not land — the person did not resolve
            into this shape. A thin outcome triggers a pivot.
    """

    template: str
    result: object
    thin: bool


# A ProbeRunner runs one probe sub-interview to completion and returns its
# outcome. The default runners (below) await the real LiveKit AgentTask; tests
# inject fakes so routing can be proven with no live LLM and no session.
ProbeRunner = Callable[[], Awaitable[ProbeOutcome]]


async def _run_iceberg() -> ProbeOutcome:
    """Default runner for the Iceberg probe — awaits the real ``AgentTask``.

    The probe is awaited inside the supervisor's session (LiveKit v1.5.9:
    ``result = await IcebergProbeTask()``). The Iceberg probe carries its
    thin signal in the layer text rather than on the typed result, so
    :func:`_iceberg_is_thin` reads it back off the completed task/result.
    """
    task = IcebergProbeTask()
    result: IcebergResult = await task
    return ProbeOutcome("iceberg", result, _probe_thin(task))


async def _run_two_buttons() -> ProbeOutcome:
    """Default runner for the Two Buttons probe — awaits the real ``AgentTask``.

    The Two Buttons probe sets ``tension_is_real`` on the task when it
    records; a false value is the thin signal.
    """
    task = TwoButtonsProbeTask()
    result: TwoButtonsResult = await task
    return ProbeOutcome("two_buttons", result, _probe_thin(task))


async def _run_compass() -> ProbeOutcome:
    """Default runner for the Compass probe — awaits the real ``AgentTask``."""
    task = CompassProbeTask()
    result: CompassResult = await task
    return ProbeOutcome("compass", result, _probe_thin(task))


async def _run_arc() -> ProbeOutcome:
    """Default runner for the Arc probe — awaits the real ``AgentTask``."""
    task = ArcProbeTask()
    result: ArcResult = await task
    return ProbeOutcome("arc", result, _probe_thin(task))


def _probe_thin(task: object) -> bool:
    """Read a completed probe task's thin signal, normalised to a bool.

    The four probes were each built in isolation (G4–G7) and signal a thin
    result differently — Two Buttons sets ``tension_is_real`` on the task,
    others may set a ``thin``/``thin_result`` attribute. Routing must not
    change the probes, so it reads whatever signal is present defensively and
    treats a missing signal as *not thin* (a full result). The supervisor's
    pivot cap is the backstop if a probe never signals at all.
    """
    # Two Buttons: tension_is_real is the inverse of thin.
    tension_is_real = getattr(task, "tension_is_real", None)
    if tension_is_real is not None:
        return not bool(tension_is_real)
    # Iceberg / Compass / Arc: a thin flag set on the task, if any.
    for attr in ("thin", "thin_result", "thread_came_back_thin"):
        value = getattr(task, attr, None)
        if value is not None:
            return bool(value)
    # No signal exposed — treat as a full result; the pivot cap is the guard.
    return False


# The default probe registry: template label -> runner awaiting the real
# AgentTask. The supervisor looks runners up here; a test passes its own
# registry of fakes to InterviewerAgent so routing runs with no LLM.
DEFAULT_PROBE_RUNNERS: dict[str, ProbeRunner] = {
    "iceberg": _run_iceberg,
    "two_buttons": _run_two_buttons,
    "compass": _run_compass,
    "arc": _run_arc,
}


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

    def __init__(
        self,
        state: InterviewState | None = None,
        probe_runners: dict[str, ProbeRunner] | None = None,
    ) -> None:
        """Build the interviewer with the persona prompt as its instructions.

        ``state`` is the shared interview state. When omitted a fresh
        :class:`InterviewState` is created — convenient for tests and for a
        worker that constructs the agent before wiring ``userdata``.

        ``probe_runners`` maps each template label to the coroutine the
        supervisor awaits to run that probe. It defaults to
        :data:`DEFAULT_PROBE_RUNNERS`, which await the real LiveKit
        ``AgentTask`` probes. A test passes its own registry of fakes so the
        routing logic can be proven with no live LLM and no session.
        """
        super().__init__(instructions=load_persona())
        self._state: InterviewState = state if state is not None else InterviewState()
        self._probe_runners: dict[str, ProbeRunner] = (
            dict(probe_runners) if probe_runners is not None else dict(DEFAULT_PROBE_RUNNERS)
        )
        # Templates whose probe has already been run this interview — so a
        # pivot never re-runs a shape the supervisor already tried.
        self._probes_attempted: list[str] = []
        # In-flight background classifier tasks (G14). The classifier runs as
        # a parallel observer fired on each user turn — never awaited on the
        # critical path — so the agent holds references here both to keep the
        # tasks from being garbage-collected mid-run and to cancel/await any
        # still pending when the session closes. See on_user_turn().
        self._classifier_tasks: set[asyncio.Task] = set()

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

    # --- Background classifier (G14, observer pattern) --------------------

    @property
    def classifier_tasks(self) -> tuple[asyncio.Task, ...]:
        """The background classifier tasks currently tracked, in no order.

        Exposed for the live worker's shutdown path and for tests asserting
        an observer task was actually fired.
        """
        return tuple(self._classifier_tasks)

    def on_user_turn(self, user_response: str) -> Optional[asyncio.Task]:
        """Observer hook: fire the background classifier for one user turn.

        This is the brief's step 6 — LiveKit's observer pattern. Called
        once per completed user turn (in the live worker, off the session's
        ``user_input_transcribed`` final-transcript event). It fires
        :func:`~agent.classifier.classify_turn` as a background
        ``asyncio.create_task`` and returns **immediately** — the classifier
        is NOT awaited here, so it never blocks the next agent turn.

        When the task later resolves, :meth:`_absorb_classifier_result`
        accumulates its scores into the shared :class:`InterviewState`
        signal weights, so the supervisor's later
        :meth:`InterviewState.leading_template` read reflects evidence
        gathered across every turn.

        The task is tracked in :attr:`classifier_tasks` (so it is not
        garbage-collected mid-run and can be cleaned up at session close)
        and its result is absorbed via a done-callback that cannot raise
        into the session. An empty/whitespace response is a no-op — no task
        is fired — and ``None`` is returned.

        Returns the created :class:`asyncio.Task`, or ``None`` when no task
        was fired (empty input, or no running event loop).
        """
        if not user_response or not user_response.strip():
            return None

        try:
            task = asyncio.ensure_future(classify_turn(user_response))
        except RuntimeError:
            # No running event loop — nothing to fire onto. The interview
            # simply runs without mid-interview signal nudges; the supervisor
            # falls back to its deterministic template order.
            logger.warning(
                "on_user_turn() called with no running event loop — "
                "skipping the background classifier for this turn"
            )
            return None

        self._classifier_tasks.add(task)
        task.add_done_callback(self._on_classifier_done)
        logger.debug(
            "background classifier fired for a user turn (%d in flight)",
            len(self._classifier_tasks),
        )
        return task

    def _on_classifier_done(self, task: asyncio.Task) -> None:
        """Done-callback for a background classifier task — never raises.

        Runs when a :func:`~agent.classifier.classify_turn` task completes
        (resolved, failed, or cancelled). It drops the task from the
        tracking set and, on a normal result, absorbs the scores into the
        interview state. Any exception the task carried is logged and
        swallowed here so a failed classifier can never crash the session —
        :func:`classify_turn` is already built not to raise, but this is the
        belt-and-braces guard the observer pattern requires.
        """
        self._classifier_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "background classifier task ended with an exception — "
                "ignoring (signal weights unchanged)",
                exc_info=exc,
            )
            return
        self._absorb_classifier_result(task.result())

    def _absorb_classifier_result(self, scores: object) -> None:
        """Accumulate one classifier result into the interview signal weights.

        Delegates to :func:`agent.classifier.apply_scores`, which adds the
        four scores onto the matching ``*_signal`` fields of the shared
        :class:`InterviewState`. No-op (all-zero) scores leave the weights
        untouched. ``apply_scores`` never raises; this wrapper exists only
        to keep the absorb step a single named, testable seam.
        """
        apply_scores(self._state, scores)  # type: ignore[arg-type]

    async def aclose_classifiers(self) -> None:
        """Cancel and drain any in-flight background classifier tasks.

        Called from the live worker's session-shutdown path so a classifier
        task still waiting on a slow LLM hop does not outlive the interview.
        Each pending task is cancelled and awaited; exceptions are
        suppressed — shutdown must be quiet. Safe to call with no tasks
        in flight.
        """
        pending = [t for t in self._classifier_tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._classifier_tasks.clear()

    # --- Supervisor routing (G8) ------------------------------------------

    @property
    def probes_attempted(self) -> tuple[str, ...]:
        """Template labels whose probe has been run this interview, in order."""
        return tuple(self._probes_attempted)

    @property
    def routing_done(self) -> bool:
        """True once routing has settled — a probe has landed, or the cap hit.

        Routing is done when :attr:`InterviewState.chosen_template` is set (a
        probe came back full) or the supervisor has exhausted its probe
        attempts. Either way, the interview can close.
        """
        return (
            self._state.chosen_template is not None
            or len(self._probes_attempted) >= MAX_PROBE_ATTEMPTS
        )

    def _next_probe_template(self) -> Optional[str]:
        """Pick the next probe to run: the highest-signal template not yet tried.

        Uses :meth:`InterviewState.leading_template` for the signal-ranked
        choice, walking down the signal weights to skip any template already
        attempted. When every signal is still at 0.0 — no evidence to lead on
        — it falls back to the fixed :data:`TEMPLATE_ORDER` so routing still
        makes a deterministic, defensible choice rather than stalling.

        Returns the chosen template label, or ``None`` when all four have
        already been attempted.
        """
        # Signal-ranked: sort the four weights high-to-low, take the first
        # template not yet tried. Ties fall back to TEMPLATE_SIGNALS order
        # (Python's sort is stable and signal_weights() preserves that order).
        weights = self._state.signal_weights()
        ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
        for label, weight in ranked:
            if weight > 0.0 and label not in self._probes_attempted:
                return label

        # No signal (or every signalled template already tried): walk the
        # fixed fallback order for the first untried template.
        for label in TEMPLATE_ORDER:
            if label not in self._probes_attempted:
                return label

        # All four templates have been attempted.
        return None

    def _store_full_result(self, outcome: ProbeOutcome) -> None:
        """Record a full (non-thin) probe outcome as the interview's verdict.

        Sets :attr:`InterviewState.chosen_template` to the probe's template
        and writes its typed result onto the matching ``*_result`` field.
        This is the sort landing — after this the interview closes.
        """
        self._state.chosen_template = outcome.template
        field = _RESULT_FIELD[outcome.template]
        setattr(self._state, field, outcome.result)
        logger.info(
            "probe '%s' landed — chosen_template set, %s populated",
            outcome.template,
            field,
        )

    async def route_to_probe(self) -> Optional[ProbeOutcome]:
        """Supervisor routing — invoke probes after the base interview.

        This is the brief's step 4 and the G8 outcome. Called once the five
        base questions are complete, it:

        1. reads the accumulated signal weights via
           :meth:`InterviewState.leading_template` (with a deterministic
           fallback when there is no signal yet);
        2. runs the matching probe sub-interview, awaiting its typed result;
        3. inspects the result — a **full** result is the sort: it sets
           ``chosen_template`` and the matching result field, and routing
           ends; a **thin** result is recorded and the supervisor *pivots*
           to the next-highest-signal probe not yet tried;
        4. caps the number of probes at :data:`MAX_PROBE_ATTEMPTS` so a run
           of thin probes cannot loop through every shape endlessly — after
           the cap the interview closes with the best result it has.

        Routing is logic, not dialogue: each probe carries its own voice,
        continuous with the persona, so this method itself never speaks.

        Returns the :class:`ProbeOutcome` the interview settled on — the full
        result if one landed, otherwise the last thin outcome seen (or
        ``None`` if no probe ran at all). The authoritative verdict is always
        on :class:`InterviewState`.
        """
        if not self.base_questions_done:
            logger.warning(
                "route_to_probe() called before the base interview is "
                "complete (%d/%d) — ignoring",
                self.base_questions_completed,
                BASE_QUESTION_COUNT,
            )
            return None

        last_outcome: Optional[ProbeOutcome] = None

        while len(self._probes_attempted) < MAX_PROBE_ATTEMPTS:
            template = self._next_probe_template()
            if template is None:
                # Every template has been attempted; nothing left to pivot to.
                logger.info("supervisor: all probes attempted, closing")
                break

            runner = self._probe_runners.get(template)
            if runner is None:
                # No runner registered for this template — skip it rather than
                # stall. Mark it attempted so the loop makes progress.
                logger.warning(
                    "supervisor: no probe runner for '%s' — skipping", template
                )
                self._probes_attempted.append(template)
                continue

            attempt = len(self._probes_attempted) + 1
            logger.info(
                "supervisor: probe attempt %d/%d -> '%s'",
                attempt,
                MAX_PROBE_ATTEMPTS,
                template,
            )
            outcome = await runner()
            self._probes_attempted.append(template)
            last_outcome = outcome

            if not outcome.thin:
                # A full result — the sort lands here. Record it and close.
                self._store_full_result(outcome)
                return outcome

            # Thin: the probe did not land. Record nothing as the verdict and
            # pivot to the next-strongest untried probe.
            logger.info(
                "supervisor: probe '%s' came back thin — pivoting", template
            )

        # The cap was reached (or all templates were exhausted) with no full
        # result. The interview closes with the best result it has — the last
        # thin outcome — rather than looping forever.
        if last_outcome is not None and self._state.chosen_template is None:
            logger.info(
                "supervisor: probe cap reached after %d thin probe(s) — "
                "closing with the best result from '%s'",
                len(self._probes_attempted),
                last_outcome.template,
            )
            self._store_full_result(last_outcome)
        return last_outcome


__all__ = [
    "InterviewerAgent",
    "BASE_QUESTIONS",
    "BASE_QUESTION_COUNT",
    "load_persona",
    "ProbeOutcome",
    "ProbeRunner",
    "DEFAULT_PROBE_RUNNERS",
    "MAX_PROBE_ATTEMPTS",
    "TEMPLATE_ORDER",
    "classify_turn",
    "apply_scores",
]
