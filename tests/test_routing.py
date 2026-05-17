"""G8 tests — supervisor routing in :class:`InterviewerAgent`.

Scripted tests only: NO live LLM, no LiveKit room, no real probe ``AgentTask``.
The four probes are replaced with fake runners — plain coroutines that return
a :class:`ProbeOutcome` — so the supervisor's routing *logic* is proven in
isolation:

1. After the five base questions, the supervisor selects the leading-signal
   probe (and falls back deterministically when there is no signal).
2. A thin result triggers a pivot to the next-strongest probe not yet tried.
3. A full result sets ``chosen_template`` and the matching result field and
   ends routing.
4. The pivot cap stops the supervisor looping through all four shapes — after
   a bounded number of thin probes it closes with the best result it has.
5. A full scripted interview path — five base questions then routing — lands
   a template and closes cleanly.

These run fully offline.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.interviewer import (
    BASE_QUESTION_COUNT,
    DEFAULT_PROBE_RUNNERS,
    MAX_PROBE_ATTEMPTS,
    TEMPLATE_ORDER,
    InterviewerAgent,
    ProbeOutcome,
)
from agent.state import (
    ArcResult,
    CompassResult,
    IcebergResult,
    InterviewState,
    TwoButtonsResult,
)

# ---------------------------------------------------------------------------
# Typed-result fixtures — one valid result per template
# ---------------------------------------------------------------------------


def _iceberg_result() -> IcebergResult:
    return IcebergResult(
        surface="The capable, unflappable one everyone leans on.",
        first_layer="Quietly tired of being the dependable one.",
        second_layer="Suspects the competence is a costume.",
        abyss="Afraid there is nothing underneath the usefulness.",
    )


def _two_buttons_result() -> TwoButtonsResult:
    return TwoButtonsResult(
        button_a_label="Stay and build",
        button_a_seduction="The work is finally his and walking away would waste years.",
        button_b_label="Leave and be free",
        button_b_seduction="A clean slate somewhere new is the life he keeps picturing.",
        impossibility="Each option is only worth having if he commits fully to it.",
    )


def _compass_result() -> CompassResult:
    return CompassResult(
        axis_1_poles=("structure", "improvisation"),
        axis_1_position=0.4,
        axis_2_poles=("solo", "collective"),
        axis_2_position=-0.2,
        why_these_axes="These two axes capture how she decides and who she decides with.",
    )


def _arc_result() -> ArcResult:
    return ArcResult(
        before="He measured himself entirely by other people's approval.",
        catalyst="A burnout forced a year of doing nothing that mattered to anyone.",
        middle="He is relearning what he wants when no one is watching.",
        after="He senses a self that holds up without an audience.",
    )


_RESULT_BUILDERS = {
    "iceberg": _iceberg_result,
    "two_buttons": _two_buttons_result,
    "compass": _compass_result,
    "arc": _arc_result,
}


# ---------------------------------------------------------------------------
# Fake probe runners — coroutines returning a ProbeOutcome, no AgentTask
# ---------------------------------------------------------------------------


def _make_runner(template: str, *, thin: bool, log: list[str] | None = None):
    """Build a fake probe runner for ``template``.

    The returned coroutine records that it ran (into ``log`` if given) and
    returns a :class:`ProbeOutcome` carrying a valid typed result for the
    template and the requested ``thin`` flag.
    """

    async def _runner() -> ProbeOutcome:
        if log is not None:
            log.append(template)
        return ProbeOutcome(
            template=template,
            result=_RESULT_BUILDERS[template](),
            thin=thin,
        )

    return _runner


def _all_thin_runners(log: list[str] | None = None) -> dict:
    """A registry where every probe comes back thin."""
    return {t: _make_runner(t, thin=True, log=log) for t in TEMPLATE_ORDER}


def _all_full_runners(log: list[str] | None = None) -> dict:
    """A registry where every probe comes back full."""
    return {t: _make_runner(t, thin=False, log=log) for t in TEMPLATE_ORDER}


def _advance_through_base_questions(agent: InterviewerAgent) -> None:
    """Drive the interviewer through all five base-question threads."""
    for _ in range(BASE_QUESTION_COUNT):
        agent.advance_base_question()
    assert agent.base_questions_done is True


# ---------------------------------------------------------------------------
# 1. Selecting the leading-signal probe
# ---------------------------------------------------------------------------


def test_supervisor_selects_leading_signal_probe() -> None:
    """After the base questions, routing runs the highest-signal probe first."""
    state = InterviewState(compass_signal=0.9, iceberg_signal=0.3)
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners(log))

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    assert outcome is not None
    # Compass led the signal weights, so the compass probe ran first.
    assert log[0] == "compass"
    assert outcome.template == "compass"


def test_supervisor_uses_fallback_order_with_no_signal() -> None:
    """With every signal at 0.0 the supervisor falls back to the fixed order."""
    state = InterviewState()  # all signals 0.0
    assert state.leading_template() is None
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners(log))

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    # The first template in the fixed fallback order is chosen.
    assert log[0] == TEMPLATE_ORDER[0]
    assert outcome is not None
    assert outcome.template == TEMPLATE_ORDER[0]


def test_routing_refuses_to_run_before_base_questions_done() -> None:
    """route_to_probe() called early is a guarded no-op, not a probe run."""
    state = InterviewState(arc_signal=1.0)
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners(log))

    # Only three of five base threads done.
    for _ in range(3):
        agent.advance_base_question()
    outcome = asyncio.run(agent.route_to_probe())

    assert outcome is None
    assert log == []  # no probe was run
    assert state.chosen_template is None


# ---------------------------------------------------------------------------
# 2. A thin result triggers a pivot
# ---------------------------------------------------------------------------


def test_thin_result_pivots_to_next_strongest_probe() -> None:
    """A thin lead probe pivots to the next-highest-signal probe."""
    # arc leads, two_buttons second.
    state = InterviewState(arc_signal=0.9, two_buttons_signal=0.6, iceberg_signal=0.1)
    log: list[str] = []
    runners = {
        "arc": _make_runner("arc", thin=True, log=log),  # lead comes back thin
        "two_buttons": _make_runner("two_buttons", thin=False, log=log),  # full
        "compass": _make_runner("compass", thin=True, log=log),
        "iceberg": _make_runner("iceberg", thin=True, log=log),
    }
    agent = InterviewerAgent(state=state, probe_runners=runners)

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    # arc ran first (thin), then pivoted to two_buttons (full) and stopped.
    assert log == ["arc", "two_buttons"]
    assert outcome is not None
    assert outcome.template == "two_buttons"
    assert state.chosen_template == "two_buttons"


def test_pivot_never_reruns_an_already_attempted_probe() -> None:
    """Pivoting walks to untried probes only — no probe runs twice."""
    state = InterviewState(
        iceberg_signal=0.9,
        compass_signal=0.7,
        arc_signal=0.5,
        two_buttons_signal=0.3,
    )
    log: list[str] = []
    # iceberg + compass thin, arc full.
    runners = {
        "iceberg": _make_runner("iceberg", thin=True, log=log),
        "compass": _make_runner("compass", thin=True, log=log),
        "arc": _make_runner("arc", thin=False, log=log),
        "two_buttons": _make_runner("two_buttons", thin=True, log=log),
    }
    agent = InterviewerAgent(state=state, probe_runners=runners)

    _advance_through_base_questions(agent)
    asyncio.run(agent.route_to_probe())

    # Each probe attempted at most once, in descending-signal order.
    assert log == ["iceberg", "compass", "arc"]
    assert len(set(agent.probes_attempted)) == len(agent.probes_attempted)


# ---------------------------------------------------------------------------
# 3. A full result sets chosen_template + the result field and ends routing
# ---------------------------------------------------------------------------


def test_full_result_sets_chosen_template_and_result_field() -> None:
    """A full probe result lands: chosen_template + matching field are set."""
    state = InterviewState(iceberg_signal=1.0)
    agent = InterviewerAgent(
        state=state, probe_runners=_all_full_runners()
    )

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    assert outcome is not None and outcome.thin is False
    assert state.chosen_template == "iceberg"
    # The iceberg result field is populated; the other three stay None.
    assert isinstance(state.iceberg_result, IcebergResult)
    assert state.two_buttons_result is None
    assert state.compass_result is None
    assert state.arc_result is None


def test_full_result_ends_routing_immediately() -> None:
    """A full lead probe ends routing — no further probes run."""
    state = InterviewState(two_buttons_signal=0.8)
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners(log))

    _advance_through_base_questions(agent)
    asyncio.run(agent.route_to_probe())

    # Exactly one probe ran — the lead — because it landed full.
    assert log == ["two_buttons"]
    assert agent.probes_attempted == ("two_buttons",)
    assert agent.routing_done is True


@pytest.mark.parametrize("template", list(TEMPLATE_ORDER))
def test_each_template_lands_on_its_own_result_field(template: str) -> None:
    """For every template, a full probe writes onto exactly its result field."""
    # Give this template the only signal so it leads.
    state = InterviewState(**{f"{template}_signal": 1.0})
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners())

    _advance_through_base_questions(agent)
    asyncio.run(agent.route_to_probe())

    assert state.chosen_template == template
    field_map = {
        "iceberg": "iceberg_result",
        "two_buttons": "two_buttons_result",
        "compass": "compass_result",
        "arc": "arc_result",
    }
    assert getattr(state, field_map[template]) is not None
    # Every other result field is still None.
    for other, field in field_map.items():
        if other != template:
            assert getattr(state, field) is None


# ---------------------------------------------------------------------------
# 4. The pivot cap prevents infinite looping
# ---------------------------------------------------------------------------


def test_pivot_cap_stops_after_bounded_thin_probes() -> None:
    """All-thin probes stop at the cap — not after looping all four shapes."""
    state = InterviewState(
        iceberg_signal=0.9,
        compass_signal=0.7,
        arc_signal=0.5,
        two_buttons_signal=0.3,
    )
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_thin_runners(log))

    _advance_through_base_questions(agent)
    asyncio.run(agent.route_to_probe())

    # Exactly MAX_PROBE_ATTEMPTS probes ran — capped below the four templates.
    assert len(log) == MAX_PROBE_ATTEMPTS
    assert MAX_PROBE_ATTEMPTS < len(TEMPLATE_ORDER)
    assert len(agent.probes_attempted) == MAX_PROBE_ATTEMPTS


def test_pivot_cap_closes_with_best_result_it_has() -> None:
    """At the cap with only thin probes, routing still closes with a verdict."""
    state = InterviewState(compass_signal=0.9)
    agent = InterviewerAgent(state=state, probe_runners=_all_thin_runners())

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    # Routing is done and a template was chosen despite every probe being thin.
    assert agent.routing_done is True
    assert state.chosen_template is not None
    assert outcome is not None
    # The chosen template's result field is populated so the interview closes
    # with a defined verdict rather than an empty one.
    field_map = {
        "iceberg": "iceberg_result",
        "two_buttons": "two_buttons_result",
        "compass": "compass_result",
        "arc": "arc_result",
    }
    assert getattr(state, field_map[state.chosen_template]) is not None


def test_routing_does_not_call_route_again_after_landing() -> None:
    """Re-entering route_to_probe after a landing does not re-run probes."""
    state = InterviewState(arc_signal=1.0)
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_thin_runners(log))

    _advance_through_base_questions(agent)
    asyncio.run(agent.route_to_probe())
    first_count = len(log)
    assert first_count == MAX_PROBE_ATTEMPTS

    # A second call must not run more probes — the cap is already reached.
    asyncio.run(agent.route_to_probe())
    assert len(log) == first_count


# ---------------------------------------------------------------------------
# 5. A full scripted interview path lands a template and closes
# ---------------------------------------------------------------------------


def test_full_scripted_interview_lands_and_closes() -> None:
    """Five base questions, then routing — a clean interview that sorts."""
    # A scripted "willing user" whose Compass signal accumulated highest.
    state = InterviewState(
        iceberg_signal=0.2,
        two_buttons_signal=0.1,
        compass_signal=0.85,
        arc_signal=0.3,
    )
    log: list[str] = []
    agent = InterviewerAgent(state=state, probe_runners=_all_full_runners(log))

    # The base interview.
    assert agent.base_questions_completed == 0
    assert agent.routing_done is False
    _advance_through_base_questions(agent)
    assert agent.base_questions_done is True
    assert state.chosen_template is None  # not sorted until routing runs

    # Routing.
    outcome = asyncio.run(agent.route_to_probe())

    # The interview landed on the leading template and closed.
    assert log == ["compass"]
    assert outcome is not None
    assert outcome.template == "compass"
    assert outcome.thin is False
    assert state.chosen_template == "compass"
    assert isinstance(state.compass_result, CompassResult)
    assert agent.routing_done is True


def test_scripted_interview_with_one_pivot_still_closes() -> None:
    """A scripted run where the lead probe is thin still lands via one pivot."""
    # Iceberg leads the signal but the probe comes back thin; the person is
    # really a tension type — two_buttons is second and lands full.
    state = InterviewState(
        iceberg_signal=0.8,
        two_buttons_signal=0.7,
        compass_signal=0.1,
        arc_signal=0.05,
    )
    log: list[str] = []
    runners = {
        "iceberg": _make_runner("iceberg", thin=True, log=log),
        "two_buttons": _make_runner("two_buttons", thin=False, log=log),
        "compass": _make_runner("compass", thin=False, log=log),
        "arc": _make_runner("arc", thin=False, log=log),
    }
    agent = InterviewerAgent(state=state, probe_runners=runners)

    _advance_through_base_questions(agent)
    outcome = asyncio.run(agent.route_to_probe())

    assert log == ["iceberg", "two_buttons"]
    assert outcome is not None
    assert state.chosen_template == "two_buttons"
    assert isinstance(state.two_buttons_result, TwoButtonsResult)
    assert state.iceberg_result is None  # the thin probe wrote no verdict
    assert agent.routing_done is True


# ---------------------------------------------------------------------------
# Wiring — the default registry and main.py session
# ---------------------------------------------------------------------------


def test_default_probe_registry_covers_all_four_templates() -> None:
    """The default runner registry has one entry per locked template."""
    assert set(DEFAULT_PROBE_RUNNERS) == set(TEMPLATE_ORDER)
    assert len(DEFAULT_PROBE_RUNNERS) == 4


def test_interviewer_defaults_to_real_probe_registry() -> None:
    """With no registry passed, the agent uses the default (real-probe) one."""
    agent = InterviewerAgent()
    # The agent holds a copy of the default registry, keyed by all four labels.
    assert set(agent._probe_runners) == set(TEMPLATE_ORDER)


def test_main_session_wires_interviewstate_as_userdata() -> None:
    """agent.main.build_session puts an InterviewState on the session userdata.

    The supervisor's routing needs the session's userdata to BE the shared
    InterviewState; this asserts the G8 main.py wiring does that.
    """
    from agent.config import load_config
    from agent.main import build_session

    parts = build_session(load_config())
    assert isinstance(parts.state, InterviewState)
    assert parts.session.userdata is parts.state
