"use client";

import { useEffect, useRef, useState } from "react";
import {
  BarVisualizer,
  RoomAudioRenderer,
  useVoiceAssistant,
  useConnectionState,
} from "@livekit/components-react";
import { ConnectionState } from "livekit-client";
import styles from "../app/kiosk.module.css";
import { SessionProgress } from "./SessionProgress";

/**
 * Screen 2 — Active. Rendered inside a `<LiveKitRoom>`. Shows the agent's
 * waveform via `BarVisualizer` and a subtle speaking indicator. No transcript
 * by default — seeing one's own words changes the dynamic of the interview.
 * A hidden corner toggle reveals a dev transcript view.
 *
 * Calls `onComplete` when the room disconnects (the agent ends the session)
 * or when the agent worker never joins within a grace period — a kiosk must
 * never strand a visitor on a frozen "Connecting…" screen.
 *
 * The End button is **state-aware**: it polls the delivery server's
 * `GET /live/<session-id>` for the interview's phase and only appears once
 * there is enough material to make a portrait from. Before the base questions
 * are done it is hidden entirely; during the probe it is a soft "End early"
 * affordance; once routing has settled it is the confident "I'm done" button.
 * Whichever way End is pressed, the agent side runs the offline pipeline on
 * whatever transcript exists — ending early yields a thinner portrait, never
 * none.
 */

/** The interview phases the delivery server's `/live` endpoint reports. */
type LivePhase = "pending" | "base_questions" | "probing" | "complete";

/** How often to poll the live-state endpoint while the interview runs. */
const LIVE_POLL_INTERVAL_MS = 3_000;

export function ActiveScreen({
  sessionId,
  onComplete,
}: {
  sessionId: string | null;
  onComplete: () => void;
}) {
  const { state, audioTrack, agentTranscriptions } = useVoiceAssistant();
  const connectionState = useConnectionState();
  const [showTranscript, setShowTranscript] = useState(false);
  // True once we have given up waiting for the agent worker to appear.
  const [agentTimedOut, setAgentTimedOut] = useState(false);
  // Whether the agent has been observed in the room at least once.
  const agentSeen = useRef(false);
  // Whether the room has reached Connected at least once. Guards against the
  // initial Disconnected value of useConnectionState() being mistaken for the
  // interview ending the instant this screen mounts.
  const hasConnected = useRef(false);
  // The interview's phase, polled from the delivery server's /live endpoint —
  // it gates the End button. Starts "pending": no End button until there is
  // enough material to make a portrait from.
  const [livePhase, setLivePhase] = useState<LivePhase>("pending");

  // Poll GET /live/<session-id> for the interview phase (same pattern as
  // CompleteScreen's status poll). A failed poll is non-fatal — keep the last
  // known phase and try again; a missing session id just never polls.
  useEffect(() => {
    if (!sessionId) return;

    const baseUrl = (
      process.env.NEXT_PUBLIC_DELIVERY_SERVER_URL ?? "http://localhost:8808"
    ).replace(/\/+$/, "");

    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;
      try {
        const res = await fetch(`${baseUrl}/live/${sessionId}`, {
          cache: "no-store",
        });
        if (res.ok) {
          const data = (await res.json()) as { phase?: string };
          if (!stopped && isLivePhase(data.phase)) setLivePhase(data.phase);
        }
      } catch {
        // A single failed poll is not fatal — the delivery server may be
        // starting, or the wifi hiccuped. Keep the last phase and retry.
      }
      if (!stopped) timer = setTimeout(poll, LIVE_POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId]);

  // When the room disconnects, the interview is over -> Complete screen.
  // The initial Disconnected state (and Connecting) is inert: only a
  // disconnect *after* a genuine Connected counts as the session ending.
  useEffect(() => {
    if (connectionState === ConnectionState.Connected) {
      hasConnected.current = true;
    } else if (
      connectionState === ConnectionState.Disconnected &&
      hasConnected.current
    ) {
      onComplete();
    }
  }, [connectionState, onComplete]);

  // Note when the agent first becomes active — any non-disconnected voice
  // state means the worker has joined and the session is genuinely live.
  useEffect(() => {
    if (state && state !== "disconnected" && state !== "connecting") {
      agentSeen.current = true;
    }
  }, [state]);

  // Agent-join watchdog: if the worker is not dispatched into the room
  // within the grace period, end the session calmly instead of hanging.
  useEffect(() => {
    const t = setTimeout(() => {
      if (!agentSeen.current) setAgentTimedOut(true);
    }, AGENT_JOIN_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (agentTimedOut) onComplete();
  }, [agentTimedOut, onComplete]);

  const speaking = state === "speaking";
  const label = stateLabel(state, connectionState);

  return (
    <div className={styles.screen}>
      {/* Plays the agent's audio. Without this the visitor hears nothing. */}
      <RoomAudioRenderer />

      <div className={styles.visualizer}>
        <BarVisualizer
          state={state}
          barCount={7}
          trackRef={audioTrack}
          options={{ minHeight: 8 }}
        />
      </div>

      <div className={styles.stateLabel} data-speaking={speaking}>
        <span className={styles.speakingDot} data-speaking={speaking} />
        {"  "}
        {label}
      </div>

      {/* A calm, building sense of how far the conversation has come. */}
      <SessionProgress />

      {/* The state-aware End button. Hidden until the base questions are
          done — before then the interview genuinely lacks the material for a
          portrait. During the probe it is a soft "End early" affordance:
          allowed (a kiosk must never trap anyone) but quiet. Once routing has
          settled it becomes the confident primary "I'm done" button. Either
          way it routes through the same onComplete path the agent/disconnect
          uses, so the LiveKitRoom teardown happens exactly once — and the
          agent side runs the offline pipeline on whatever transcript exists. */}
      {livePhase === "probing" && (
        <button
          className={styles.endButtonSoft}
          onClick={onComplete}
          aria-label="End the interview early"
        >
          End early
        </button>
      )}
      {livePhase === "complete" && (
        <button
          className={styles.endButtonPrimary}
          onClick={onComplete}
          aria-label="Finish the interview"
        >
          I&rsquo;m done
        </button>
      )}

      {/* Hidden hotspot in the corner — for dev/debug only, off by default. */}
      <button
        className={styles.devToggle}
        onClick={() => setShowTranscript((v) => !v)}
        aria-label="Toggle developer transcript"
        tabIndex={-1}
      />

      {showTranscript && (
        <div className={styles.transcript}>
          {agentTranscriptions.length === 0 ? (
            <div className={styles.transcriptLine}>
              (transcript — dev view)
            </div>
          ) : (
            agentTranscriptions.map((seg) => (
              <div
                key={seg.id}
                className={styles.transcriptLine}
                data-from="agent"
              >
                {seg.text}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/**
 * How long to wait for the Python agent worker to join the room before
 * giving up. Generous — worker cold-start plus model warm-up can take a
 * few seconds — but bounded, so a misconfigured deployment fails visibly.
 */
const AGENT_JOIN_TIMEOUT_MS = 30_000;

/** Narrow an unknown `/live` phase value to a known `LivePhase`. */
function isLivePhase(value: unknown): value is LivePhase {
  return (
    value === "pending" ||
    value === "base_questions" ||
    value === "probing" ||
    value === "complete"
  );
}

/** A calm, human-readable label for the current voice-assistant state. */
function stateLabel(
  state: string | undefined,
  connection: ConnectionState,
): string {
  if (connection === ConnectionState.Connecting) return "Connecting…";
  if (connection === ConnectionState.Reconnecting) return "Reconnecting…";
  switch (state) {
    case "initializing":
      return "Just a moment…";
    case "listening":
      return "Listening";
    case "thinking":
      return "Thinking";
    case "speaking":
      return "Speaking";
    default:
      return "";
  }
}
