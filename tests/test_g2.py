"""G2 tests — config graceful degradation + dry-run session wiring.

Covers the two things the G2 goal must prove:

1. ``agent.config`` loads cleanly with AND without env vars present — a
   missing key degrades gracefully (logged warning, never a crash).
2. The ``--dry-run`` path constructs every session object — STT/TTS, the
   OpenRouter LLM, VAD, the ``AgentSession`` and ``Agent`` — without joining
   a live LiveKit room, and ``run_dry_run()`` returns exit code 0.

No network and no LiveKit room are touched; these tests run fully offline.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Env vars that drive config. Cleared/set per-test so the suite is hermetic.
_CONFIG_ENV_VARS = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "DEEPGRAM_API_KEY",
    "STT_MODEL",
    "TTS_MODEL",
    "INTERVIEWER_MODEL",
    "SESSIONS_DIR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every config env var so a test starts from a known-empty state."""
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Config — graceful degradation
# ---------------------------------------------------------------------------


def test_config_loads_with_no_env_vars(clean_env: None) -> None:
    """With no env vars, config still loads — every secret is simply None."""
    from agent.config import load_config

    cfg = load_config()

    # Secrets absent, but the object exists — import/load never crashes.
    assert cfg.livekit_url is None
    assert cfg.livekit_api_key is None
    assert cfg.livekit_api_secret is None
    assert cfg.openrouter_api_key is None

    # Disabled-feature flags reflect the missing keys.
    assert cfg.has_livekit is False
    assert cfg.has_openrouter is False

    # Defaults still apply for non-secret fields — the approved Decision 0001
    # per-job model selection (docs/decisions/0001-model-selection.md).
    assert cfg.stt_model == "deepgram/flux-general-en"
    assert cfg.tts_model == "inworld/inworld-tts-1.5-max"
    assert cfg.tts_fallback_model == "cartesia/sonic-3"
    assert cfg.llm_model == "anthropic/claude-haiku-4.5"
    assert cfg.llm_fallback_model == "anthropic/claude-sonnet-4.6"
    assert cfg.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert isinstance(cfg.sessions_dir, Path)


def test_config_warns_on_missing_keys_without_crashing(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing keys produce logged warnings and a names list — never an error."""
    from agent.config import load_config

    cfg = load_config()
    with caplog.at_level("WARNING", logger="sorting-hat.config"):
        missing = cfg.warn_missing()

    assert set(missing) == {"livekit", "openrouter"}
    # The warnings actually reached the log.
    assert any("LiveKit" in rec.message for rec in caplog.records)
    assert any("OPENROUTER_API_KEY" in rec.message for rec in caplog.records)


def test_config_loads_with_all_env_vars(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With every key present, config reports the features as enabled."""
    from agent.config import load_config

    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("INTERVIEWER_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))

    cfg = load_config()

    assert cfg.has_livekit is True
    assert cfg.has_openrouter is True
    assert cfg.livekit_url == "wss://example.livekit.cloud"
    assert cfg.openrouter_api_key == "or-key"
    assert cfg.llm_model == "anthropic/claude-3.5-sonnet"
    assert cfg.sessions_dir == tmp_path

    # Nothing is missing -> warn_missing reports an empty list.
    assert cfg.warn_missing() == []


def test_partial_livekit_credentials_degrade_gracefully(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial LiveKit credentials count as 'not configured' — no half-state."""
    from agent.config import load_config

    # URL + key, but no secret — has_livekit must still be False.
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")

    cfg = load_config()
    assert cfg.has_livekit is False


def test_config_module_imports_without_env(clean_env: None) -> None:
    """Re-importing agent.config with no env vars must not raise."""
    import agent.config as config_mod

    # A fresh import exercises the module-level load_config() singleton.
    reloaded = importlib.reload(config_mod)
    assert reloaded.config is not None
    assert reloaded.config.has_livekit is False


def test_blank_env_var_is_treated_as_missing(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty / whitespace env var is treated as absent, not as a real key."""
    from agent.config import load_config

    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    cfg = load_config()
    assert cfg.openrouter_api_key is None
    assert cfg.has_openrouter is False


# ---------------------------------------------------------------------------
# Dry-run — session wiring constructs without a live room
# ---------------------------------------------------------------------------


def test_build_session_constructs_all_objects(clean_env: None) -> None:
    """build_session() constructs STT/TTS/LLM/VAD/AgentSession/Agent offline."""
    from agent.config import load_config
    from agent.main import build_session

    parts = build_session(load_config())

    # Each piece exists and is the expected kind of object.
    assert parts.session.__class__.__name__ == "AgentSession"
    assert parts.agent.__class__.__name__ == "Agent"
    assert parts.llm.__class__.__name__ == "LLM"
    assert parts.vad.__class__.__name__ == "VAD"


def test_build_llm_targets_openrouter(clean_env: None) -> None:
    """The LLM is built against the OpenRouter base URL with the chosen model."""
    from agent.config import load_config
    from agent.main import build_llm

    cfg = load_config()
    llm = build_llm(cfg)

    assert llm.model == cfg.llm_model
    # The OpenAI-compatible client must point at the OpenRouter gateway.
    assert "openrouter.ai" in str(llm._client.base_url)


def test_interviewer_llm_has_max_tokens_cap(clean_env: None) -> None:
    """The interviewer LLM is built with the max_tokens cap applied.

    An unbounded reply in a real-time voice loop can starve the TTS pipeline,
    so build_llm() caps the reply length at INTERVIEWER_MAX_TOKENS.
    """
    from agent.config import INTERVIEWER_MAX_TOKENS, load_config
    from agent.main import build_llm

    llm = build_llm(load_config())

    assert llm._opts.max_completion_tokens == INTERVIEWER_MAX_TOKENS
    # A short cap — spoken follow-ups, never monologues.
    assert 0 < INTERVIEWER_MAX_TOKENS <= 500


# ---------------------------------------------------------------------------
# STT — provider route selection (direct Deepgram vs LiveKit Inference)
# ---------------------------------------------------------------------------


def test_config_has_deepgram_reflects_key(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """has_deepgram is True only when a non-blank DEEPGRAM_API_KEY is set."""
    from agent.config import load_config

    # Absent -> False.
    assert load_config().has_deepgram is False

    # Blank / whitespace -> still treated as absent.
    monkeypatch.setenv("DEEPGRAM_API_KEY", "   ")
    cfg = load_config()
    assert cfg.deepgram_api_key is None
    assert cfg.has_deepgram is False

    # Real value -> True.
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    cfg = load_config()
    assert cfg.deepgram_api_key == "dg-key"
    assert cfg.has_deepgram is True


def test_build_stt_uses_inference_without_deepgram_key(clean_env: None) -> None:
    """With no DEEPGRAM_API_KEY, STT falls back to the LiveKit Inference path."""
    from agent.config import load_config
    from agent.main import build_stt

    stt = build_stt(load_config())

    # inference.STT — the bundled LiveKit Inference gateway.
    assert stt.__class__.__module__.startswith("livekit.agents.inference")
    assert stt.__class__.__name__ == "STT"


def test_build_stt_uses_direct_deepgram_when_key_present(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a DEEPGRAM_API_KEY, STT uses the direct livekit-plugins-deepgram plugin."""
    from agent.config import DIRECT_STT_MODEL, load_config
    from agent.main import build_stt

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    stt = build_stt(load_config())

    # The direct Deepgram plugin's Flux v2 STT — bypasses LiveKit Inference.
    assert stt.__class__.__module__.startswith("livekit.plugins.deepgram")
    assert stt.__class__.__name__ == "STTv2"
    # The bare Flux model name (no "deepgram/" Inference prefix).
    assert DIRECT_STT_MODEL == "flux-general-en"


def test_dry_run_exits_zero(clean_env: None) -> None:
    """run_dry_run() validates wiring and returns exit code 0 with no keys."""
    from agent.main import run_dry_run

    assert run_dry_run() == 0


def test_dry_run_exits_zero_with_keys_present(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_dry_run() also returns 0 when credentials are present."""
    from agent.main import run_dry_run

    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    assert run_dry_run() == 0


def test_dry_run_logs_inference_stt_path_without_key(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """With no DEEPGRAM_API_KEY, the dry-run log reports the Inference STT path."""
    from agent.main import run_dry_run

    with caplog.at_level("INFO", logger="sorting-hat.agent"):
        assert run_dry_run() == 0

    assert any(
        "STT path: LiveKit Inference" in rec.message for rec in caplog.records
    )


def test_dry_run_logs_direct_deepgram_stt_path_with_key(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With a DEEPGRAM_API_KEY, the dry-run log reports the direct Deepgram path."""
    from agent.main import run_dry_run

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    with caplog.at_level("INFO", logger="sorting-hat.agent"):
        assert run_dry_run() == 0

    assert any(
        "STT path: DIRECT Deepgram" in rec.message for rec in caplog.records
    )


def test_main_dry_run_flag_returns_zero(clean_env: None) -> None:
    """The --dry-run CLI flag routes to run_dry_run() and returns 0."""
    from agent.main import main

    assert main(["--dry-run"]) == 0


def test_instrument_latency_attaches_cleanly(clean_env: None) -> None:
    """The latency hook attaches to a constructed session without error."""
    from agent.config import load_config
    from agent.main import build_session, instrument_latency

    parts = build_session(load_config())
    # Wiring the hook must not raise; it only registers event listeners.
    instrument_latency(parts.session)
