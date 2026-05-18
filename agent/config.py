"""Typed runtime configuration for the sorting-hat voice agent.

Loads environment variables (via ``python-dotenv``) once at import time and
exposes them as a frozen, typed :class:`Config`. The guiding rule from the
README's locked constraints and ``.env.example``:

    Missing keys DISABLE the dependent feature; they never block the app.

So importing this module is always safe: a missing ``LIVEKIT_API_KEY`` or
``OPENROUTER_API_KEY`` produces a logged warning, not an exception. Code that
actually needs a key checks the relevant ``has_*`` property before using it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("sorting-hat.config")

# Load .env if present. override=False so a real shell environment wins over
# a checked-out .env, which matters in CI and on the kiosk.
load_dotenv(override=False)


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Per-job model selection per the approved Decision 0001 (model-selection):
# docs/decisions/0001-model-selection.md. Each job has an approved PRIMARY
# (the default below) and a FALLBACK; both are overridable by env var so a
# deployment — or a G17 live test — can swap models without a code change.
# These are the LiveKit Inference model strings (confirmed against the
# installed livekit-agents 1.5.9 inference.STTModels / inference.TTSModels
# literals) and OpenRouter model slugs.

# --- Job 5 · STT — Deepgram Flux, conversational STT with turn detection. ---
# The Inference model string (deepgram/<model>) and, when a direct
# DEEPGRAM_API_KEY is set, the bare model name fed to the direct plugin.
DEFAULT_STT_MODEL = "deepgram/flux-general-en"
DIRECT_STT_MODEL = "flux-general-en"

# --- Job 6 · TTS — Inworld TTS 1.5-max (primary), Cartesia Sonic-3 (fallback).
# Inworld leads blind-preference for emotional realism; Sonic-3 is the
# graceful-degradation fallback.
DEFAULT_TTS_MODEL = "inworld/inworld-tts-1.5-max"
FALLBACK_TTS_MODEL = "cartesia/sonic-3"

# --- Job 1 · Interviewer brain — Claude Haiku 4.5 (primary), Sonnet 4.6
# (fallback). Haiku's ~0.6-0.85s TTFT fits the sub-800ms voice budget;
# reasoning is left OFF (the in-call job is bounded, not frontier reasoning).
# Provider routing sorts by latency — prefer the lowest-p50 endpoint.
DEFAULT_LLM_MODEL = "anthropic/claude-haiku-4.5"
FALLBACK_LLM_MODEL = "anthropic/claude-sonnet-4.6"

# Cap on the interviewer LLM's reply length. The interviewer asks short
# spoken follow-up questions and never monologues; an unbounded reply in a
# real-time voice loop can starve the TTS pipeline. ~250 tokens is comfortably
# above any single spoken question.
INTERVIEWER_MAX_TOKENS = 250

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ---------------------------------------------------------------------------
# Delivery server
# ---------------------------------------------------------------------------
# The local kiosk delivery server (delivery/server.py) serves finished
# portraits and exposes a pipeline-progress status endpoint the kiosk polls.
# It binds 0.0.0.0 on DELIVERY_SERVER_PORT so phones on the same wifi can
# reach the per-session portrait page the QR code points at.

#: Default port the delivery server binds.
DEFAULT_DELIVERY_SERVER_PORT = 8808

#: Default delivery-server base URL. On a real kiosk this should be set to the
#: kiosk machine's LAN IP (e.g. http://192.168.1.42:8808) so the QR scans from
#: a phone — localhost only works for a browser on the kiosk itself.
DEFAULT_DELIVERY_SERVER_URL = "http://localhost:8808"


def _clean(value: str | None) -> str | None:
    """Return a stripped value, or ``None`` if it is empty/whitespace."""
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Config:
    """Immutable typed view of the agent's runtime environment.

    Every field has a safe default; absent secrets are simply ``None``. Use
    :meth:`warn_missing` once at startup to surface what is disabled.
    """

    # --- LiveKit transport --------------------------------------------------
    livekit_url: str | None
    livekit_api_key: str | None
    livekit_api_secret: str | None

    # --- OpenRouter (LLM gateway) -------------------------------------------
    openrouter_api_key: str | None
    openrouter_base_url: str

    # --- OpenAI (render stage) ----------------------------------------------
    # Powers the offline render stage's gpt-image-2 calls (pipeline/render.py).
    # Absent -> render degrades to a minimal Pillow text render, never blocks.
    openai_api_key: str | None

    # --- Deepgram (direct STT) ----------------------------------------------
    # When present, STT talks straight to Deepgram's API via the direct
    # livekit-plugins-deepgram plugin, bypassing LiveKit Inference and its
    # rate limit. Absent -> STT falls back to the Inference path.
    deepgram_api_key: str | None

    # --- Model selection ----------------------------------------------------
    # Each job carries its approved primary plus the fallback to swap to if
    # the primary is unavailable (Decision 0001). No circuit breaker — the
    # fallback is simply a configurable model string per that decision.
    stt_model: str
    tts_model: str
    tts_fallback_model: str
    llm_model: str
    llm_fallback_model: str

    # --- Runtime ------------------------------------------------------------
    sessions_dir: Path

    # --- Delivery server ----------------------------------------------------
    # The local kiosk server that serves portraits + the status endpoint.
    delivery_server_port: int
    delivery_server_url: str

    @property
    def has_livekit(self) -> bool:
        """True when all three LiveKit credentials are present.

        A live room join needs the URL, key and secret together — partial
        credentials are as useless as none, so they are reported as one unit.
        """
        return all(
            (self.livekit_url, self.livekit_api_key, self.livekit_api_secret)
        )

    @property
    def has_openrouter(self) -> bool:
        """True when an OpenRouter API key is available for LLM calls."""
        return self.openrouter_api_key is not None

    @property
    def has_openai(self) -> bool:
        """True when an OpenAI API key is available for the render stage.

        When True, ``pipeline.render`` fills meme templates with gpt-image-2.
        When False, render falls back to a minimal Pillow text render —
        graceful degradation, the offline pipeline never blocks.
        """
        return self.openai_api_key is not None

    @property
    def has_deepgram(self) -> bool:
        """True when a direct Deepgram API key is available.

        When True, STT is constructed with the direct livekit-plugins-deepgram
        plugin (straight to Deepgram's API). When False, STT falls back to the
        LiveKit Inference path — graceful degradation, nothing breaks.
        """
        return self.deepgram_api_key is not None

    def warn_missing(self) -> list[str]:
        """Log a clear warning for each disabled feature; return their names.

        Called once at startup. Never raises — graceful degradation is a
        locked constraint. The returned list is handy for tests and for the
        ``--dry-run`` report.
        """
        missing: list[str] = []
        if not self.has_livekit:
            missing.append("livekit")
            logger.warning(
                "LiveKit credentials incomplete (need LIVEKIT_URL, "
                "LIVEKIT_API_KEY, LIVEKIT_API_SECRET) — live room join is "
                "disabled. --dry-run still works."
            )
        if not self.has_openrouter:
            missing.append("openrouter")
            logger.warning(
                "OPENROUTER_API_KEY is not set — the interviewer LLM is "
                "disabled. The agent cannot speak in a live call."
            )
        return missing


def load_config() -> Config:
    """Build a :class:`Config` from the current process environment.

    Pure with respect to the environment: reads ``os.environ``, never writes,
    never raises. Call it again to pick up changes (tests rely on this).
    """
    sessions_dir = Path(_clean(os.getenv("SESSIONS_DIR")) or "./sessions")

    raw_port = _clean(os.getenv("DELIVERY_SERVER_PORT"))
    try:
        delivery_server_port = int(raw_port) if raw_port else DEFAULT_DELIVERY_SERVER_PORT
    except ValueError:
        logger.warning(
            "DELIVERY_SERVER_PORT=%r is not an integer — using default %d",
            raw_port,
            DEFAULT_DELIVERY_SERVER_PORT,
        )
        delivery_server_port = DEFAULT_DELIVERY_SERVER_PORT

    return Config(
        livekit_url=_clean(os.getenv("LIVEKIT_URL")),
        livekit_api_key=_clean(os.getenv("LIVEKIT_API_KEY")),
        livekit_api_secret=_clean(os.getenv("LIVEKIT_API_SECRET")),
        openrouter_api_key=_clean(os.getenv("OPENROUTER_API_KEY")),
        openrouter_base_url=_clean(os.getenv("OPENROUTER_BASE_URL"))
        or OPENROUTER_BASE_URL,
        openai_api_key=_clean(os.getenv("OPENAI_API_KEY")),
        deepgram_api_key=_clean(os.getenv("DEEPGRAM_API_KEY")),
        stt_model=_clean(os.getenv("STT_MODEL")) or DEFAULT_STT_MODEL,
        tts_model=_clean(os.getenv("TTS_MODEL")) or DEFAULT_TTS_MODEL,
        tts_fallback_model=_clean(os.getenv("TTS_FALLBACK_MODEL"))
        or FALLBACK_TTS_MODEL,
        llm_model=_clean(os.getenv("INTERVIEWER_MODEL")) or DEFAULT_LLM_MODEL,
        llm_fallback_model=_clean(os.getenv("INTERVIEWER_FALLBACK_MODEL"))
        or FALLBACK_LLM_MODEL,
        sessions_dir=sessions_dir,
        delivery_server_port=delivery_server_port,
        delivery_server_url=_clean(os.getenv("DELIVERY_SERVER_URL"))
        or DEFAULT_DELIVERY_SERVER_URL,
    )


# Module-level singleton — the common case. Import-safe even with no .env.
config = load_config()
