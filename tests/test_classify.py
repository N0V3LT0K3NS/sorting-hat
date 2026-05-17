"""Unit tests for the offline classification stage (``pipeline/classify.py``).

No test in this file makes a live LLM call. The OpenRouter client is always
mocked. The four fixture transcripts under ``tests/fixtures/`` stand in for
real interviews — one unmistakably each template.

Coverage:
* the XML-wrapping helper escapes special characters correctly;
* the response parser turns a well-formed structured response into a
  :class:`ClassificationResult`;
* confidence is clamped/validated into ``0.0 .. 1.0``;
* a missing API key degrades gracefully (clear typed error, no import crash);
* the template label is always one of the four locked values;
* :func:`classify` drives a mocked client end-to-end for each fixture.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pipeline.classify import (
    VALID_TEMPLATES,
    ClassificationError,
    ClassificationParseError,
    ClassificationResult,
    MissingAPIKeyError,
    classify,
    get_openrouter_client,
    load_classify_prompt,
    parse_classification_response,
    wrap_transcript_xml,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_FILES = {
    "iceberg": FIXTURES / "iceberg.xml",
    "two_buttons": FIXTURES / "two_buttons.xml",
    "compass": FIXTURES / "compass.xml",
    "arc": FIXTURES / "arc.xml",
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
    # Already imported at module top; re-exercise pure helpers to be sure.
    assert VALID_TEMPLATES == ("iceberg", "two_buttons", "compass", "arc")
    assert load_classify_prompt()  # prompt file loads fine without a key


# ---------------------------------------------------------------------------
# wrap_transcript_xml — escaping
# ---------------------------------------------------------------------------


def test_wrap_transcript_basic_structure():
    xml = wrap_transcript_xml(
        [("interviewer", "How are you?"), ("interviewee", "Fine.")]
    )
    assert xml.startswith("<transcript>")
    assert xml.endswith("</transcript>")
    assert "<interviewer>How are you?</interviewer>" in xml
    assert "<interviewee>Fine.</interviewee>" in xml


def test_wrap_transcript_escapes_special_characters():
    """Ampersands and angle brackets must be escaped, not passed through."""
    xml = wrap_transcript_xml(
        [("interviewee", "I love R&D <work> and 5 > 3 thinking.")]
    )
    assert "&amp;" in xml
    assert "&lt;work&gt;" in xml
    assert "&gt; 3" in xml
    # The raw, unescaped forms must NOT survive inside the text.
    assert "R&D" not in xml
    assert "<work>" not in xml


def test_wrap_transcript_escaped_xml_roundtrips():
    """The escaped XML must parse back to the original text verbatim."""
    import xml.etree.ElementTree as ET

    original = 'She said "this & that" <loudly>'
    xml = wrap_transcript_xml([("interviewee", original)])
    root = ET.fromstring(xml)
    assert root.find("interviewee").text == original


def test_wrap_transcript_normalizes_speaker_labels():
    xml = wrap_transcript_xml(
        [("user", "hello"), ("agent", "hi"), ("AI", "yes")]
    )
    # user -> interviewee; agent / AI -> interviewer.
    assert "<interviewee>hello</interviewee>" in xml
    assert xml.count("<interviewer>") == 2


def test_wrap_transcript_empty_is_valid_xml():
    xml = wrap_transcript_xml([])
    assert xml == "<transcript>\n</transcript>"


# ---------------------------------------------------------------------------
# parse_classification_response — well-formed responses
# ---------------------------------------------------------------------------


def test_parse_wellformed_response():
    result = parse_classification_response(
        json.dumps(
            {
                "template": "iceberg",
                "confidence": 0.87,
                "reasoning": "The person described a layered self with a hidden bottom.",
            }
        )
    )
    assert isinstance(result, ClassificationResult)
    assert result.template == "iceberg"
    assert result.confidence == 0.87
    assert "layered" in result.reasoning


def test_parse_tolerates_code_fence():
    fenced = (
        "```json\n"
        '{"template": "arc", "confidence": 0.7, "reasoning": "A clear before and after."}\n'
        "```"
    )
    result = parse_classification_response(fenced)
    assert result.template == "arc"


def test_parse_normalizes_template_case_and_whitespace():
    result = parse_classification_response(
        '{"template": "  Compass  ", "confidence": 0.6, "reasoning": "Position."}'
    )
    assert result.template == "compass"


def test_parse_rejects_non_json():
    with pytest.raises(ClassificationParseError):
        parse_classification_response("this is not json at all")


def test_parse_rejects_json_array():
    with pytest.raises(ClassificationParseError):
        parse_classification_response('["iceberg", 0.9]')


def test_parse_rejects_unknown_template():
    with pytest.raises(ClassificationParseError):
        parse_classification_response(
            '{"template": "spiral", "confidence": 0.9, "reasoning": "x"}'
        )


def test_parse_rejects_missing_field():
    with pytest.raises(ClassificationParseError):
        parse_classification_response('{"template": "arc", "confidence": 0.5}')


# ---------------------------------------------------------------------------
# ClassificationResult — template validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", VALID_TEMPLATES)
def test_result_accepts_each_valid_template(template):
    result = ClassificationResult(
        template=template, confidence=0.5, reasoning="ok"
    )
    assert result.template in VALID_TEMPLATES


def test_result_rejects_invalid_template():
    with pytest.raises(ValidationError):
        ClassificationResult(
            template="enneagram", confidence=0.5, reasoning="ok"
        )


def test_result_template_always_one_of_four_after_parse():
    """Whatever the model returns, a parsed result's label is locked."""
    for template in VALID_TEMPLATES:
        result = parse_classification_response(
            json.dumps(
                {"template": template, "confidence": 0.5, "reasoning": "x"}
            )
        )
        assert result.template in VALID_TEMPLATES


# ---------------------------------------------------------------------------
# ClassificationResult — confidence clamping
# ---------------------------------------------------------------------------


def test_confidence_above_one_is_clamped():
    result = ClassificationResult(
        template="arc", confidence=1.4, reasoning="ok"
    )
    assert result.confidence == 1.0


def test_confidence_below_zero_is_clamped():
    result = ClassificationResult(
        template="arc", confidence=-0.3, reasoning="ok"
    )
    assert result.confidence == 0.0


def test_confidence_in_range_is_untouched():
    result = ClassificationResult(
        template="arc", confidence=0.42, reasoning="ok"
    )
    assert result.confidence == 0.42


def test_confidence_boundaries_ok():
    assert (
        ClassificationResult(
            template="iceberg", confidence=0.0, reasoning="x"
        ).confidence
        == 0.0
    )
    assert (
        ClassificationResult(
            template="iceberg", confidence=1.0, reasoning="x"
        ).confidence
        == 1.0
    )


def test_confidence_clamped_through_parser():
    """A percentage-style confidence from the model is clamped, not rejected."""
    result = parse_classification_response(
        '{"template": "compass", "confidence": 85, "reasoning": "x"}'
    )
    assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Missing API key degrades gracefully
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_typed_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        get_openrouter_client()


def test_missing_api_key_is_a_classification_error(monkeypatch):
    """MissingAPIKeyError is catchable as the broader ClassificationError."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ClassificationError):
        get_openrouter_client()


def test_classify_without_key_raises_before_any_network(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        classify("<transcript></transcript>")


def test_explicit_api_key_builds_client(monkeypatch):
    """A key passed explicitly is honored even when the env var is unset."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = get_openrouter_client(api_key="sk-test-explicit")
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# classify — end to end with a mocked client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", list(FIXTURE_FILES))
def test_classify_drives_mocked_client_per_fixture(template):
    """Each fixture transcript runs through classify() with a mock client.

    The mock returns the matching label; we assert classify wires the call,
    parses the response, and produces a valid typed result.
    """
    transcript_xml = FIXTURE_FILES[template].read_text(encoding="utf-8")
    client = _fake_client(
        {
            "template": template,
            "confidence": 0.9,
            "reasoning": f"The transcript shows the {template} shape clearly.",
        }
    )
    result = classify(transcript_xml, client=client)
    assert isinstance(result, ClassificationResult)
    assert result.template == template
    assert result.template in VALID_TEMPLATES
    assert 0.0 <= result.confidence <= 1.0
    client.chat.completions.create.assert_called_once()


def test_classify_requests_structured_output():
    """The LLM call must ask for structured/JSON output via response_format."""
    client = _fake_client(
        {"template": "iceberg", "confidence": 0.8, "reasoning": "x"}
    )
    classify("<transcript></transcript>", client=client)
    _, kwargs = client.chat.completions.create.call_args
    assert "response_format" in kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["template"]["enum"] == list(VALID_TEMPLATES)


def test_classify_sends_transcript_and_prompt():
    """The system prompt and the transcript XML both reach the model."""
    client = _fake_client(
        {"template": "arc", "confidence": 0.7, "reasoning": "x"}
    )
    transcript = wrap_transcript_xml([("interviewee", "I used to be different.")])
    classify(transcript, client=client)
    _, kwargs = client.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "four" in messages[0]["content"].lower()
    assert transcript in messages[1]["content"]


def test_classify_wraps_network_failure_as_classification_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection reset")
    with pytest.raises(ClassificationError):
        classify("<transcript></transcript>", client=client)


def test_classify_raises_parse_error_on_garbage_response():
    client = _fake_client("not json")
    with pytest.raises(ClassificationParseError):
        classify("<transcript></transcript>", client=client)


def test_classify_raises_parse_error_on_empty_content():
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
    )
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    with pytest.raises(ClassificationParseError):
        classify("<transcript></transcript>", client=client)


def test_classify_clamps_out_of_range_model_confidence():
    """An out-of-range confidence from the model survives as a clamped value."""
    client = _fake_client(
        {"template": "compass", "confidence": 1.7, "reasoning": "x"}
    )
    result = classify("<transcript></transcript>", client=client)
    assert result.confidence == 1.0
