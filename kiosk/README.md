# sorting-hat kiosk

The fullscreen frontend for the sorting-hat voice interview. A person walks
up, sees an idle screen, presses one button, has a ~10–15 minute voice
conversation with the AI interviewer, and sees a calm completion screen.

This is a minimal Next.js app (App Router, TypeScript). It is **voice-only
from the visitor's perspective** — there is no on-screen survey and no text
fallback. That is one of the project's four locked constraints.

## The three screens

A single state machine: `idle → active → complete`.

1. **Idle** — "Ready when you are." One large button: _Press to begin_.
2. **Active** — a minimal agent waveform (`BarVisualizer`) and a subtle
   speaking indicator. **No transcript by default** — seeing one's own
   words changes the dynamic of the interview. A hidden hotspot in the
   bottom-right corner toggles a developer transcript view.
3. **Complete** — "Thank you. Your portrait is being made."

Black background, a single warm amber accent, large readable type, and
calm fade transitions with a slow breathing glow so the idle screen never
feels dead.

## How it connects to the Python agent

The kiosk and the Python agent worker meet in a **LiveKit room**:

```
visitor  ──audio──▶  browser (this kiosk)  ──WebRTC──▶  LiveKit room
                                                            ▲
                                          Python agent ─────┘
                                          (agent/main.py worker)
```

- When the start button is pressed, the kiosk calls its own token route
  (`/api/token`), which mints a LiveKit access token for a fresh,
  randomly-named room.
- `<LiveKitRoom>` connects the browser to that room over WebRTC; the
  visitor's microphone audio publishes automatically.
- The Python agent worker (started separately — see the repo root README
  and `agent/main.py`) is dispatched into the room and joins as the
  interviewer. Audio I/O is browser WebRTC throughout.
- When the agent ends the interview the room disconnects, and the kiosk
  advances to the Complete screen.

The kiosk needs the **same LiveKit project credentials** the Python agent
uses. STT/TTS are handled by LiveKit Inference on the agent side; the
kiosk only does audio transport and visualization.

## Setup

```sh
cd kiosk
npm install
cp .env.local.example .env.local   # then fill in the values
```

Set these in `.env.local` (documented in `.env.local.example`):

| Variable             | Purpose                                            |
| -------------------- | -------------------------------------------------- |
| `LIVEKIT_URL`        | `wss://` URL of your LiveKit project               |
| `LIVEKIT_API_KEY`    | LiveKit API key (server-side only)                 |
| `LIVEKIT_API_SECRET` | LiveKit API secret (server-side only)              |

The API key and secret are used **only** in the server-side token route;
they are never sent to the browser.

If LiveKit is not configured, the app still builds and the idle screen
still renders — pressing _begin_ simply shows a calm, non-fatal error
instead of starting an interview.

## Running

```sh
npm run dev      # development server at http://localhost:3000
npm run build    # production build
npm run start    # serve the production build
```

For the kiosk deployment, run `npm run build` then `npm run start` and
open the browser fullscreen (kiosk mode) on the kiosk computer.
