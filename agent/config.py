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
# These are the LiveKit Inference model strings (confirmed against the
# installed livekit-agents 1.5.9 inference.STTModels / inference.TTSModels
# literals) and the OpenRouter model slug for the interviewer's brain.

# Deepgram Flux — conversational STT with built-in turn detection.
DEFAULT_STT_MODEL = "deepgram/flux-general-en"

# Cartesia Sonic-3 — warm, low-latency conversational TTS.
DEFAULT_TTS_MODEL = "cartesia/sonic-3"

# Interviewer LLM, routed through OpenRouter. Overridable via INTERVIEWER_MODEL
# so a live smoke test can swap models without a code change.
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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

    # --- Model selection ----------------------------------------------------
    stt_model: str
    tts_model: str
    llm_model: str

    # --- Runtime ------------------------------------------------------------
    sessions_dir: Path

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

    return Config(
        livekit_url=_clean(os.getenv("LIVEKIT_URL")),
        livekit_api_key=_clean(os.getenv("LIVEKIT_API_KEY")),
        livekit_api_secret=_clean(os.getenv("LIVEKIT_API_SECRET")),
        openrouter_api_key=_clean(os.getenv("OPENROUTER_API_KEY")),
        openrouter_base_url=_clean(os.getenv("OPENROUTER_BASE_URL"))
        or OPENROUTER_BASE_URL,
        stt_model=_clean(os.getenv("STT_MODEL")) or DEFAULT_STT_MODEL,
        tts_model=_clean(os.getenv("TTS_MODEL")) or DEFAULT_TTS_MODEL,
        llm_model=_clean(os.getenv("INTERVIEWER_MODEL")) or DEFAULT_LLM_MODEL,
        sessions_dir=sessions_dir,
    )


# Module-level singleton — the common case. Import-safe even with no .env.
config = load_config()
