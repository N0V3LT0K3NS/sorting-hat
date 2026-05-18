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
- [ ] `kiosk/.env.local` has `NEXT_PUBLIC_DELIVERY_SERVER_URL` set to the
      kiosk machine's LAN IP (e.g. `http://192.168.1.42:8808`), not
      `localhost` — the Complete-screen QR must be scannable from a phone.
- [ ] The delivery server is running on the kiosk machine
      (`uv run python -m delivery.server` from the repo root).
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

### Optional Shape Rotator interface

If testing the experimental Shape Rotator skin, build or run with
`NEXT_PUBLIC_SHAPE_ROTATOR_INTERFACE=1`.

- [ ] With the flag **unset**, the default Idle screen still renders exactly
      as above.
- [ ] With the flag set, the Idle screen renders the Shape Rotator field and
      the _Enter the field_ CTA.
- [ ] Pressing the Shape CTA follows the same mic preflight, fullscreen,
      token-minting, and LiveKit join path as the default UI.
- [ ] During the interview, the Shape object responds subtly to local speech
      while listening and to agent speech while speaking.
- [ ] The Shape UI preserves the same live-phase End behavior: no End control
      during base questions, _End early_ during probing, and _I'm done_ once
      routing is complete.
- [ ] The Complete screen covers all delivery states: generating stage copy,
      done portrait + QR reveal, and graceful error/timeout closeout.

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
- [ ] The progress arc below the state label fills gently as the minutes
      pass, with an understated minute count beneath it — a calm sense of
      building, no countdown or pressure.
- [ ] The _End_ control is **state-aware** (it polls the delivery server's
      `/live/<session-id>`):
      - Early in the interview (before the base questions are done) there is
        **no** End button — the interview lacks the material for a portrait.
      - Once the base questions are done and the probe is running, a quiet
        _End early_ pill appears at the bottom centre — discoverable, low
        prominence, never trapping a visitor.
      - Once routing has settled it becomes a confident filled _I'm done_
        button.
      Pressing End in either visible state advances to the Complete screen
      and the kiosk resets to Idle, exactly as an agent-ended interview does.
- [ ] **Early End still produces a portrait.** Press _End early_ during the
      probe phase. The Complete screen still resolves to a portrait (a
      thinner one — lower-confidence classification, no deep probe) rather
      than timing out: the agent runs the offline pipeline on whatever
      transcript exists, on *any* close.
- [ ] (Dev only) Tap the hidden bottom-right hotspot — the developer
      transcript view toggles. Tap again to hide it. This is for field
      debugging only and is invisible to visitors.

## 6. End of session + reset

Run **each** ending and confirm the kiosk returns cleanly to Idle:

- [ ] **Agent ends the interview.** Let the interview run to completion.
      The room disconnects and the Complete screen appears — see section 7
      below for the portrait reveal it then runs. After the reveal the
      kiosk resets to Idle on its own.
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

## 7. Complete screen — the portrait reveal

The Complete screen polls the delivery server (`NEXT_PUBLIC_DELIVERY_SERVER_URL`)
for `/status/<session-id>` and reveals the portrait. Run with the delivery
server up and the agent worker producing real portraits.

- [ ] **Stage reveal.** Right after the interview ends, the screen reads
      "Making your portrait." with a stage line that advances —
      _Reading your interview… → Finding your shape… → Drawing your
      portrait… → Almost there…_ — and a row of step dots that fill as the
      pipeline progresses. It is calm and still, not a loud spinner.
- [ ] **Portrait + QR reveal.** When the pipeline finishes (~90 s–3 min),
      the finished portrait appears full-size with a QR code beside (or
      below) it and the line "Scan to keep it on your phone".
- [ ] **The QR works.** Scan it with a phone on the same wifi — it opens
      the portrait page served by the delivery server. (If it fails, check
      `NEXT_PUBLIC_DELIVERY_SERVER_URL` is the LAN IP, not `localhost`.)
- [ ] **Error / timeout fallback.** Stop the delivery server (or let a
      session error), end an interview, and confirm the Complete screen
      shows a calm closing message — _"Your portrait is on its way…"_ —
      and still resets to Idle. It never strands the visitor on a broken
      or frozen screen.

## 8. Back-to-back sessions — no state leak

- [ ] Run **three interviews in a row** without reloading the page.
- [ ] Each session gets a fresh room (the agent log shows a new room name
      each time — `interview-xxxxxx`).
- [ ] The microphone is released between sessions (the OS mic-in-use
      indicator clears on the Complete screen).
- [ ] The third session behaves identically to the first — no audio
      artefacts, no stale connection, no degraded latency.

## 9. Recovery

- [ ] Reloading the page at any time returns the kiosk to a clean Idle
      screen — the attendant's one manual reset path.

---

## Result

- Date / tester: ______________________
- Machine / mic / speaker: ______________________
- All boxes checked: ☐ yes ☐ no — if no, note the failure:

  ______________________________________________________________
