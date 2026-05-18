"use client";

import { useEffect, useRef, useState } from "react";
import {
  RoomAudioRenderer,
  useConnectionState,
  useLocalParticipant,
  useTrackVolume,
  useVoiceAssistant,
} from "@livekit/components-react";
import { ConnectionState, type LocalAudioTrack } from "livekit-client";
import styles from "../../app/shape-interface.module.css";
import { ShapeField } from "./ShapeField";
import { ShapeSessionProgress } from "./ShapeSessionProgress";

type LivePhase = "pending" | "base_questions" | "probing" | "complete";

const LIVE_POLL_INTERVAL_MS = 3_000;
const AGENT_JOIN_TIMEOUT_MS = 30_000;

export function ShapeActiveScreen({
  sessionId,
  onComplete,
}: {
  sessionId: string | null;
  onComplete: () => void;
}) {
  const { state, audioTrack, agentTranscriptions } = useVoiceAssistant();
  const { microphoneTrack } = useLocalParticipant();
  const connectionState = useConnectionState();
  const outputLevel = useTrackVolume(audioTrack);
  const inputLevel = useTrackVolume(
    microphoneTrack?.track as LocalAudioTrack | undefined,
  );
  const [showTranscript, setShowTranscript] = useState(false);
  const [agentTimedOut, setAgentTimedOut] = useState(false);
  const [livePhase, setLivePhase] = useState<LivePhase>("pending");
  const agentSeen = useRef(false);
  const hasConnected = useRef(false);

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
        // Keep the last known phase; delivery can be temporarily unreachable.
      }
      if (!stopped) timer = setTimeout(poll, LIVE_POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId]);

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

  useEffect(() => {
    if (state && state !== "disconnected" && state !== "connecting") {
      agentSeen.current = true;
    }
  }, [state]);

  useEffect(() => {
    const t = setTimeout(() => {
      if (!agentSeen.current) setAgentTimedOut(true);
    }, AGENT_JOIN_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (agentTimedOut) onComplete();
  }, [agentTimedOut, onComplete]);

  const visualPhase = getVisualPhase(state, connectionState);
  const label = stateLabel(state, connectionState);

  return (
    <div className={styles.screen}>
      <RoomAudioRenderer />
      <ShapeField
        phase={visualPhase}
        inputLevel={visualPhase === "listening" ? inputLevel : 0}
        outputLevel={visualPhase === "speaking" ? outputLevel : 0}
      />

      <section className={styles.activePanel}>
        <p className={styles.kicker}>live interview / blind sort</p>
        <h1 className={styles.activeTitle}>{label || "Finding signal"}</h1>
        <p className={styles.body}>
          Keep speaking. The interface listens for contour, tension, depth, and
          trajectory without naming the template.
        </p>
        <div className={styles.progressWrap}>
          <ShapeSessionProgress />
        </div>
      </section>

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

function isLivePhase(value: unknown): value is LivePhase {
  return (
    value === "pending" ||
    value === "base_questions" ||
    value === "probing" ||
    value === "complete"
  );
}

function getVisualPhase(
  state: string | undefined,
  connection: ConnectionState,
) {
  if (connection === ConnectionState.Connecting) return "connecting";
  if (state === "speaking") return "speaking";
  if (state === "thinking") return "thinking";
  if (state === "listening") return "listening";
  return "thinking";
}

function stateLabel(
  state: string | undefined,
  connection: ConnectionState,
): string {
  if (connection === ConnectionState.Connecting) return "Connecting";
  if (connection === ConnectionState.Reconnecting) return "Reconnecting";
  switch (state) {
    case "initializing":
      return "Just a moment";
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
