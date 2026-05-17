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

/**
 * Screen 2 — Active. Rendered inside a `<LiveKitRoom>`. Shows the agent's
 * waveform via `BarVisualizer` and a subtle speaking indicator. No transcript
 * by default — seeing one's own words changes the dynamic of the interview.
 * A hidden corner toggle reveals a dev transcript view.
 *
 * Calls `onComplete` when the room disconnects (the agent ends the session)
 * or when the agent worker never joins within a grace period — a kiosk must
 * never strand a visitor on a frozen "Connecting…" screen.
 */
export function ActiveScreen({ onComplete }: { onComplete: () => void }) {
  const { state, audioTrack, agentTranscriptions } = useVoiceAssistant();
  const connectionState = useConnectionState();
  const [showTranscript, setShowTranscript] = useState(false);
  // True once we have given up waiting for the agent worker to appear.
  const [agentTimedOut, setAgentTimedOut] = useState(false);
  // Whether the agent has been observed in the room at least once.
  const agentSeen = useRef(false);

  // When the room disconnects, the interview is over -> Complete screen.
  useEffect(() => {
    if (connectionState === ConnectionState.Disconnected) {
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
