"use client";

import { useCallback, useState } from "react";
import { LiveKitRoom } from "@livekit/components-react";
import styles from "../app/kiosk.module.css";
import { IdleScreen } from "./IdleScreen";
import { ActiveScreen } from "./ActiveScreen";
import { CompleteScreen } from "./CompleteScreen";

/** The three-screen state machine: idle -> active -> complete. */
type Phase = "idle" | "active" | "complete";

interface Connection {
  serverUrl: string;
  token: string;
  roomName: string;
}

export function Kiosk() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);

  // Idle -> Active: fetch a token, then join the LiveKit room.
  const handleBegin = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      const res = await fetch("/api/token", { cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(
          body.message ??
            "Could not start a session. Please ask an attendant.",
        );
        setConnecting(false);
        return;
      }
      const data = (await res.json()) as Connection;
      setConnection(data);
      setPhase("active");
    } catch {
      // Network failure, dev server down, etc. — calm, never a crash.
      setError("Could not reach the interview service. Please ask an attendant.");
    } finally {
      setConnecting(false);
    }
  }, []);

  // Active -> Complete: the room disconnected (agent ended the interview).
  const handleComplete = useCallback(() => {
    setConnection(null);
    setPhase("complete");
  }, []);

  return (
    <main className={styles.stage}>
      {phase === "idle" && (
        <IdleScreen
          onBegin={handleBegin}
          connecting={connecting}
          error={error}
        />
      )}

      {phase === "active" && connection && (
        <LiveKitRoom
          serverUrl={connection.serverUrl}
          token={connection.token}
          connect
          audio
          video={false}
          // If the connection drops or fails outright, end gracefully.
          onDisconnected={handleComplete}
          onError={handleComplete}
        >
          <ActiveScreen onComplete={handleComplete} />
        </LiveKitRoom>
      )}

      {phase === "complete" && <CompleteScreen />}
    </main>
  );
}
