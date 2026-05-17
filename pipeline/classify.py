"""Stage 1 of the offline analysis pipeline: the authoritative sort.

Given a finished interview transcript, decide which of the four locked,
orthogonal meme templates fits the *shape* of the person:

* ``iceberg``     — depth      (vertical layering, a hidden bottom)
* ``two_buttons`` — tension    (an unresolved pull between two options)
* ``compass``     — position   (coordinates on axes, settled)
* ``arc``         — trajectory (a before-and-after transformation)

This module is **importable and standalone**. It has no LiveKit dependency
and does no live-interview work. It is a separate cognitive job from
slot-filling (``pipeline/fill.py``, a later goal) — the two are never merged.

The LLM call routes through OpenRouter using the ``openai`` SDK pointed at
``https://openrouter.ai/api/v1``. A missing ``OPENROUTER_API_KEY`` degrades
gracefully: importing this module never fails, and :func:`classify` raises a
clear, typed :class:`MissingAPIKeyError` only when an actual call is needed.

The transcript is handed to the model as **escaped XML**, not JSON — this
preserves verbatim interviewee quotes unambiguously (see
``docs/borrowed-craft.md`` §6).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Sequence
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The four locked template labels. Classification picks exactly one.
VALID_TEMPLATES: tuple[str, ...] = ("iceberg", "two_buttons", "compass", "arc")

#: OpenRouter's OpenAI-compatible endpoint.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Environment variable holding the OpenRouter API key.
API_KEY_ENV = "OPENROUTER_API_KEY"

#: Default classification model — a capable model is worth it for the sort,
#: which is run once per interview and off the latency-critical path.
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

#: The classification prompt lives next to the other analysis prompts.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "classify.md"


# ---------------------------------------------------------------------------
# Typed errors and result
# ---------------------------------------------------------------------------


class ClassificationError(RuntimeError):
    """Base class for any failure in the classification stage."""


class MissingAPIKeyError(ClassificationError):
    """Raised when a classification is attempted with no OpenRouter API key.

    This is the graceful-degradation signal: importing the module and
    constructing inputs never fails, but a call without a key raises this
    clear, catchable error rather than crashing deep inside the SDK.
    """


class ClassificationParseError(ClassificationError):
    """Raised when the LLM response cannot be parsed into a valid result."""


class ClassificationResult(BaseModel):
    """The authoritative output of the sort.

    One template label, a confidence score, and a short rationale. The
    template is constrained to the four locked values; confidence is clamped
    into ``0.0 .. 1.0`` so a slightly out-of-range model answer is tolerated
    rather than rejected.
    """

    template: str = Field(
        ...,
        description="One of: iceberg, two_buttons, compass, arc.",
    )
    confidence: float = Field(
        ...,
        description="How clearly one shape dominated, clamped to 0.0 .. 1.0.",
    )
    reasoning: str = Field(
        ...,
        description="2-3 sentences naming the shape and the evidence for it.",
    )

    @field_validator("template")
    @classmethod
    def _template_is_valid(cls, value: str) -> str:
        """The label must be exactly one of the four locked templates."""
        normalized = value.strip().lower()
        if normalized not in VALID_TEMPLATES:
            raise ValueError(
                f"template must be one of {VALID_TEMPLATES}, got {value!r}"
            )
        return normalized

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        """Clamp confidence into ``0.0 .. 1.0``.

        Models occasionally return a value just outside the range (e.g. a
        percentage, or ``1.0000001``). The sort is more useful clamped than
        rejected, so we coerce rather than raise.
        """
        number = float(value)  # type: ignore[arg-type]
        return max(0.0, min(1.0, number))


# ---------------------------------------------------------------------------
# Transcript -> escaped XML
# ---------------------------------------------------------------------------


def wrap_transcript_xml(turns: Sequence[tuple[str, str]]) -> str:
    """Wrap an interview transcript as escaped XML for the analysis prompt.

    ``turns`` is an ordered sequence of ``(speaker, text)`` pairs. ``speaker``
    is matched case-insensitively: anything starting with ``i`` and not
    ``interviewe`` is treated as the interviewer; an interviewee otherwise.
    In practice pass the literal strings ``"interviewer"`` / ``"interviewee"``.

    Every piece of text is XML-escaped (``&``, ``<``, ``>``) so verbatim
    quotes — quotation marks, ampersands, angle brackets — survive intact.
    Returns a single ``<transcript>`` element.

    Passing transcripts as escaped XML rather than JSON is a deliberate
    choice (``docs/borrowed-craft.md`` §6): it preserves quote fidelity,
    which the downstream meme copy depends on.
    """
    lines: list[str] = ["<transcript>"]
    for speaker, text in turns:
        tag = _normalize_speaker(speaker)
        lines.append(f"  <{tag}>{escape(text)}</{tag}>")
    lines.append("</transcript>")
    return "\n".join(lines)


def _normalize_speaker(speaker: str) -> str:
    """Map a free-form speaker label onto ``interviewer`` / ``interviewee``."""
    s = speaker.strip().lower()
    if s in ("interviewee", "user", "respondent", "subject"):
        return "interviewee"
    if s in ("interviewer", "agent", "assistant", "ai"):
        return "interviewer"
    # Fall back on a sensible default rather than emitting an unknown tag.
    return "interviewee" if s.startswith("interviewe") else "interviewer"


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def load_classify_prompt() -> str:
    """Return the classification system prompt from ``prompts/classify.md``."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------


def get_openrouter_client(api_key: Optional[str] = None):
    """Build an ``openai`` SDK client pointed at OpenRouter.

    The key is taken from the ``api_key`` argument or, failing that, the
    ``OPENROUTER_API_KEY`` environment variable. If neither is present this
    raises :class:`MissingAPIKeyError` — a clear, typed signal — instead of
    letting the SDK fail obscurely later.
    """
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise MissingAPIKeyError(
            f"{API_KEY_ENV} is not set. Classification needs an OpenRouter "
            "API key. Set it in the environment or pass api_key=... ."
        )
    # Imported lazily so that importing this module never requires the SDK
    # to be importable, and never touches the network.
    from openai import OpenAI

    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key)


# ---------------------------------------------------------------------------
# Response-format schema for structured output
# ---------------------------------------------------------------------------

#: JSON-schema ``response_format`` requesting structured output from the
#: OpenAI-compatible endpoint. OpenRouter passes this through to providers
#: that support structured outputs; the result is still validated locally
#: against :class:`ClassificationResult`, so a provider that ignores the
#: schema and merely returns JSON is still handled correctly.
RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "classification_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "enum": list(VALID_TEMPLATES),
                    "description": "The single dominant template shape.",
                },
                "confidence": {
                    "type": "number",
                    "description": "How clearly one shape dominated, 0.0-1.0.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "2-3 sentences of shape-based rationale.",
                },
            },
            "required": ["template", "confidence", "reasoning"],
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_classification_response(content: str) -> ClassificationResult:
    """Parse a model's structured JSON response into a typed result.

    ``content`` is the raw assistant message text — expected to be a JSON
    object with ``template`` / ``confidence`` / ``reasoning`` keys. Any
    surrounding whitespace or ```` ```json ```` fences are tolerated.

    Raises :class:`ClassificationParseError` if the text is not JSON, is not
    an object, or does not validate into :class:`ClassificationResult`
    (e.g. an unknown template label).
    """
    text = _strip_code_fence(content.strip())
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClassificationParseError(
            f"classification response was not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ClassificationParseError(
            f"classification response must be a JSON object, got {type(payload).__name__}"
        )
    try:
        return ClassificationResult.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ClassificationParseError(
            f"classification response did not match the expected schema: {exc}"
        ) from exc


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```` ``` ```` / ```` ```json ```` fence if present."""
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def classify(
    transcript_xml: str,
    *,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
) -> ClassificationResult:
    """Classify an interview into one of the four locked templates.

    ``transcript_xml`` is the interview transcript already wrapped as escaped
    XML — use :func:`wrap_transcript_xml` to produce it from ``(speaker,
    text)`` turns.

    The call routes through OpenRouter via the ``openai`` SDK and requests
    structured JSON output. The response is validated locally into a typed
    :class:`ClassificationResult` regardless of whether the provider honored
    the JSON-schema request.

    Pass ``client`` to inject a pre-built (or mock) OpenAI-compatible client;
    otherwise one is built from ``api_key`` / ``OPENROUTER_API_KEY``. A
    missing key raises :class:`MissingAPIKeyError`.

    Raises:
        MissingAPIKeyError: no client given and no API key available.
        ClassificationParseError: the model response could not be parsed.
        ClassificationError: any other classification-stage failure.
    """
    active_client = client if client is not None else get_openrouter_client(api_key)
    system_prompt = load_classify_prompt()

    try:
        completion = active_client.chat.completions.create(  # type: ignore[attr-defined]
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Classify this interview. Return only the JSON "
                        "object described in the system prompt.\n\n"
                        f"{transcript_xml}"
                    ),
                },
            ],
            response_format=RESPONSE_FORMAT,
            temperature=0.0,
        )
    except ClassificationError:
        raise
    except Exception as exc:  # network / API / SDK failure
        raise ClassificationError(
            f"the classification LLM call failed: {exc}"
        ) from exc

    content = _extract_message_content(completion)
    return parse_classification_response(content)


def _extract_message_content(completion: object) -> str:
    """Pull the assistant message text out of a chat-completion response."""
    try:
        choices = completion.choices  # type: ignore[attr-defined]
        message = choices[0].message
        content = message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ClassificationParseError(
            f"classification response had no usable message content: {exc}"
        ) from exc
    if not content or not str(content).strip():
        raise ClassificationParseError(
            "classification response message content was empty"
        )
    return str(content)
