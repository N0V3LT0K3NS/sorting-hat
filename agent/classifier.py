"""sorting-hat — G14: the background signal classifier (observer pattern).

This is the brief's step 6 — the elegant part, built last because the
interview already works without it. The supervisor in
``agent/interviewer.py`` reads the four signal weights on
:class:`~agent.state.InterviewState` to decide which probe to run, but
nothing *updates* those weights mid-interview. This module is what does.

After each user turn the interviewer fires :func:`classify_turn` as a
**background task** (``asyncio.create_task``) — never awaited on the
critical path. A fast, small model via OpenRouter silently scores how
strongly that one response carries each of the four template signals
(iceberg / two_buttons / compass / arc) as floats ``0.0 .. 1.0``. Those
scores are nudged into the InterviewState signal weights so the
supervisor's later :meth:`InterviewState.leading_template` read reflects
accumulated evidence.

This is LiveKit's documented observer pattern: a parallel task off the
critical path. It is built to *never* stall or crash the interview:

* a hard timeout (:data:`CLASSIFIER_TIMEOUT_S`) guards the LLM call — a
  slow provider cannot delay the next agent turn;
* on timeout, any exception, or a missing API key, it returns
  :data:`NOOP_SCORES` (all zero) and logs — it never raises into the
  interview;
* it is a pure scoring function plus an apply helper; firing it as a
  background task is the interviewer's job, not this module's.

The LLM call routes through OpenRouter via the ``openai`` SDK pointed at
``https://openrouter.ai/api/v1`` — the same gateway the rest of the
project uses. Per Decision 0001 the model is a fast, cheap open model
(``openai/gpt-oss-120b``) pinned to Groq for its LPU latency: this is
off the critical path but has a ~1s budget, and the Groq pin is the
difference between hitting and blowing it. The scoring prompt lives in
``prompts/bg_classifier.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sorting-hat.classifier")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The four signal names the classifier scores — the keys of the dict it
#: returns. These match the short template labels used across the project
#: (see :data:`agent.state.TEMPLATE_SIGNALS`).
SIGNAL_NAMES: tuple[str, ...] = ("iceberg", "two_buttons", "compass", "arc")

#: Hard timeout for the background LLM call, in seconds. The classifier is
#: off the critical path, but it must never *stall* the interview: if the
#: model has not answered within this window the call is abandoned and
#: no-op scores are returned. ~1s keeps the background task short-lived.
CLASSIFIER_TIMEOUT_S: float = 1.0

#: The no-op result: every signal at 0.0. Returned on timeout, on any
#: error, and when no API key is configured. Adding these into the
#: InterviewState signal weights changes nothing — a safe degraded mode.
NOOP_SCORES: dict[str, float] = {name: 0.0 for name in SIGNAL_NAMES}

#: OpenRouter's OpenAI-compatible endpoint.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Environment variable holding the OpenRouter API key.
API_KEY_ENV = "OPENROUTER_API_KEY"

#: Default background-classifier model (Job 2, Decision 0001) — a fast, cheap
#: open model routed through OpenRouter. Overridable via ``BG_CLASSIFIER_MODEL``
#: so a deployment or a live smoke test can swap it without a code change.
#: This is deliberately a smaller/faster model than the interviewer or the
#: offline classifier: the job is a quick four-number score, run on every
#: turn, off the critical path.
DEFAULT_MODEL = "openai/gpt-oss-120b"

#: Fallback background-classifier model — used for graceful degradation if the
#: primary is unavailable. Overridable via ``BG_CLASSIFIER_FALLBACK_MODEL``.
FALLBACK_MODEL = "google/gemini-3.1-flash-lite"

#: OpenRouter provider pin for Job 2. Decision 0001 pins the primary to Groq —
#: its LPU gives the ~0.6-0.9s TTFT that fits the ~1s background budget
#: (~10x backend throughput spread on open models). ``allow_fallbacks: True``
#: keeps an unpinned fallback for graceful degradation.
GROQ_PROVIDER_PIN: dict[str, object] = {
    "only": ["Groq"],
    "allow_fallbacks": True,
}

#: The scoring prompt lives beside the other prompts: classifier.py ->
#: agent -> repo root -> prompts/.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "bg_classifier.md"


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def load_classifier_prompt() -> str:
    """Return the background-classifier scoring prompt.

    The prompt lives in ``prompts/bg_classifier.md``. A missing file falls
    back to a compact inline prompt rather than raising — this classifier
    must degrade, never crash the interview, even if the prompt file is
    absent.
    """
    if _PROMPT_PATH.is_file():
        text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
        logger.warning(
            "background-classifier prompt at %s is empty — using inline fallback",
            _PROMPT_PATH,
        )
    else:
        logger.warning(
            "background-classifier prompt not found at %s — using inline fallback",
            _PROMPT_PATH,
        )
    return _INLINE_PROMPT


#: Compact fallback prompt, used only if ``prompts/bg_classifier.md`` is
#: missing or empty. Keeps the classifier functional in a degraded repo.
_INLINE_PROMPT = (
    "You are a silent background classifier in a voice interview. Read the "
    "single interviewee response and score how strongly it carries each of "
    "four orthogonal signals, each a float 0.0-1.0:\n"
    "- iceberg: depth — hidden layers, a public surface vs a private bottom.\n"
    "- two_buttons: tension — an unresolved pull between two seductive options.\n"
    "- compass: position — a settled sense of where the person stands on axes.\n"
    "- arc: trajectory — a before-and-after, a change over time.\n"
    "Score each signal independently; a flat answer scores low across the "
    "board. Return ONLY a JSON object: "
    '{"iceberg": 0.0, "two_buttons": 0.0, "compass": 0.0, "arc": 0.0}'
)


# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------


def get_openrouter_client(api_key: Optional[str] = None):
    """Build an ``openai`` SDK client pointed at OpenRouter, or ``None``.

    The key is taken from the ``api_key`` argument or the
    ``OPENROUTER_API_KEY`` environment variable. Unlike the offline
    pipeline, a missing key here is **not** an error: this is a background
    classifier that must degrade silently. With no key this returns
    ``None`` and :func:`classify_turn` falls back to :data:`NOOP_SCORES`.
    """
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        return None
    # Imported lazily so importing this module never requires the SDK and
    # never touches the network.
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=key)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_scores(content: str) -> dict[str, float]:
    """Parse a model response into four clamped ``0.0 .. 1.0`` signal scores.

    ``content`` is the raw assistant message text — expected to be a JSON
    object with ``iceberg`` / ``two_buttons`` / ``compass`` / ``arc``
    keys. A surrounding ```` ```json ```` fence is tolerated.

    This is forgiving by design: a missing or non-numeric key becomes
    ``0.0``, and every value is clamped into ``0.0 .. 1.0``. The result
    always has exactly the four :data:`SIGNAL_NAMES` keys. A response that
    is not JSON at all raises ``ValueError`` — the caller treats that as
    an error and falls back to no-op scores.
    """
    text = _strip_code_fence(content.strip())
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(
            f"classifier response must be a JSON object, got {type(payload).__name__}"
        )
    scores: dict[str, float] = {}
    for name in SIGNAL_NAMES:
        scores[name] = _clamp_score(payload.get(name))
    return scores


def _clamp_score(value: object) -> float:
    """Coerce one raw score into a clamped ``0.0 .. 1.0`` float.

    A missing key, ``None``, or a non-numeric value becomes ``0.0`` — the
    classifier never rejects a response over one bad field.
    """
    if value is None:
        return 0.0
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


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
# The scoring call
# ---------------------------------------------------------------------------


async def _score_response(
    client: object,
    user_response: str,
    *,
    model: str,
    fallback_model: str,
) -> dict[str, float]:
    """Run one OpenRouter scoring call and return the four parsed scores.

    This is the inner coroutine :func:`classify_turn` wraps in a timeout.
    It assumes ``client`` is a valid async OpenAI-compatible client. Any
    failure propagates to :func:`classify_turn`, which catches it.

    Per Decision 0001, Job 2 pins the primary model to Groq (the latency
    win that keeps it inside the ~1s budget) and names ``fallback_model``
    for graceful degradation — both passed via OpenRouter's ``extra_body``.
    """
    system_prompt = load_classifier_prompt()
    completion = await client.chat.completions.create(  # type: ignore[attr-defined]
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Score this single interviewee response. Return only the "
                    "JSON object described above.\n\n"
                    f"<response>{user_response}</response>"
                ),
            },
        ],
        temperature=0.0,
        extra_body={
            # Pin the primary to Groq; fall through to the fallback model
            # (unpinned) if the primary route is unavailable.
            "provider": GROQ_PROVIDER_PIN,
            "models": [model, fallback_model],
        },
    )
    content = _extract_message_content(completion)
    return parse_scores(content)


def _extract_message_content(completion: object) -> str:
    """Pull the assistant message text out of a chat-completion response."""
    choices = completion.choices  # type: ignore[attr-defined]
    content = choices[0].message.content
    if not content or not str(content).strip():
        raise ValueError("classifier response message content was empty")
    return str(content)


async def classify_turn(
    user_response: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
    timeout: float = CLASSIFIER_TIMEOUT_S,
) -> dict[str, float]:
    """Score one user response for the four template signals.

    This is the public entry point. Given the text of a **single**
    interviewee turn it returns a dict with exactly the four
    :data:`SIGNAL_NAMES` keys, each a float in ``0.0 .. 1.0``.

    It is built to be safe to fire as a background task: it **never
    raises**. On a timeout, any exception, an empty response, or a
    missing API key it logs and returns :data:`NOOP_SCORES` (all zero) —
    adding which into the signal weights changes nothing.

    Args:
        user_response: The text the interviewee just said. Empty or
            whitespace-only input short-circuits to no-op scores.
        model: OpenRouter model slug. Defaults to ``BG_CLASSIFIER_MODEL``
            from the environment, then :data:`DEFAULT_MODEL`. The fallback
            is ``BG_CLASSIFIER_FALLBACK_MODEL`` then :data:`FALLBACK_MODEL`.
        api_key: OpenRouter key. Defaults to ``OPENROUTER_API_KEY``.
        client: A pre-built async OpenAI-compatible client to use instead
            of constructing one — the injection point for tests.
        timeout: Hard timeout in seconds for the LLM call. Defaults to
            :data:`CLASSIFIER_TIMEOUT_S`.

    Returns:
        A fresh dict of the four signal scores. A copy of
        :data:`NOOP_SCORES` on any degraded path.
    """
    if not user_response or not user_response.strip():
        # Nothing to score — not an error, just an empty turn.
        return dict(NOOP_SCORES)

    active_client = client if client is not None else get_openrouter_client(api_key)
    if active_client is None:
        # No API key configured — degrade silently. This is expected on a
        # dev box with no .env; it must never block the interview.
        logger.info(
            "background classifier disabled (no %s) — returning no-op scores",
            API_KEY_ENV,
        )
        return dict(NOOP_SCORES)

    chosen_model = model or os.environ.get("BG_CLASSIFIER_MODEL") or DEFAULT_MODEL
    fallback_model = (
        os.environ.get("BG_CLASSIFIER_FALLBACK_MODEL") or FALLBACK_MODEL
    )

    try:
        scores = await asyncio.wait_for(
            _score_response(
                active_client,
                user_response,
                model=chosen_model,
                fallback_model=fallback_model,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        # The classifier is off the critical path; a slow provider must not
        # stall the interview. Abandon the call and return no-op scores.
        logger.warning(
            "background classifier timed out after %.1fs — returning no-op scores",
            timeout,
        )
        return dict(NOOP_SCORES)
    except asyncio.CancelledError:
        # The session is shutting the task down — re-raise so cancellation
        # propagates cleanly; do not swallow it as an error.
        raise
    except Exception:  # noqa: BLE001 — any SDK/network/parse failure
        # ANY failure degrades to no-op. The background classifier must
        # never raise into the interview.
        logger.warning(
            "background classifier failed — returning no-op scores", exc_info=True
        )
        return dict(NOOP_SCORES)

    logger.info("background classifier scored a turn: %s", scores)
    return scores


# ---------------------------------------------------------------------------
# Applying scores to InterviewState
# ---------------------------------------------------------------------------


def apply_scores(state: object, scores: dict[str, float]) -> None:
    """Add a turn's signal scores into an :class:`InterviewState`.

    The four scores are *accumulated* onto the matching ``*_signal``
    fields of ``state`` (``iceberg_signal``, ``two_buttons_signal``,
    ``compass_signal``, ``arc_signal``) so the supervisor's later
    :meth:`InterviewState.leading_template` read reflects evidence
    gathered across every turn so far. No-op scores (all zero) leave the
    weights unchanged.

    This never raises: a missing attribute or a malformed ``scores`` dict
    is logged and skipped. Like the rest of the classifier, it is on a
    path that must not crash the session.
    """
    if not isinstance(scores, dict):
        logger.warning("apply_scores got a non-dict scores value — skipping")
        return
    for name in SIGNAL_NAMES:
        field = f"{name}_signal"
        delta = _clamp_score(scores.get(name))
        if delta == 0.0:
            continue
        try:
            current = getattr(state, field)
            setattr(state, field, float(current) + delta)
        except Exception:  # noqa: BLE001
            logger.warning(
                "apply_scores could not update %s on the interview state — skipping",
                field,
                exc_info=True,
            )


__all__ = [
    "SIGNAL_NAMES",
    "CLASSIFIER_TIMEOUT_S",
    "NOOP_SCORES",
    "DEFAULT_MODEL",
    "FALLBACK_MODEL",
    "GROQ_PROVIDER_PIN",
    "OPENROUTER_BASE_URL",
    "API_KEY_ENV",
    "load_classifier_prompt",
    "get_openrouter_client",
    "parse_scores",
    "classify_turn",
    "apply_scores",
]
