"""sorting-hat — G2 walking skeleton: one LiveKit voice agent that talks.

This is the brief's step 1. No interview logic, no probes, no classification —
just a working low-latency voice loop, to prove the stack:

* **STT / TTS** via LiveKit Inference (Deepgram Flux STT, Cartesia Sonic-3 TTS)
  behind the single ``LIVEKIT`` key.
* **LLM** via OpenRouter — OpenAI-compatible — using the ``openai`` plugin's
  ``with_openrouter`` factory (``base_url=https://openrouter.ai/api/v1``).
* **Native turn detector** (the transformer EOU model), the **adaptive
  interruption classifier**, and **preemptive generation** all enabled.

It also instruments end-of-user-speech -> start-of-agent-speech latency.

Run modes
---------
``python -m agent.main --dry-run``
    Validate all session wiring — config loads, every plugin constructs,
    the ``AgentSession`` builds — WITHOUT joining a live LiveKit room.
    Exits 0 on success. This is G2's proving command.

``python -m agent.main [start|dev|console]``
    Hand off to the LiveKit Agents CLI worker (needs real credentials and a
    room). Requires ``LIVEKIT_*`` + ``OPENROUTER_API_KEY``.

.. note::
   Streaming the LLM through a *custom* ``base_url`` is the one fragile spot.
   ``with_openrouter`` builds an ``openai.LLM`` whose ``.chat()`` streams
   token deltas over the OpenRouter route — streaming is the plugin default
   and is not disabled here. A LIVE smoke test must still confirm that the
   chosen interviewer model actually token-streams through OpenRouter; that
   cannot be verified by ``--dry-run`` alone (no network, no room).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    inference,
)
from livekit.agents.voice import (
    AgentStateChangedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from agent.config import Config, load_config
from agent.interviewer import InterviewerAgent
from agent.state import InterviewState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sorting-hat.agent")


# The agent's instructions. G2 is a walking skeleton — a warm, brief
# conversationalist, deliberately with NO interview structure. The real
# InterviewerAgent persona lands in G3.
SKELETON_INSTRUCTIONS = (
    "You are a warm, curious conversationalist running a quick voice check. "
    "Speak naturally and concisely — one or two sentences per turn. Greet the "
    "person, ask how they are, and keep a light back-and-forth going. Do not "
    "mention that this is a test."
)


# ---------------------------------------------------------------------------
# Plugin construction — shared by --dry-run and the live worker
# ---------------------------------------------------------------------------


@dataclass
class SessionParts:
    """The constructed pieces of a voice session, for inspection/wiring.

    The native EOU turn detector is not held here: it can only be
    instantiated inside a job context (it needs the worker's inference
    executor) and ``AgentSession.turn_detection`` is read-only, so it is
    passed to the :class:`Agent` built in :func:`entrypoint`.
    """

    session: AgentSession
    agent: Agent
    llm: openai.LLM
    vad: silero.VAD
    state: InterviewState


def build_llm(cfg: Config) -> openai.LLM:
    """Construct the interviewer LLM, routed through OpenRouter.

    Uses the ``openai`` plugin's ``with_openrouter`` factory — an OpenAI-
    compatible client pointed at ``base_url=https://openrouter.ai/api/v1``.
    Streaming is the plugin default for ``LLM.chat()`` and is intentionally
    left enabled; the fragile bit (token-streaming through a custom base_url)
    must be confirmed by a live smoke test, see this module's docstring.

    A missing key does not raise — graceful degradation is a locked
    constraint. When ``OPENROUTER_API_KEY`` is absent the plugin is built with
    a placeholder so wiring still validates; it would only fail at actual
    call time (and ``cfg.warn_missing()`` has already logged the warning).
    """
    # with_openrouter() rejects an empty key outright, so substitute a clearly
    # non-functional placeholder when none is configured. No network call is
    # made at construction time; --dry-run never reaches a live request.
    api_key = cfg.openrouter_api_key or "missing-openrouter-key"
    return openai.LLM.with_openrouter(
        model=cfg.llm_model,
        api_key=api_key,
        base_url=cfg.openrouter_base_url,
        app_name="sorting-hat",
    )


def build_session(cfg: Config) -> SessionParts:
    """Construct an :class:`AgentSession` + :class:`Agent` with full wiring.

    STT/TTS are passed as LiveKit Inference model strings (resolved behind the
    ``LIVEKIT`` key at connect time). The adaptive interruption classifier and
    preemptive generation are enabled here; the native turn detector is wired
    onto the ``Agent`` in :func:`entrypoint` because it needs a job context.

    This never joins a room — it only builds objects, so it is the heart of
    the ``--dry-run`` validation path. The ``Agent`` built here has no turn
    detector and is replaced in the live path.
    """
    llm = build_llm(cfg)

    # Silero VAD — required for endpointing and as the substrate the adaptive
    # interruption classifier and the native turn detector build on.
    vad = silero.VAD.load()

    # LiveKit Inference STT/TTS — Deepgram Flux STT, Cartesia Sonic-3 TTS,
    # both behind the single LIVEKIT key. Constructed explicitly (rather than
    # passed as bare model strings) so a placeholder key can stand in when
    # LIVEKIT_API_KEY is absent — graceful degradation; no network call is
    # made at construction time.
    livekit_key = cfg.livekit_api_key or "missing-livekit-key"
    livekit_secret = cfg.livekit_api_secret or "missing-livekit-secret"
    stt = inference.STT(
        model=cfg.stt_model,
        api_key=livekit_key,
        api_secret=livekit_secret,
    )
    tts = inference.TTS(
        model=cfg.tts_model,
        api_key=livekit_key,
        api_secret=livekit_secret,
    )

    # The interview's shared state. It is the session's typed ``userdata`` so
    # the InterviewerAgent supervisor (G8) and the four probe AgentTasks all
    # read and write the same InterviewState — signal weights, base-question
    # progress, the chosen template, and the typed probe results.
    state = InterviewState()

    session: AgentSession = AgentSession(
        stt=stt,
        tts=tts,
        llm=llm,
        vad=vad,
        # Typed shared state for the whole interview. The InterviewerAgent
        # reads its routing inputs (signal weights) and writes its verdict
        # (chosen_template + result) here; probe tasks run inside this session
        # and share the same userdata.
        userdata=state,
        # turn_handling bundles three behaviours for G2:
        #  * preemptive_generation — draft the reply before the user's turn is
        #    fully confirmed, then commit it the instant the turn ends.
        #  * interruption mode "adaptive" — the ML-based interruption
        #    classifier, distinguishing a real interruption from a backchannel
        #    ("mhm", "yeah").
        turn_handling=TurnHandlingOptions(
            preemptive_generation={"enabled": True},
            interruption={"mode": "adaptive"},
        ),
    )

    agent = Agent(instructions=SKELETON_INSTRUCTIONS)

    return SessionParts(session=session, agent=agent, llm=llm, vad=vad, state=state)


# ---------------------------------------------------------------------------
# Latency instrumentation
# ---------------------------------------------------------------------------


def instrument_latency(session: AgentSession) -> None:
    """Wire up end-of-user-speech -> start-of-agent-speech latency logging.

    The user-perceived responsiveness metric: the gap between the user
    finishing their turn and the agent's voice starting. We capture it from
    two session events:

    * ``user_state_changed`` to ``"listening"`` — the user stopped speaking.
    * ``agent_state_changed`` to ``"speaking"`` — the agent's voice started.

    The hook is wired unconditionally, but only fires in a real call (there is
    no audio in ``--dry-run``).
    """
    # Mutable single-slot timestamp shared by the two closures.
    last_user_speech_end: list[float | None] = [None]

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        # The user transitions away from "speaking" -> their turn just ended.
        if ev.old_state == "speaking" and ev.new_state != "speaking":
            last_user_speech_end[0] = time.monotonic()

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "speaking" and last_user_speech_end[0] is not None:
            latency_ms = (time.monotonic() - last_user_speech_end[0]) * 1000.0
            logger.info(
                "voice latency: end-of-user-speech -> start-of-agent-speech "
                "= %.0f ms",
                latency_ms,
            )
            # Consume it so a later agent state change isn't misattributed.
            last_user_speech_end[0] = None


# ---------------------------------------------------------------------------
# Live worker entrypoint
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker entrypoint — runs one interview per job.

    Builds the session, attaches the native turn detector (constructable now
    that a job context exists), wires latency instrumentation, joins the room,
    and starts the agent.
    """
    cfg = load_config()
    cfg.warn_missing()

    parts = build_session(cfg)

    # The InterviewerAgent is the supervisor: it owns the five base questions
    # and, once they are done, routes into the probe sub-interviews (G8). It
    # is wired to the same InterviewState that backs the session's userdata,
    # so its routing reads the signal weights the background classifier nudges
    # and writes its verdict where the offline pipeline can read it.
    #
    # The native transformer end-of-utterance turn detector needs the worker's
    # inference executor, so it is constructed here, inside the job context;
    # AgentSession.turn_detection is read-only, so the detector rides on the
    # Agent (which the session reads at start()).
    agent = InterviewerAgent(state=parts.state)
    agent.turn_detection = EnglishModel()

    instrument_latency(parts.session)

    await ctx.connect()
    await parts.session.start(agent=agent, room=ctx.room)


# ---------------------------------------------------------------------------
# --dry-run validation
# ---------------------------------------------------------------------------


def run_dry_run() -> int:
    """Validate all session wiring without joining a live room. Returns exit code.

    Confirms: config loads, missing keys degrade gracefully, every plugin
    constructs (STT/TTS strings, the OpenRouter LLM, Silero VAD), the
    ``AgentSession`` and ``Agent`` build, the native turn-detector class is
    importable, and the latency hook attaches. Exits 0 on success.
    """
    logger.info("=== sorting-hat G2 dry-run: validating session wiring ===")

    cfg = load_config()
    missing = cfg.warn_missing()
    logger.info(
        "config loaded: stt=%s tts=%s llm=%s sessions_dir=%s",
        cfg.stt_model,
        cfg.tts_model,
        cfg.llm_model,
        cfg.sessions_dir,
    )
    if missing:
        logger.info(
            "degraded features (missing keys, not fatal): %s",
            ", ".join(missing),
        )

    try:
        parts = build_session(cfg)
    except Exception:  # pragma: no cover - surfaced as a failed dry-run
        logger.exception("dry-run FAILED: could not build the session")
        return 1

    # The native turn detector class must be importable. Instantiation is
    # deferred to the job context (it needs the inference executor), so we
    # only assert the class is available here.
    assert EnglishModel is not None, "native turn detector unavailable"

    # The latency hook must attach cleanly to the constructed session.
    instrument_latency(parts.session)

    logger.info("session built: %s", type(parts.session).__name__)
    logger.info("agent built:   %s", type(parts.agent).__name__)
    logger.info("llm built:     %s (model=%s)", type(parts.llm).__name__, parts.llm.model)
    logger.info("vad built:     %s", type(parts.vad).__name__)
    logger.info("turn detector: %s (instantiated in job context)", EnglishModel.__name__)
    logger.info("interruption classifier: adaptive (enabled via turn_handling)")
    logger.info("preemptive generation: enabled")
    logger.info("latency instrumentation: wired")
    logger.info("=== dry-run OK — session wiring valid, exiting 0 ===")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point. ``--dry-run`` validates wiring; otherwise run the worker."""
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(
        prog="agent.main",
        description="sorting-hat G2 — single LiveKit voice agent.",
        add_help=False,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate session wiring without joining a live room, then exit.",
    )
    args, remaining = parser.parse_known_args(argv)

    if args.dry_run:
        return run_dry_run()

    # No --dry-run: hand the remaining args to the LiveKit Agents CLI worker.
    # cli.run_app reads sys.argv directly, so restore it minus our flag.
    sys.argv = [sys.argv[0], *remaining]
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
    return 0


if __name__ == "__main__":
    sys.exit(main())
