"""Stage 2 of the offline analysis pipeline: structured slot-filling.

Stage 1 (:mod:`pipeline.classify`) decides *which* of the four locked meme
templates fits a person. This module does the next, separate job: given that
template label, the interview transcript, and the live probe result, it
**fills the chosen template's typed slots** with vivid, transcript-grounded
meme copy.

Classification and filling are two cognitively distinct LLM jobs and are
never merged — this module takes the label as fixed and does not re-classify.

The four templates and their typed slot models live in :mod:`agent.state`:

* ``iceberg``     -> :class:`agent.state.IcebergResult`     (4 layers)
* ``two_buttons`` -> :class:`agent.state.TwoButtonsResult`  (2 buttons + why)
* ``compass``     -> :class:`agent.state.CompassResult`     (2 axes + why)
* ``arc``         -> :class:`agent.state.ArcResult`         (4 panels)

This module is **importable and standalone**. It has no LiveKit dependency.
The LLM call routes through OpenRouter via the ``openai`` SDK — reusing the
client builder and the transcript XML helper from :mod:`pipeline.classify`
rather than duplicating them. A missing ``OPENROUTER_API_KEY`` degrades
gracefully: importing this module never fails, and :func:`fill` raises a
clear, typed :class:`MissingAPIKeyError` only when an actual call is needed.

The model's response is structured JSON, but it is always **re-validated
locally** against the matching pydantic model from :mod:`agent.state`, so the
per-field character limits are enforced no matter what the provider returns.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from agent.state import (
    ArcResult,
    CompassResult,
    IcebergResult,
    TwoButtonsResult,
)

# Reuse — never duplicate — classify.py's OpenRouter client and XML helpers.
from pipeline.classify import (
    API_KEY_ENV,
    OPENROUTER_BASE_URL,
    VALID_TEMPLATES,
    MissingAPIKeyError,
    get_openrouter_client,
    wrap_transcript_xml,
)

__all__ = [
    "API_KEY_ENV",
    "OPENROUTER_BASE_URL",
    "VALID_TEMPLATES",
    "TEMPLATE_MODELS",
    "DEFAULT_MODEL",
    "FillError",
    "MissingAPIKeyError",
    "FillParseError",
    "UnknownTemplateError",
    "load_fill_prompt",
    "get_openrouter_client",
    "wrap_transcript_xml",
    "response_format_for",
    "parse_fill_response",
    "fill",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Map each locked template label onto its typed result model in
#: :mod:`agent.state`. :func:`fill` returns an instance of the matching one.
TEMPLATE_MODELS: dict[str, type[BaseModel]] = {
    "iceberg": IcebergResult,
    "two_buttons": TwoButtonsResult,
    "compass": CompassResult,
    "arc": ArcResult,
}

#: Default slot-filling model. Filling is run once per interview, off the
#: latency-critical path, so a capable model is worth it for vivid copy.
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

#: The slot-filling prompt lives next to the other analysis prompts.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "fill.md"


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class FillError(RuntimeError):
    """Base class for any failure in the slot-filling stage."""


class FillParseError(FillError):
    """Raised when the LLM response cannot be parsed into the typed model.

    This also covers a response that parses as JSON but fails local
    re-validation — for example a slot that exceeds its character limit.
    """


class UnknownTemplateError(FillError):
    """Raised when :func:`fill` is asked for a template label it doesn't know.

    The label must be exactly one of the four locked templates.
    """


# ---------------------------------------------------------------------------
# Template-label normalisation
# ---------------------------------------------------------------------------


def _normalize_template(template_label: str) -> str:
    """Return the canonical lowercase label, or raise :class:`UnknownTemplateError`."""
    if not isinstance(template_label, str):
        raise UnknownTemplateError(
            f"template label must be a string, got {type(template_label).__name__}"
        )
    normalized = template_label.strip().lower()
    if normalized not in TEMPLATE_MODELS:
        raise UnknownTemplateError(
            f"template must be one of {tuple(TEMPLATE_MODELS)}, "
            f"got {template_label!r}"
        )
    return normalized


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def load_fill_prompt() -> str:
    """Return the slot-filling system prompt from ``prompts/fill.md``."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Response-format schemas for structured output
# ---------------------------------------------------------------------------


def _schema_for_model(name: str, model: type[BaseModel]) -> dict:
    """Build an OpenAI-style ``json_schema`` response_format for a model.

    The schema is derived from the pydantic model itself, so the slot names
    and char limits stay in lockstep with :mod:`agent.state`. The result is
    still re-validated locally against the model — a provider that ignores
    the schema is handled correctly regardless.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": model.model_json_schema(),
        },
    }


#: Per-template ``response_format`` payloads requesting structured JSON.
RESPONSE_FORMATS: dict[str, dict] = {
    label: _schema_for_model(f"{label}_result", model)
    for label, model in TEMPLATE_MODELS.items()
}


def response_format_for(template_label: str) -> dict:
    """Return the structured-output ``response_format`` for one template."""
    return RESPONSE_FORMATS[_normalize_template(template_label)]


# ---------------------------------------------------------------------------
# Response parsing + local re-validation
# ---------------------------------------------------------------------------


def parse_fill_response(content: str, template_label: str) -> BaseModel:
    """Parse a model's JSON response into the matching typed result model.

    ``content`` is the raw assistant message text — expected to be a JSON
    object whose keys are the chosen template's slot fields. Surrounding
    whitespace or ```` ```json ```` fences are tolerated.

    The parsed object is **re-validated locally** against the pydantic model
    from :mod:`agent.state`, so per-field character limits (and the compass
    position range) are enforced here regardless of what the model returned.

    Raises :class:`FillParseError` if the text is not a JSON object or does
    not validate into the template's model (e.g. an over-long slot).
    Raises :class:`UnknownTemplateError` for an unknown template label.
    """
    label = _normalize_template(template_label)
    model = TEMPLATE_MODELS[label]

    text = _strip_code_fence(content.strip())
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FillParseError(
            f"fill response was not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise FillParseError(
            f"fill response must be a JSON object, got {type(payload).__name__}"
        )
    try:
        return model.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError and friends
        raise FillParseError(
            f"fill response did not match the {label} schema "
            f"(char limits enforced locally): {exc}"
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
# Probe-result normalisation
# ---------------------------------------------------------------------------


def _probe_result_to_json(probe_result: object) -> str:
    """Render the live probe result as a JSON string for the prompt.

    Accepts a pydantic model, a plain dict, ``None``, or a pre-serialised
    JSON string. The probe result is a *head start* for the fill, not gospel
    — passing ``None`` is valid and yields an explicit "no probe result".
    """
    if probe_result is None:
        return "null"
    if isinstance(probe_result, str):
        return probe_result
    if isinstance(probe_result, BaseModel):
        return probe_result.model_dump_json(indent=2)
    try:
        return json.dumps(probe_result, indent=2, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(probe_result))


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def fill(
    template_label: str,
    transcript: str,
    probe_result: object = None,
    *,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
) -> BaseModel:
    """Fill one template's typed slots from an interview transcript.

    ``template_label`` is the label chosen by stage 1 — one of ``iceberg``,
    ``two_buttons``, ``compass``, ``arc``. It is taken as fixed; this stage
    does not re-classify.

    ``transcript`` is the interview transcript. If it does not already look
    like a wrapped ``<transcript>`` element it is wrapped as escaped XML via
    :func:`wrap_transcript_xml` before being sent — quote fidelity matters
    for the meme copy (see ``docs/borrowed-craft.md`` §6).

    ``probe_result`` is the typed result from the live interview's probe for
    this template (a pydantic model, dict, JSON string, or ``None``). It is
    passed to the model as a head start.

    Returns the populated typed model from :mod:`agent.state` matching the
    template label — :class:`~agent.state.IcebergResult`,
    :class:`~agent.state.TwoButtonsResult`,
    :class:`~agent.state.CompassResult`, or
    :class:`~agent.state.ArcResult`. The result is re-validated locally, so
    every per-field character limit is enforced.

    Pass ``client`` to inject a pre-built (or mock) OpenAI-compatible client;
    otherwise one is built from ``api_key`` / ``OPENROUTER_API_KEY``.

    Raises:
        UnknownTemplateError: ``template_label`` is not one of the four.
        MissingAPIKeyError: no client given and no API key available.
        FillParseError: the model response could not be parsed or failed
            local re-validation (e.g. an over-long slot).
        FillError: any other slot-filling-stage failure.
    """
    label = _normalize_template(template_label)
    transcript_xml = _ensure_transcript_xml(transcript)

    active_client = client if client is not None else get_openrouter_client(api_key)
    system_prompt = load_fill_prompt()
    probe_json = _probe_result_to_json(probe_result)

    user_content = (
        f"Template label (fixed, do not re-classify): {label}\n\n"
        "Fill this template's slots. Return only the JSON object described "
        "in the system prompt for this template.\n\n"
        "=== INTERVIEW TRANSCRIPT ===\n"
        f"{transcript_xml}\n\n"
        "=== LIVE PROBE RESULT (a head start; sharpen against the "
        "transcript) ===\n"
        f"{probe_json}"
    )

    try:
        completion = active_client.chat.completions.create(  # type: ignore[attr-defined]
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=response_format_for(label),
            temperature=0.4,
        )
    except FillError:
        raise
    except Exception as exc:  # network / API / SDK failure
        raise FillError(f"the slot-filling LLM call failed: {exc}") from exc

    content = _extract_message_content(completion)
    return parse_fill_response(content, label)


def _ensure_transcript_xml(transcript: str) -> str:
    """Return the transcript as escaped ``<transcript>`` XML.

    If ``transcript`` already looks like a wrapped ``<transcript>`` element
    it is used as-is (the caller wrapped it with :func:`wrap_transcript_xml`).
    Otherwise a bare string is wrapped as a single escaped interviewee turn,
    so a raw transcript is never sent unescaped.
    """
    if not isinstance(transcript, str):
        raise FillError(
            f"transcript must be a string, got {type(transcript).__name__}"
        )
    if transcript.lstrip().startswith("<transcript>"):
        return transcript
    return wrap_transcript_xml([("interviewee", transcript)])


def _extract_message_content(completion: object) -> str:
    """Pull the assistant message text out of a chat-completion response."""
    try:
        choices = completion.choices  # type: ignore[attr-defined]
        message = choices[0].message
        content = message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise FillParseError(
            f"fill response had no usable message content: {exc}"
        ) from exc
    if not content or not str(content).strip():
        raise FillParseError("fill response message content was empty")
    return str(content)
