"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveKitRoom } from "@livekit/components-react";
import type { AudioCaptureOptions, RoomConnectOptions } from "livekit-client";
import styles from "../app/kiosk.module.css";
import { IdleScreen } from "./IdleScreen";
import { ActiveScreen } from "./ActiveScreen";
import { CompleteScreen } from "./CompleteScreen";
import { NoiseCancellation } from "./NoiseCancellation";
import { checkAudioInput } from "../lib/audioPreflight";
import { installKioskGuards, requestFullscreen } from "../lib/kioskMode";

/**
 * The kiosk session lifecycle.
 *
 *   idle ──press begin──▶ active ──interview ends / disconnect──▶ complete
 *    ▲                                                              │
 *    └──────────────────── auto-reset after a pause ────────────────┘
 *
 * One physical kiosk runs many interviews back to back, so the single most
 * important property here is that **no state leaks between sessions**: every
 * session gets a fresh token, a fresh room name, and — critically — a fresh
 * `<LiveKitRoom>` instance (forced by the `sessionKey` remount). When the
 * room unmounts, livekit-client tears down the WebRTC peer connection and
 * releases the microphone; the next visitor starts from a clean slate.
 */
type Phase = "idle" | "active" | "complete";

interface Connection {
  serverUrl: string;
  token: string;
  roomName: string;
}

/** How long the Complete screen lingers before the kiosk resets to Idle. */
const COMPLETE_DWELL_MS = 9_000;

/**
 * Audio-capture options — WebRTC noise/echo suppression as a baseline.
 * Krisp enhanced cancellation (see `<NoiseCancellation />`) layers on top
 * of this; together they cover both supported and unsupported browsers.
 */
const AUDIO_CAPTURE: AudioCaptureOptions = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};

/** Keep trying to reconnect briefly if the venue Wi-Fi hiccups mid-session. */
const CONNECT_OPTIONS: RoomConnectOptions = { autoSubscribe: true };

export function Kiosk() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  // Bumped on every reset — forces a full <LiveKitRoom> remount so no
  // connection state survives into the next visitor's session.
  const [sessionKey, setSessionKey] = useState(0);
  // Guards against a double-fire of completion (onDisconnected + onError, or
  // the ActiveScreen's own connection-state effect) advancing twice.
  const completed = useRef(false);

  // Install kiosk-mode guards once for the life of the page.
  useEffect(() => installKioskGuards(), []);

  /** Return the kiosk to Idle, fully cleaned, ready for the next person. */
  const resetToIdle = useCallback(() => {
    completed.current = false;
    setConnection(null);
    setError(null);
    setConnecting(false);
    setPhase("idle");
    // New key => the next session mounts a brand-new room object.
    setSessionKey((k) => k + 1);
  }, []);

  // Idle -> Active: preflight the mic, mint a token, then join the room.
  const handleBegin = useCallback(async () => {
    setConnecting(true);
    setError(null);

    // 1. Preflight audio. A denied permission or missing mic is shown
    //    calmly on the Idle screen rather than as a broken Active screen.
    const audio = await checkAudioInput();
    if (!audio.ok) {
      setError(audio.message);
      setConnecting(false);
      return;
    }

    // 2. Best-effort fullscreen — must run inside this user gesture.
    void requestFullscreen();

    // 3. Mint a token for a fresh room and join.
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
      completed.current = false;
      setConnection(data);
      setPhase("active");
    } catch {
      // Network failure, dev server down, etc. — calm, never a crash.
      setError(
        "Could not reach the interview service. Please ask an attendant.",
      );
    } finally {
      setConnecting(false);
    }
  }, []);

  // Active -> Complete: the room disconnected. This fires when the agent
  // ends the interview, when the visitor walks away and the connection
  // drops, or on an outright connection error — all roads lead to a calm
  // Complete screen, then an automatic reset.
  const handleComplete = useCallback(() => {
    if (completed.current) return; // idempotent — see `completed` ref above
    completed.current = true;
    setConnection(null);
    setPhase("complete");
  }, []);

  // While on the Complete screen, hold for a moment, then reset to Idle so
  // the kiosk is ready for the next visitor with no attendant intervention.
  useEffect(() => {
    if (phase !== "complete") return;
    const t = setTimeout(resetToIdle, COMPLETE_DWELL_MS);
    return () => clearTimeout(t);
  }, [phase, resetToIdle]);

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
          // The key forces a fresh room per session — no state leaks.
          key={sessionKey}
          serverUrl={connection.serverUrl}
          token={connection.token}
          connect
          audio={AUDIO_CAPTURE}
          video={false}
          connectOptions={CONNECT_OPTIONS}
          // Disconnect, hard error, or a device failure that slips past the
          // preflight check — all end the session gracefully.
          onDisconnected={handleComplete}
          onError={handleComplete}
          onMediaDeviceFailure={handleComplete}
        >
          {/* Enhanced (Krisp) noise cancellation for the rough public-space
              audio. Mounted inside the room so it can reach the mic track. */}
          <NoiseCancellation />
          <ActiveScreen onComplete={handleComplete} />
        </LiveKitRoom>
      )}

      {phase === "complete" && <CompleteScreen />}
    </main>
  );
}
