import { NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";

/**
 * Mints a short-lived LiveKit access token for one kiosk interview.
 *
 * The kiosk client calls this when the visitor presses "begin". A fresh
 * random room name is generated per interview so each visitor gets an
 * isolated session; the Python agent worker is dispatched into whatever
 * room it is assigned.
 *
 * If LiveKit credentials are not configured the route returns a 503 with a
 * calm message rather than crashing — the client surfaces this gently.
 */
export const dynamic = "force-dynamic";

function randomRoomName(): string {
  // e.g. "interview-3f9a2c" — readable, unique enough for a kiosk.
  const suffix = Math.random().toString(16).slice(2, 8);
  return `interview-${suffix}`;
}

export async function GET() {
  const url = process.env.LIVEKIT_URL?.trim();
  const apiKey = process.env.LIVEKIT_API_KEY?.trim();
  const apiSecret = process.env.LIVEKIT_API_SECRET?.trim();

  if (!url || !apiKey || !apiSecret) {
    return NextResponse.json(
      {
        error: "not_configured",
        message:
          "LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, " +
          "and LIVEKIT_API_SECRET in kiosk/.env.local.",
      },
      { status: 503 },
    );
  }

  const roomName = randomRoomName();
  // The visitor is anonymous — a stable-enough identity for one session.
  const identity = `visitor-${Math.random().toString(16).slice(2, 8)}`;

  try {
    const at = new AccessToken(apiKey, apiSecret, {
      identity,
      // Token outlives a 10-15 min interview with comfortable margin.
      ttl: "1h",
    });
    at.addGrant({
      room: roomName,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
    });

    const token = await at.toJwt();

    return NextResponse.json({ serverUrl: url, roomName, token });
  } catch (err) {
    console.error("Failed to mint LiveKit token:", err);
    return NextResponse.json(
      {
        error: "token_error",
        message: "Could not start a session. Please ask an attendant.",
      },
      { status: 500 },
    );
  }
}
