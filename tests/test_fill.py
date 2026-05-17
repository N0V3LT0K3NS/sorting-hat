"""Unit tests for the offline slot-filling stage (``pipeline/fill.py``).

No test in this file makes a live LLM call. The OpenRouter client is always
mocked. Slot-filling is stage 2 of the offline pipeline: given a template
label already chosen by stage 1, the transcript, and the live probe result,
:func:`fill` returns the matching typed result model from :mod:`agent.state`,
fully populated and re-validated locally against its char limits.

Coverage:
* :func:`fill` for each of the four templates returns the correct populated
  typed model, and the returned type matches the requested label;
* per-field character limits are enforced — an over-long model field is
  caught at local re-validation;
* the transcript is XML-wrapped before being sent to the model;
* a missing API key degrades gracefully (clear typed error, no import crash);
* an unknown template label raises a clear typed error.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.state import (
    ArcResult,
    CompassResult,
    IcebergResult,
    TwoButtonsResult,
)
from pipeline.fill import (
    TEMPLATE_MODELS,
    VALID_TEMPLATES,
    FillError,
    FillParseError,
    MissingAPIKeyError,
    UnknownTemplateError,
    fill,
    get_openrouter_client,
    load_fill_prompt,
    parse_fill_response,
    response_format_for,
    wrap_transcript_xml,
)

# ---------------------------------------------------------------------------
# Well-formed payloads — one per template, within every char limit
# ---------------------------------------------------------------------------

PAYLOADS: dict[str, dict] = {
    "iceberg": {
        "surface": "The reliable one who always says yes to covering a shift.",
        "first_layer": "Quietly keeps a tally of every favour and resents the imbalance.",
        "second_layer": "Believes being needed is the only thing keeping people around.",
        "abyss": "Suspects that if she stopped helping, nobody would actually call.",
    },
    "two_buttons": {
        "button_a_label": "Take the Denver job",
        "button_a_seduction": "A title, a raise, and finally proof to his father that the art degree was not a mistake.",
        "button_b_label": "Stay near his mother",
        "button_b_seduction": "Sunday dinners and being twenty minutes away while she is still well enough to enjoy them.",
        "impossibility": "The job starts in March; his mother's prognosis is measured in the same months he would spend unreachable on the other side of the mountains.",
    },
    "compass": {
        "axis_1_poles": ["improvise it", "plan it to death"],
        "axis_1_position": 0.6,
        "axis_2_poles": ["do it alone", "do it with the crew"],
        "axis_2_position": -0.4,
        "why_these_axes": "He kept describing himself by how he works: laminated checklists for a weekend hike, but a flat refusal to delegate any of it. Control over the plan, not over people.",
    },
    "arc": {
        "before": "She structured her whole twenties around making partner by thirty-five, and was three years ahead of schedule.",
        "catalyst": "A panic attack in the firm's parking garage that her body had clearly been drafting for months.",
        "middle": "She is still billing hours while quietly training a replacement nobody has been told about.",
        "after": "Toward a smaller practice she half-believes in and is still too scared to name out loud.",
    },
}


# ---------------------------------------------------------------------------
# Helpers — a fake OpenAI-compatible client
# ---------------------------------------------------------------------------


def _fake_client(payload: object) -> MagicMock:
    """Build a mock client whose chat completion returns ``payload`` as JSON.

    ``payload`` is serialized to JSON and placed in
    ``completion.choices[0].message.content`` — exactly where the real SDK
    puts the assistant's text.
    """
    content = payload if isinstance(payload, str) else json.dumps(payload)
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


# ---------------------------------------------------------------------------
# Module imports cleanly with no API key
# ---------------------------------------------------------------------------


def test_module_imports_without_api_key(monkeypatch):
    """Importing and using pure helpers must never require an API key."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert VALID_TEMPLATES == ("iceberg", "two_buttons", "compass", "arc")
    assert set(TEMPLATE_MODELS) == set(VALID_TEMPLATES)
    assert load_fill_prompt()  # prompt file loads fine without a key


def test_fill_prompt_mentions_meme_image_and_char_limits():
    """The prompt must tell the model the slots land on a meme image."""
    prompt = load_fill_prompt().lower()
    assert "meme" in prompt
    assert "char" in prompt  # character-limit guidance is present


# ---------------------------------------------------------------------------
# fill — each template returns its correct populated typed model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", list(PAYLOADS))
def test_fill_returns_correct_typed_model_per_template(template):
    """fill() for each template returns the matching populated typed model."""
    client = _fake_client(PAYLOADS[template])
    transcript = wrap_transcript_xml([("interviewee", "Some real detail.")])
    result = fill(template, transcript, probe_result=None, client=client)

    expected_type = TEMPLATE_MODELS[template]
    assert isinstance(result, expected_type)
    client.chat.completions.create.assert_called_once()


def test_fill_iceberg_populates_every_layer():
    client = _fake_client(PAYLOADS["iceberg"])
    result = fill("iceberg", "<transcript></transcript>", None, client=client)
    assert isinstance(result, IcebergResult)
    assert result.surface and result.first_layer
    assert result.second_layer and result.abyss
    assert "nobody would actually call" in result.abyss


def test_fill_two_buttons_populates_both_buttons():
    client = _fake_client(PAYLOADS["two_buttons"])
    result = fill("two_buttons", "<transcript></transcript>", None, client=client)
    assert isinstance(result, TwoButtonsResult)
    assert result.button_a_label and result.button_b_label
    assert result.button_a_seduction and result.button_b_seduction
    assert result.impossibility


def test_fill_compass_populates_axes_and_positions():
    client = _fake_client(PAYLOADS["compass"])
    result = fill("compass", "<transcript></transcript>", None, client=client)
    assert isinstance(result, CompassResult)
    assert len(result.axis_1_poles) == 2
    assert len(result.axis_2_poles) == 2
    assert -1.0 <= result.axis_1_position <= 1.0
    assert -1.0 <= result.axis_2_position <= 1.0


def test_fill_arc_populates_every_panel():
    client = _fake_client(PAYLOADS["arc"])
    result = fill("arc", "<transcript></transcript>", None, client=client)
    assert isinstance(result, ArcResult)
    assert result.before and result.catalyst
    assert result.middle and result.after


# ---------------------------------------------------------------------------
# fill — the returned model type matches the requested label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", list(PAYLOADS))
def test_returned_type_matches_requested_label(template):
    """Whatever label is requested, the returned model is exactly its type."""
    client = _fake_client(PAYLOADS[template])
    result = fill(template, "<transcript></transcript>", None, client=client)
    assert type(result) is TEMPLATE_MODELS[template]


def test_fill_normalizes_template_label_case_and_whitespace():
    """An untidy label still resolves to the right template model."""
    client = _fake_client(PAYLOADS["compass"])
    result = fill("  Compass  ", "<transcript></transcript>", None, client=client)
    assert isinstance(result, CompassResult)


def test_fill_rejects_unknown_template_label():
    client = _fake_client(PAYLOADS["iceberg"])
    with pytest.raises(UnknownTemplateError):
        fill("spiral", "<transcript></transcript>", None, client=client)


def test_unknown_template_is_a_fill_error():
    """UnknownTemplateError is catchable as the broader FillError."""
    client = _fake_client(PAYLOADS["iceberg"])
    with pytest.raises(FillError):
        fill("enneagram", "<transcript></transcript>", None, client=client)


# ---------------------------------------------------------------------------
# Char limits are enforced — an over-long field is caught locally
# ---------------------------------------------------------------------------


def test_overlong_iceberg_field_is_rejected():
    """A surface layer past its ~120-char limit fails local re-validation."""
    bad = dict(PAYLOADS["iceberg"])
    bad["surface"] = "x" * 200  # well over the 120-char cap
    client = _fake_client(bad)
    with pytest.raises(FillParseError):
        fill("iceberg", "<transcript></transcript>", None, client=client)


def test_overlong_two_buttons_label_is_rejected():
    """A button label past its 40-char limit fails local re-validation."""
    bad = dict(PAYLOADS["two_buttons"])
    bad["button_a_label"] = "a really very extremely long button label way over forty"
    client = _fake_client(bad)
    with pytest.raises(FillParseError):
        fill("two_buttons", "<transcript></transcript>", None, client=client)


def test_overlong_arc_panel_is_rejected():
    """An arc panel past its ~200-char limit fails local re-validation."""
    bad = dict(PAYLOADS["arc"])
    bad["catalyst"] = "y" * 300
    client = _fake_client(bad)
    with pytest.raises(FillParseError):
        fill("arc", "<transcript></transcript>", None, client=client)


def test_compass_position_out_of_range_is_rejected():
    """A compass position outside -1.0..1.0 fails local re-validation."""
    bad = dict(PAYLOADS["compass"])
    bad["axis_1_position"] = 2.5
    client = _fake_client(bad)
    with pytest.raises(FillParseError):
        fill("compass", "<transcript></transcript>", None, client=client)


def test_char_limit_caught_at_parse_layer_directly():
    """parse_fill_response itself enforces the char limits, not just fill()."""
    bad = dict(PAYLOADS["iceberg"])
    bad["abyss"] = "z" * 250
    with pytest.raises(FillParseError):
        parse_fill_response(json.dumps(bad), "iceberg")


def test_within_limit_payload_parses_cleanly():
    """A payload inside every limit parses without complaint."""
    for template, payload in PAYLOADS.items():
        result = parse_fill_response(json.dumps(payload), template)
        assert isinstance(result, TEMPLATE_MODELS[template])


# ---------------------------------------------------------------------------
# The transcript is XML-wrapped before being sent
# ---------------------------------------------------------------------------


def test_fill_wraps_bare_transcript_as_escaped_xml():
    """A raw, unwrapped transcript string is XML-wrapped before sending."""
    client = _fake_client(PAYLOADS["iceberg"])
    fill("iceberg", "I love R&D <work> & deep thinking.", None, client=client)
    _, kwargs = client.chat.completions.create.call_args
    user_content = kwargs["messages"][1]["content"]
    assert "<transcript>" in user_content
    assert "</transcript>" in user_content
    # The special characters are escaped, not passed through raw.
    assert "&amp;" in user_content
    assert "&lt;work&gt;" in user_content
    assert "R&D <work>" not in user_content


def test_fill_passes_through_already_wrapped_transcript():
    """An already-wrapped <transcript> element is sent as-is."""
    client = _fake_client(PAYLOADS["arc"])
    transcript = wrap_transcript_xml(
        [("interviewer", "What changed?"), ("interviewee", "Everything did.")]
    )
    fill("arc", transcript, None, client=client)
    _, kwargs = client.chat.completions.create.call_args
    user_content = kwargs["messages"][1]["content"]
    assert transcript in user_content


def test_fill_sends_prompt_label_and_probe_result():
    """The system prompt, the template label, and the probe result all reach the model."""
    client = _fake_client(PAYLOADS["two_buttons"])
    probe = TwoButtonsResult(**PAYLOADS["two_buttons"])
    fill("two_buttons", "<transcript></transcript>", probe, client=client)
    _, kwargs = client.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "meme" in messages[0]["content"].lower()
    assert "two_buttons" in messages[1]["content"]
    # The probe result is serialized into the user turn as a head start.
    assert "Take the Denver job" in messages[1]["content"]


def test_fill_requests_structured_output_per_template():
    """The call asks for structured JSON output matching the template schema."""
    for template in PAYLOADS:
        client = _fake_client(PAYLOADS[template])
        fill(template, "<transcript></transcript>", None, client=client)
        _, kwargs = client.chat.completions.create.call_args
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"] == response_format_for(template)


def test_fill_tolerates_none_probe_result():
    """A None probe result is valid — it is a head start, not required."""
    client = _fake_client(PAYLOADS["iceberg"])
    result = fill("iceberg", "<transcript></transcript>", probe_result=None, client=client)
    assert isinstance(result, IcebergResult)
    _, kwargs = client.chat.completions.create.call_args
    assert "null" in kwargs["messages"][1]["content"]


# ---------------------------------------------------------------------------
# Missing API key degrades gracefully
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_typed_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        get_openrouter_client()


def test_fill_without_key_raises_before_any_network(monkeypatch):
    """fill() with no client and no key raises a clear typed error, not an SDK crash."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        fill("iceberg", "<transcript></transcript>", None)


def test_fill_with_explicit_key_builds_client(monkeypatch):
    """A key passed explicitly is honored even when the env var is unset."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = get_openrouter_client(api_key="sk-test-explicit")
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# fill — failure handling with a mocked client
# ---------------------------------------------------------------------------


def test_fill_wraps_network_failure_as_fill_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection reset")
    with pytest.raises(FillError):
        fill("iceberg", "<transcript></transcript>", None, client=client)


def test_fill_raises_parse_error_on_garbage_response():
    client = _fake_client("this is not json at all")
    with pytest.raises(FillParseError):
        fill("arc", "<transcript></transcript>", None, client=client)


def test_fill_raises_parse_error_on_empty_content():
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
    )
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    with pytest.raises(FillParseError):
        fill("compass", "<transcript></transcript>", None, client=client)


def test_fill_raises_parse_error_on_json_array():
    client = _fake_client(["not", "an", "object"])
    with pytest.raises(FillParseError):
        fill("iceberg", "<transcript></transcript>", None, client=client)


def test_fill_tolerates_code_fenced_response():
    """A fenced ```json block is unwrapped before parsing."""
    fenced = "```json\n" + json.dumps(PAYLOADS["arc"]) + "\n```"
    client = _fake_client(fenced)
    result = fill("arc", "<transcript></transcript>", None, client=client)
    assert isinstance(result, ArcResult)
