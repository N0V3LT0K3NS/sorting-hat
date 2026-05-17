"use client";

import { useEffect, useRef } from "react";
import { useKrispNoiseFilter } from "@livekit/components-react/krisp";

/**
 * Enables LiveKit's enhanced (Krisp) noise cancellation on the visitor's
 * microphone track.
 *
 * The kiosk lives in a public space — background chatter, footfall, HVAC,
 * music. Raw mic audio degrades turn detection and transcription on the
 * agent side, so we cancel noise *at the source* in the browser before the
 * audio is ever published over WebRTC.
 *
 * This component renders nothing. It must be mounted **inside** a
 * `<LiveKitRoom>` because the hook reaches for the local participant's
 * microphone publication. Krisp enhanced cancellation is a LiveKit Cloud
 * feature and is only supported on modern browsers; if the browser cannot
 * run the filter the hook logs a warning and the session continues with
 * plain WebRTC noise suppression — never a hard failure.
 */
export function NoiseCancellation() {
  const { setNoiseFilterEnabled, isNoiseFilterEnabled, isNoiseFilterPending } =
    useKrispNoiseFilter();
  // Guard so we only request enabling once per session, even though the
  // microphone publication arrives a beat after the room connects.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    // Fire-and-forget: a rejection here (unsupported browser, no Cloud
    // entitlement) is already logged inside the hook and must not break
    // the interview.
    void setNoiseFilterEnabled(true).catch(() => {
      /* handled inside the hook — kiosk degrades gracefully */
    });
  }, [setNoiseFilterEnabled]);

  // Expose status for debugging via a data attribute on a zero-size node;
  // invisible to the visitor, useful when inspecting the kiosk in the field.
  return (
    <span
      hidden
      data-noise-cancellation={
        isNoiseFilterPending
          ? "pending"
          : isNoiseFilterEnabled
            ? "on"
            : "off"
      }
    />
  );
}
