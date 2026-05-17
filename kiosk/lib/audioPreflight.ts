"use client";

/**
 * Audio preflight — checks that a usable microphone exists and that the
 * browser will grant access to it, *before* the kiosk tries to join a
 * LiveKit room.
 *
 * Without this, a denied permission or an unplugged mic only surfaces deep
 * inside `<LiveKitRoom>` as a broken Active screen. Running the check up
 * front lets the Idle screen show a calm, specific message instead.
 *
 * This module is browser-only (it touches `navigator.mediaDevices`); import
 * it from client components.
 */

/** The outcome of an audio preflight check. */
export type AudioPreflightResult =
  | { ok: true }
  | { ok: false; reason: AudioPreflightFailure; message: string };

export type AudioPreflightFailure =
  | "unsupported" // browser has no getUserMedia (very old / insecure context)
  | "no-device" // no microphone hardware present
  | "denied" // the visitor (or OS) denied microphone access
  | "in-use" // the mic exists but another app holds it
  | "unknown"; // anything else

/** Calm, visitor-facing copy for each failure mode. Kept short for the kiosk. */
const MESSAGES: Record<AudioPreflightFailure, string> = {
  unsupported:
    "This kiosk's browser can't access a microphone. Please ask an attendant.",
  "no-device":
    "No microphone was found. Please ask an attendant to check the kiosk.",
  denied:
    "Microphone access is blocked. Please ask an attendant to enable it.",
  "in-use":
    "The microphone is busy. Please ask an attendant — it may need a moment.",
  unknown:
    "The microphone could not be started. Please ask an attendant.",
};

/**
 * Probe the microphone. Resolves with `ok: true` when a mic track can be
 * opened, or a typed failure with calm copy otherwise.
 *
 * The probe track is opened only to confirm access, then immediately
 * stopped — `<LiveKitRoom>` opens its own track when it connects. Holding
 * the probe track open would leave the mic "in use" for the real session.
 */
export async function checkAudioInput(): Promise<AudioPreflightResult> {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    return fail("unsupported");
  }

  let stream: MediaStream | null = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return { ok: true };
  } catch (err) {
    return fail(classifyError(err));
  } finally {
    // Release the probe track so the real LiveKit session can claim the mic.
    stream?.getTracks().forEach((t) => t.stop());
  }
}

/** Map a getUserMedia DOMException onto our typed failure set. */
function classifyError(err: unknown): AudioPreflightFailure {
  const name =
    err && typeof err === "object" && "name" in err
      ? String((err as { name: unknown }).name)
      : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "denied";
    case "NotFoundError":
    case "OverconstrainedError":
      return "no-device";
    case "NotReadableError":
    case "AbortError":
      return "in-use";
    default:
      return "unknown";
  }
}

function fail(reason: AudioPreflightFailure): AudioPreflightResult {
  return { ok: false, reason, message: MESSAGES[reason] };
}
