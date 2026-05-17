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
- **Kiosk** — a minimal Next.js frontend: Idle / Active / Complete.

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

## Layout

```
agent/                 LiveKit interview agent (interviewer + probe tasks)
pipeline/              Offline analysis: classify, fill, render, deliver
prompts/               Persona prompt, probe prompts, analysis prompts
kiosk/                 Next.js kiosk frontend
assets/templates/      The four base meme images
tests/                 Unit tests + the scripted-persona test harness
docs/                  GOALS.md ledger, borrowed-craft notes
sessions/              Per-session output (gitignored)
```
