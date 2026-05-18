# Kiosk lifecycle smoke test

A manual checklist for verifying a sorting-hat kiosk install on real
hardware. Run it on the actual kiosk machine, with the real microphone and
speaker, before opening to the public.

The automated check is the production build — `cd kiosk && npm run build`
must pass with no errors. This document covers everything the build
**cannot** prove: audio hardware, the agent dispatch, and the session
lifecycle end to end.

---

## 0. Preconditions

- [ ] `kiosk/.env.local` has a valid `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
      `LIVEKIT_API_SECRET`.
- [ ] The external microphone is connected and set as the OS **default
      input device**.
- [ ] The external speaker is connected and at a sensible volume.
- [ ] `npm run build` completed with no errors.
- [ ] The Python agent worker is running from the repo root
      (`uv run python -m agent.main start`) and has registered with the
      LiveKit project (check its log for a "registered worker" line).

---

## 1. Idle screen

- [ ] `npm run start`, then open the kiosk URL fullscreen (kiosk mode).
- [ ] The Idle screen shows "Ready when you are." and the _Press to begin_
      button.
- [ ] The button has a slow breathing glow — the screen does not look dead.
- [ ] No scrollbars are visible.
- [ ] Text cannot be selected by click-drag.
- [ ] Right-click (or long-press) does **not** open a context menu.
- [ ] Ctrl/Cmd + `+` / `-` / `0` does **not** zoom the page.

## 2. Audio preflight — the failure paths

Test these **before** the happy path, so a calm failure is proven first.

- [ ] **Permission denied.** In the browser, block microphone access for
      the kiosk origin, then press _begin_. The Idle screen shows a calm
      "microphone access is blocked" message — **not** a broken Active
      screen. The app does not crash.
- [ ] **No device.** Disconnect/disable the microphone, press _begin_. The
      Idle screen shows a calm "no microphone was found" message.
- [ ] Re-enable the microphone and grant permission again for the happy
      path below.

## 3. Start → room → agent dispatch (happy path)

- [ ] Press _begin_. The browser asks for (or already has) microphone
      permission, then the screen transitions to Active.
- [ ] The Active screen shows the waveform visualizer and a state label
      ("Connecting…" → "Just a moment…" → "Listening").
- [ ] Within a few seconds the **agent speaks its opening line** through
      the speaker — confirming the Python worker was dispatched into the
      room and joined.
- [ ] In the agent worker's log, a new room/job appears for this session.

## 4. Noise cancellation

- [ ] With the interview live, open dev tools and inspect the DOM for the
      hidden `<span data-noise-cancellation="...">` node. Its value should
      reach `on` shortly after the session starts (it may pass through
      `pending`). On an unsupported browser it stays `off` and the agent
      log shows a Krisp "not supported" warning — the session still works.
- [ ] Speak with background noise present (music, chatter). The agent's
      turn detection should still respond cleanly to your speech.

## 5. The interview

- [ ] Hold a short back-and-forth with the agent. Latency from end-of-your-
      speech to start-of-agent-speech feels conversational.
- [ ] The waveform reacts to the agent's voice; the speaking dot pulses.
- [ ] (Dev only) Tap the hidden bottom-right hotspot — the developer
      transcript view toggles. Tap again to hide it. This is for field
      debugging only and is invisible to visitors.

## 6. End of session + reset

Run **each** ending and confirm the kiosk returns cleanly to Idle:

- [ ] **Agent ends the interview.** Let the interview run to completion.
      The room disconnects, the Complete screen shows "Thank you. Your
      portrait is being made.", and after a short pause the kiosk resets
      to Idle on its own.
- [ ] **Visitor walks away.** Mid-interview, close/refuse the connection
      (e.g. disable the network briefly, or have the agent worker stop).
      The kiosk advances to Complete and then resets — no frozen screen.
- [ ] **Agent never joins.** Stop the Python worker, then press _begin_.
      After ~30 seconds the agent-join watchdog ends the session and the
      kiosk returns to Idle — it does not hang on "Connecting…".

> **Regression watch:** pressing _begin_ must show the Active screen and
> attempt to connect — it must **not** jump straight to Complete. The
> initial/Connecting connection state is not a disconnect; only a drop
> *after* the room has connected ends the session.

## 7. Back-to-back sessions — no state leak

- [ ] Run **three interviews in a row** without reloading the page.
- [ ] Each session gets a fresh room (the agent log shows a new room name
      each time — `interview-xxxxxx`).
- [ ] The microphone is released between sessions (the OS mic-in-use
      indicator clears on the Complete screen).
- [ ] The third session behaves identically to the first — no audio
      artefacts, no stale connection, no degraded latency.

## 8. Recovery

- [ ] Reloading the page at any time returns the kiosk to a clean Idle
      screen — the attendant's one manual reset path.

---

## Result

- Date / tester: ______________________
- Machine / mic / speaker: ______________________
- All boxes checked: ☐ yes ☐ no — if no, note the failure:

  ______________________________________________________________
