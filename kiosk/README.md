# sorting-hat kiosk

The fullscreen frontend for the sorting-hat voice interview. A person walks
up, sees an idle screen, presses one button, has a ~10–15 minute voice
conversation with the AI interviewer, and sees a calm completion screen.

This is a minimal Next.js app (App Router, TypeScript). It is **voice-only
from the visitor's perspective** — there is no on-screen survey and no text
fallback. That is one of the project's four locked constraints.

It is built to run **fullscreen on a dedicated single-station machine**: one
computer, one good microphone, one speaker, browser launched in kiosk mode.
The kiosk runs many interviews back to back unattended.

## The three screens

A single state machine: `idle → active → complete → (auto-reset) → idle`.

1. **Idle** — "Ready when you are." One large button: _Press to begin_.
   If the microphone is blocked or missing, a calm message appears here
   (see _Audio robustness_ below) — never a broken Active screen.
2. **Active** — a minimal agent waveform (`BarVisualizer`) and a subtle
   speaking indicator. **No transcript by default** — seeing one's own
   words changes the dynamic of the interview. A hidden hotspot in the
   bottom-right corner toggles a developer transcript view.
3. **Complete** — the post-interview reveal. While the offline portrait
   pipeline runs (~90 s–3 min) the screen shows a calm **stage-by-stage
   reveal** — _Reading your interview… → Finding your shape… → Drawing your
   portrait… → Almost there…_ — polling the local delivery server for
   progress. When the portrait is ready it is shown **full-size on the
   screen** with a **QR code** the visitor scans to keep the portrait on
   their phone. A pipeline error, an unreachable delivery server, or a
   timeout all degrade to a calm closing message — never a broken screen.
   After the reveal the kiosk resets itself to Idle for the next visitor.

Black background, a single warm amber accent, large readable type, and
calm fade transitions with a slow breathing glow so the idle screen never
feels dead.

## Developer dashboard (`/dev`)

A separate diagnostic route at `/dev`, away from the visitor kiosk flow at
`/`. It is a full **local session dashboard** over every interview this
machine has run — a two-level view, both fed by the local delivery server
(`NEXT_PUBLIC_DELIVERY_SERVER_URL`, the same server the Complete screen
uses).

**Index (`/dev`)** — a live list of every interview, most-recent first. Each
row shows the session id, a relative timestamp, a status badge (`active`
while the interview runs, `rendering` mid-pipeline, `complete`, `error`, or
`idle`), the turn count, the chosen template, and a portrait thumbnail once
one exists. It polls `GET /sessions` every ~4 s, so a newly-started
interview appears on its own. A calm empty state shows when there are no
sessions yet.

**Detail (`/dev?session=<session-id>`)** — one session in full. The selected
session lives in the `?session=` query param, so a detail view is linkable
and survives a refresh; a back link returns to the index. What the detail
view shows depends on where the session is:

- **Active** (interview running) — the **live classifier view**: the four
  signal weights (`iceberg`, `two_buttons`, `compass`, `arc`) as moving
  bars with the leading signal in the accent color, base-question progress,
  the phase, leading/chosen template, `routing_done`, and the turn count.
  Polls `GET /live/<session-id>` every ~2 s.
- **Mid-pipeline** (interview done, portrait generating) — the offline
  pipeline **stage row**, polling `GET /status/<session-id>`; the result
  panel below reveals itself once the pipeline is `done`.
- **Complete** (or as far as it got) — the **full local record**: the
  portrait image, the classification (template, confidence, reasoning), the
  filled result, and the **full transcript** rendered turn by turn. A
  developer or operator can review exactly what the machine captured.

Every fetch degrades gracefully — a pending or unreachable delivery server,
or a partial session (a transcript but no portrait), shows what exists and
keeps a calm line. This route never affects the kiosk flow at `/`.

## Session lifecycle

Pressing _begin_ runs an explicit, bounded lifecycle. Each step is designed
so a kiosk can run hundreds of sessions without an attendant and without
state leaking from one visitor to the next.

```
  ┌─ idle ─────────────────────────────────────────────────────────┐
  │  visitor presses "Press to begin"                               │
  └──────────────┬──────────────────────────────────────────────────┘
                 │ 1. AUDIO PREFLIGHT
                 │    Probe the microphone with getUserMedia. If access
                 │    is denied or no device is present, show a calm
                 │    message on the Idle screen and stop here.
                 │ 2. FULLSCREEN
                 │    Best-effort requestFullscreen() inside the click
                 │    gesture (the OS kiosk flag is the real guarantee).
                 │ 3. MINT TOKEN
                 │    GET /api/token → a LiveKit access token for a
                 │    fresh, randomly-named room (interview-xxxxxx).
                 ▼
  ┌─ active ────────────────────────────────────────────────────────┐
  │  4. JOIN ROOM                                                    │
  │     <LiveKitRoom> connects the browser to the room over WebRTC;  │
  │     the visitor's microphone publishes automatically.            │
  │  5. AGENT DISPATCH                                               │
  │     The Python agent worker is dispatched into that room and     │
  │     joins as the interviewer. An on-screen watchdog ends the     │
  │     session calmly if the worker never appears (~30 s).          │
  │  6. NOISE CANCELLATION                                           │
  │     Krisp enhanced cancellation is enabled on the mic track;     │
  │     WebRTC echo/noise suppression runs underneath as a baseline. │
  │  7. INTERVIEW                                                    │
  │     ~10–15 min of voice conversation.                            │
  └──────────────┬──────────────────────────────────────────────────┘
                 │ 8. END
                 │    The agent ends the interview and the room
                 │    disconnects — OR the visitor walks away and the
                 │    connection drops — OR a connection error occurs.
                 │    All three end the session gracefully.
                 ▼
  ┌─ complete ──────────────────────────────────────────────────────┐
  │  9. CLEAN UP                                                     │
  │     The <LiveKitRoom> unmounts: livekit-client tears down the    │
  │     WebRTC peer connection and releases the microphone.          │
  │ 10. REVEAL                                                       │
  │     The Complete screen polls the local delivery server for      │
  │     portrait-pipeline progress, shows a stage-by-stage reveal,   │
  │     then the finished portrait + a QR code to the phone.         │
  │ 11. RESET                                                        │
  │     After the reveal the kiosk returns to Idle, fully reset.     │
  └─────────────────────────────────────────────────────────────────┘
```

**No state leaks between sessions.** Every session gets a fresh token, a
fresh room name, and a fresh `<LiveKitRoom>` instance — the component is
remounted via a changing React `key` on every reset, so no connection,
microphone handle, or processor survives into the next visitor's session.

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
- The Python agent worker (started separately — see _Running the agent
  worker_ below) is dispatched into the room and joins as the interviewer.
  Audio I/O is browser WebRTC throughout.
- When the agent ends the interview the room disconnects, and the kiosk
  advances to the Complete screen.

The kiosk needs the **same LiveKit project credentials** the Python agent
uses. STT/TTS are handled by LiveKit Inference on the agent side; the
kiosk only does audio transport, noise cancellation, and visualization.

## Noise cancellation

The kiosk lives in a public space — background chatter, footfall, HVAC,
music. Rough mic audio degrades the agent's turn detection and
transcription quality, so noise is cancelled **at the source** in the
browser, before audio is published:

- **Enhanced cancellation (Krisp).** The `<NoiseCancellation>` component
  uses `useKrispNoiseFilter` from `@livekit/components-react/krisp` to
  apply LiveKit's enhanced noise filter to the microphone track. This is a
  LiveKit Cloud feature and requires the `@livekit/krisp-noise-filter`
  package (already a dependency).
- **WebRTC suppression (baseline).** The `<LiveKitRoom>` audio capture
  options also enable the browser's own `echoCancellation`,
  `noiseSuppression`, and `autoGainControl`.

If a browser cannot run the Krisp filter (unsupported / older Safari) the
hook logs a warning and the session continues on the WebRTC baseline —
never a hard failure.

## Audio robustness

A kiosk must degrade calmly when audio hardware misbehaves:

- **Before joining**, `checkAudioInput()` probes the microphone with
  `getUserMedia`. A denied permission, a missing device, a mic held by
  another app, or an unsupported browser each produces a short, specific
  message on the **Idle** screen. The visitor never reaches a broken
  Active screen.
- **During a session**, a disconnect, a connection error, or a media
  device failure all route to the same graceful Complete → reset path.
- **If the agent worker never joins** the room, an on-screen watchdog ends
  the session after ~30 seconds rather than stranding the visitor on a
  frozen "Connecting…" screen.

## Setup

```sh
cd kiosk
npm install
cp .env.local.example .env.local   # then fill in the values
```

Set these in `.env.local` (documented in `.env.local.example`):

| Variable                          | Purpose                                            |
| ---------------------------------- | -------------------------------------------------- |
| `LIVEKIT_URL`                      | `wss://` URL of your LiveKit project               |
| `LIVEKIT_API_KEY`                  | LiveKit API key (server-side only)                 |
| `LIVEKIT_API_SECRET`               | LiveKit API secret (server-side only)              |
| `NEXT_PUBLIC_DELIVERY_SERVER_URL`  | Base URL of the local delivery server              |

The API key and secret are used **only** in the server-side token route;
they are never sent to the browser.

`NEXT_PUBLIC_DELIVERY_SERVER_URL` points the Complete screen at the local
delivery server (`delivery/server.py`) — it polls `/status/<session-id>`
for portrait-pipeline progress and loads the finished portrait and QR
image from it. Because the QR code sends a **phone** to this same server,
the value **must be the kiosk machine's LAN IP** (e.g.
`http://192.168.1.42:8808`), not `localhost` — a phone cannot resolve the
kiosk's `localhost`. The `localhost` default is only correct when testing
the kiosk page on the kiosk machine itself.

If LiveKit is not configured, the app still builds and the idle screen
still renders — pressing _begin_ simply shows a calm, non-fatal error
instead of starting an interview.

## Running

```sh
npm run dev      # development server at http://localhost:3000
npm run build    # production build
npm run start    # serve the production build
```

## Hardware setup — a single-station install

The kiosk is one computer with one microphone and one speaker, running
this app fullscreen, plus the Python agent worker.

### 1. The machine

- A dedicated computer — nothing else running on it during sessions.
- Wired Ethernet if possible (Wi-Fi works, but the room connection is
  more stable on a wire in a crowded venue).
- The display at a comfortable standing height.

### 2. Microphone placement

- Use a **good external microphone** — a USB cardioid or a boundary mic.
  The built-in laptop mic is the single biggest quality drop.
- Place it **~12–18 inches (30–45 cm)** from where the visitor's face
  will be — close enough for a strong signal, far enough to avoid plosives
  and breath noise.
- Point it at the visitor and away from the speaker, to reduce the speaker
  audio bleeding back into the mic.
- Set it as the **system default input device** before launching the
  browser. The kiosk uses whatever the OS default input is.

### 3. Speaker

- An external speaker, positioned so the agent's voice is clearly audible
  but **not** aimed straight back into the microphone.
- Set a comfortable volume during the smoke test (see `SMOKE_TEST.md`) —
  loud enough to hear over venue noise, not so loud it causes echo.

### 4. Running the frontend fullscreen

Build and serve the production app, then launch the browser in kiosk mode
so there is no address bar, no tabs, and no way to navigate away:

```sh
cd kiosk
npm run build
npm run start            # serves http://localhost:3000
```

Launch a browser in kiosk mode pointed at the app, e.g. Chrome/Chromium:

```sh
chromium --kiosk --app=http://localhost:3000 \
  --autoplay-policy=no-user-gesture-required \
  --disable-pinch --overscroll-history-navigation=0
```

The app also hardens itself from inside the page: no scrollbars, no text
selection, no right-click menu, no pinch/keyboard zoom, no drag-and-drop,
and a best-effort `requestFullscreen()` on the first button press. The
OS-level kiosk flag remains the real fullscreen guarantee; the in-page
guards cover the gaps.

### 5. Running the Python agent worker

The interviewer is a separate Python process. From the **repo root** (not
`kiosk/`), with the repo's `.env` configured (see the root `README.md`):

```sh
uv run python -m agent.main start
```

This starts a LiveKit Agents worker that registers with the same LiveKit
project. When the kiosk creates a room and a visitor joins, the worker is
dispatched into that room and runs one interview. It handles many rooms
over its lifetime — leave it running for the duration of the install.

The kiosk frontend and the agent worker are **independent processes** that
only ever meet inside a LiveKit room. They can run on the same machine or
on different machines, as long as both use the same LiveKit credentials.

### 6. How a session resets for the next person

When an interview ends — the agent finishes, or the visitor leaves and the
connection drops — the kiosk tears down the LiveKit room (releasing the
microphone) and shows the Complete screen. That screen runs the portrait
reveal (stage progress, then portrait + QR); once the reveal has landed it
holds briefly so a visitor can scan the QR, then returns to Idle on its
own. No attendant action is needed between visitors. If an attendant ever
needs to force a reset, reloading the page returns the kiosk to a clean
Idle state.

## Smoke test

Real hardware and live LiveKit credentials may not be present in every
environment. A documented manual lifecycle smoke-test checklist lives in
[`SMOKE_TEST.md`](./SMOKE_TEST.md) — run it on the actual kiosk machine
before an install. The production build (`npm run build`) is the automated
check and must always pass.

## Security notes

### postcss advisory GHSA-qx2v-qp2m-jg93 — resolved 2026-05-17

`npm audit` previously reported 2 moderate-severity vulnerabilities: Next.js
pins `postcss` at `8.4.31`, which is below the `8.5.10` release that fixes a
moderate-severity XSS via unescaped `</style>` in CSS stringify output
(advisory [GHSA-qx2v-qp2m-jg93](https://github.com/advisories/GHSA-qx2v-qp2m-jg93)).
No Next.js release on any line — 15.x or 16.x — ships a patched `postcss`,
so `npm audit fix` only offered a breaking downgrade of Next.js itself.

Resolved without a breaking change via a `package.json` `overrides` entry
forcing `postcss` to `^8.5.10`. `postcss` 8.5.x is the same major version as
the pinned 8.4.31 and is semver-compatible, so the build is unaffected.
`npm audit --omit=dev` now reports 0 vulnerabilities. Remove the override
once Next.js bumps its bundled `postcss` past 8.5.10.
