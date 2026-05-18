# sorting-hat

**A voice-AI kiosk that interviews you and hands you back the shape of yourself, drawn in a meme.**

A person walks into a room, sits at a computer, hits start, and has a warm
10–15 minute voice conversation with an AI interviewer. Afterward, an offline
pipeline reads the transcript, classifies the person into one of four meme
templates, fills that template's slots with content drawn from the interview,
and renders a portrait image. Voice in, image out.

This is a standalone installation product. It is not coupled to any other
system. It explains itself in one breath and ships on its own terms.

---

## The four locked constraints

These are design law. No goal, no agent, no PR may violate them.

1. **Four templates, locked.** Iceberg (depth), 2x2 Compass (position),
   Anakin/Padmé Arc (trajectory), Two Buttons (tension). They are orthogonal
   by construction. No fifth template. No sub-categories. No tier list.

2. **Classification and filling are separate stages.** Two cognitively
   distinct LLM jobs, two prompts, two calls. They are never merged.

3. **The sort is blind.** The interviewee never hears the words "iceberg" or
   "compass" during the interview. The classification is invisible; the
   reveal is the payoff.

4. **The interview is voice-only from the user's perspective.** No on-screen
   survey, no text fallback.

The tiebreaker for every judgment call: **which approach produces a more
truthful portrait?**

---

## Architecture

- **Live interview** — LiveKit Agents (Python). One `AgentSession` per
  interview; an `InterviewerAgent` supervisor owning the base questions;
  four `AgentTask` probes returning typed results; typed `userdata` for
  shared state; a background classifier via the observer pattern.
- **Offline analysis** — four decoupled, independently testable functions:
  `classify` -> `fill` -> `render` -> `deliver`. Not in LiveKit.
- **Delivery server** — a small local web server (`delivery/`) that serves
  the finished portrait, exposes interview progress so the kiosk can show a
  reveal, and hosts the QR target for the visitor's phone.
- **Kiosk** — a minimal Next.js frontend: Idle / Active / Complete, plus a
  `/dev` dashboard listing every interview the machine has run.

All LLM calls route through OpenRouter; STT/TTS through LiveKit Inference.
Renders go through OpenAI `gpt-image-2`.

See [`docs/GOALS.md`](docs/GOALS.md) for the full build plan.

---

## Development

```sh
uv sync                      # install dependencies
cp .env.example .env         # fill in API keys (see comments in the file)
uv run pytest                # run the test suite
```

Missing API keys disable the dependent feature; they never block the build.

---

## Running the kiosk

The kiosk is **three processes** on one machine. Start them in this order.

**1. Wire the delivery-server URL to the machine's LAN IP.** The kiosk
browser and the visitor's phone both reach the delivery server over the
local network, so it must be the LAN IP — not `localhost`. Find the IP
(`ipconfig getifaddr en0` on macOS), then set it in both env files:

```sh
# sorting-hat/.env
DELIVERY_SERVER_URL=http://<LAN-IP>:8808
# sorting-hat/kiosk/.env.local
NEXT_PUBLIC_DELIVERY_SERVER_URL=http://<LAN-IP>:8808
```

**2. Start the three processes** (separate terminals):

```sh
# delivery server — serves portraits + interview-progress status
uv run python -m delivery.server

# agent worker — the LiveKit voice interviewer
uv run python -m agent.main start

# kiosk frontend
cd kiosk && npm install && npm run dev
```

For a real kiosk run the agent worker with **`start`**, the production worker —
not `dev`. The `dev` subcommand is for development: it adds file-watching and
hot-reload, which restart the worker on any source change, so it is not a
durable long-running process. If a `dev` worker is restarted (or simply stops)
while an interview's offline pipeline is still running, that pipeline is
orphaned mid-stage. Use `dev` while developing; use `start` for anything a
visitor will actually use.

**3. Open the kiosk** at `http://localhost:3000`. Press begin, allow the
microphone, and have the interview. When it ends, the screen shows a
stage-by-stage reveal (~1–3 min while the portrait renders), then the
portrait with a QR code to take it to a phone.

**Operator view:** `http://localhost:3000/dev` — a dashboard of every
interview the machine has run: live signal state for an active interview,
and the full record (transcript, classification, portrait) for past ones.

**Where interviews are stored:** `sessions/<session-id>/` — local to this
machine, gitignored. Nothing is uploaded.

---

## Layout

```
agent/                 LiveKit interview agent (interviewer + probe tasks)
pipeline/              Offline analysis: classify, fill, render, deliver
delivery/              Local web server: serves portraits + progress status
prompts/               Persona prompt, probe prompts, analysis prompts
kiosk/                 Next.js kiosk frontend (interview flow + /dev dashboard)
assets/templates/      Meme base images (reference/ holds the real templates)
tests/                 Unit tests + the scripted-persona test harness
docs/                  GOALS.md ledger, borrowed-craft notes
sessions/              Per-session output — transcripts, portraits (gitignored)
```
