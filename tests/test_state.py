"""Unit tests for the pure data layer in ``agent/state.py``.

Covers construction, defaults, char-limit enforcement, the compass
position-range validation, and the leading-template helper.
"""

import pytest
from pydantic import ValidationError

from agent.state import (
    ArcResult,
    CompassResult,
    IcebergResult,
    InterviewState,
    TwoButtonsResult,
)


# ---------------------------------------------------------------------------
# IcebergResult
# ---------------------------------------------------------------------------


def test_iceberg_result_construction():
    r = IcebergResult(
        surface="Calm and capable",
        first_layer="Quietly exhausted by always being the capable one",
        second_layer="Afraid that the competence is the only thing loved",
        abyss="Suspects no one has ever met the person under the competence",
    )
    assert r.surface == "Calm and capable"
    assert r.abyss.startswith("Suspects")


def test_iceberg_char_limit_enforced():
    with pytest.raises(ValidationError):
        IcebergResult(
            surface="x" * 121,
            first_layer="ok",
            second_layer="ok",
            abyss="ok",
        )


def test_iceberg_char_limit_boundary_ok():
    # Exactly 120 chars is allowed on every layer.
    r = IcebergResult(
        surface="s" * 120,
        first_layer="f" * 120,
        second_layer="d" * 120,
        abyss="a" * 120,
    )
    assert len(r.surface) == 120
    assert len(r.abyss) == 120


def test_iceberg_missing_field_rejected():
    with pytest.raises(ValidationError):
        IcebergResult(surface="a", first_layer="b", second_layer="c")


# ---------------------------------------------------------------------------
# TwoButtonsResult
# ---------------------------------------------------------------------------


def test_two_buttons_result_construction():
    r = TwoButtonsResult(
        button_a_label="Stay and be safe",
        button_a_seduction="The known life keeps every relationship and certainty intact.",
        button_b_label="Leave and be free",
        button_b_seduction="A clean break finally lets the unlived life begin.",
        impossibility="Each option only has its meaning because it forecloses the other.",
    )
    assert r.button_a_label == "Stay and be safe"
    assert r.impossibility


def test_two_buttons_label_char_limit_enforced():
    with pytest.raises(ValidationError):
        TwoButtonsResult(
            button_a_label="x" * 41,
            button_a_seduction="ok",
            button_b_label="ok",
            button_b_seduction="ok",
            impossibility="ok",
        )


def test_two_buttons_seduction_char_limit_enforced():
    with pytest.raises(ValidationError):
        TwoButtonsResult(
            button_a_label="ok",
            button_a_seduction="x" * 161,
            button_b_label="ok",
            button_b_seduction="ok",
            impossibility="ok",
        )


def test_two_buttons_impossibility_char_limit_enforced():
    with pytest.raises(ValidationError):
        TwoButtonsResult(
            button_a_label="ok",
            button_a_seduction="ok",
            button_b_label="ok",
            button_b_seduction="ok",
            impossibility="x" * 201,
        )


# ---------------------------------------------------------------------------
# CompassResult
# ---------------------------------------------------------------------------


def test_compass_result_construction():
    r = CompassResult(
        axis_1_poles=("structure", "improvisation"),
        axis_1_position=-0.4,
        axis_2_poles=("solitude", "company"),
        axis_2_position=0.8,
        why_these_axes="These two tensions show up in every story the person told.",
    )
    assert r.axis_1_poles == ("structure", "improvisation")
    assert r.axis_1_position == -0.4
    assert r.axis_2_position == 0.8


def test_compass_position_range_boundaries_ok():
    r = CompassResult(
        axis_1_poles=("a", "b"),
        axis_1_position=-1.0,
        axis_2_poles=("c", "d"),
        axis_2_position=1.0,
        why_these_axes="boundary case",
    )
    assert r.axis_1_position == -1.0
    assert r.axis_2_position == 1.0


def test_compass_position_above_range_rejected():
    with pytest.raises(ValidationError):
        CompassResult(
            axis_1_poles=("a", "b"),
            axis_1_position=1.5,
            axis_2_poles=("c", "d"),
            axis_2_position=0.0,
            why_these_axes="out of range",
        )


def test_compass_position_below_range_rejected():
    with pytest.raises(ValidationError):
        CompassResult(
            axis_1_poles=("a", "b"),
            axis_1_position=0.0,
            axis_2_poles=("c", "d"),
            axis_2_position=-1.01,
            why_these_axes="out of range",
        )


def test_compass_rejects_empty_pole():
    with pytest.raises(ValidationError):
        CompassResult(
            axis_1_poles=("a", "   "),
            axis_1_position=0.0,
            axis_2_poles=("c", "d"),
            axis_2_position=0.0,
            why_these_axes="empty pole",
        )


def test_compass_rejects_wrong_pole_count():
    with pytest.raises(ValidationError):
        CompassResult(
            axis_1_poles=("a", "b", "c"),
            axis_1_position=0.0,
            axis_2_poles=("d", "e"),
            axis_2_position=0.0,
            why_these_axes="three poles",
        )


# ---------------------------------------------------------------------------
# ArcResult
# ---------------------------------------------------------------------------


def test_arc_result_construction():
    r = ArcResult(
        before="They believed effort alone would always be enough.",
        catalyst="A failure they could not out-work broke that belief.",
        middle="Now they are learning to ask for help without shame.",
        after="They expect to come out trusting other people more.",
    )
    assert r.before.startswith("They believed")
    assert r.after


def test_arc_char_limit_enforced():
    with pytest.raises(ValidationError):
        ArcResult(
            before="x" * 201,
            catalyst="ok",
            middle="ok",
            after="ok",
        )


# ---------------------------------------------------------------------------
# InterviewState — defaults
# ---------------------------------------------------------------------------


def test_interview_state_defaults():
    s = InterviewState()
    assert s.iceberg_signal == 0.0
    assert s.two_buttons_signal == 0.0
    assert s.compass_signal == 0.0
    assert s.arc_signal == 0.0
    assert s.base_questions_completed == 0
    assert s.chosen_template is None
    assert s.iceberg_result is None
    assert s.two_buttons_result is None
    assert s.compass_result is None
    assert s.arc_result is None
    assert s.transcript_log == []


def test_interview_state_transcript_log_is_independent():
    # default_factory must give each instance its own list.
    a = InterviewState()
    b = InterviewState()
    a.transcript_log.append("turn 1")
    assert a.transcript_log == ["turn 1"]
    assert b.transcript_log == []


def test_interview_state_negative_progress_rejected():
    with pytest.raises(ValidationError):
        InterviewState(base_questions_completed=-1)


def test_interview_state_carries_typed_results():
    iceberg = IcebergResult(
        surface="a", first_layer="b", second_layer="c", abyss="d"
    )
    s = InterviewState(chosen_template="iceberg", iceberg_result=iceberg)
    assert s.chosen_template == "iceberg"
    assert s.iceberg_result is iceberg


# ---------------------------------------------------------------------------
# InterviewState — leading_template helper
# ---------------------------------------------------------------------------


def test_leading_template_none_when_no_signal():
    s = InterviewState()
    assert s.leading_template() is None


def test_leading_template_picks_highest():
    s = InterviewState(
        iceberg_signal=0.2,
        two_buttons_signal=0.9,
        compass_signal=0.5,
        arc_signal=0.1,
    )
    assert s.leading_template() == "two_buttons"


def test_leading_template_each_winner():
    assert InterviewState(iceberg_signal=1.0).leading_template() == "iceberg"
    assert (
        InterviewState(two_buttons_signal=1.0).leading_template() == "two_buttons"
    )
    assert InterviewState(compass_signal=1.0).leading_template() == "compass"
    assert InterviewState(arc_signal=1.0).leading_template() == "arc"


def test_leading_template_tie_breaks_by_declaration_order():
    # All equal and positive: the first signal (iceberg) wins.
    s = InterviewState(
        iceberg_signal=0.5,
        two_buttons_signal=0.5,
        compass_signal=0.5,
        arc_signal=0.5,
    )
    assert s.leading_template() == "iceberg"


def test_leading_template_ignores_zero_and_negative():
    # Negative signals are below the 0.0 floor; treated as no evidence.
    s = InterviewState(iceberg_signal=-0.3, arc_signal=-0.1)
    assert s.leading_template() is None


def test_signal_weights_mapping():
    s = InterviewState(
        iceberg_signal=0.1,
        two_buttons_signal=0.2,
        compass_signal=0.3,
        arc_signal=0.4,
    )
    assert s.signal_weights() == {
        "iceberg": 0.1,
        "two_buttons": 0.2,
        "compass": 0.3,
        "arc": 0.4,
    }
