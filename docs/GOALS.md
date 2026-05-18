# GOALS — the orchestration ledger

The single source of truth for the sorting-hat build. Every goal is one
PR-sized unit of work, dispatched to a CLI agent (`/goal`) and verified
against a transcript-provable completion condition before merge.

**Status legend:** `PENDING` · `DISPATCHED` · `IN-REVIEW` · `VERIFIED` · `BLOCKED`

**Executor key:** `CC` = Claude Code `/goal` headless · `CX` = Codex `/goal`
supervised · `ORCH` = orchestrator does it directly · `HERMES` = adversarial
review pass over SSH.

**Verification rule:** a goal is `VERIFIED` only after the orchestrator
independently re-runs the proving command and reads the diff against the
four locked constraints. The agent's self-report is never sufficient.

Every dispatched goal's completion condition ends with:
`... OR stop after N turns and report blockers.`

---

## Phase 0 — Foundation

### G0 · Repo scaffold + decisions doc — `VERIFIED` · ORCH
- **Outcome:** repo created, wired to GitHub remote, `uv` project skeleton,
  directory layout, `.env.example`, README with the four locks, this ledger.
- **Proving command:** `uv sync` succeeds; `git remote -v` shows origin.
- **Artifacts:** the repo itself.

---

## Phase 1 — Data foundation

### G1 · `InterviewState` + typed result schemas — `VERIFIED` · CC
- **Context:** Pure data layer. The brief's `InterviewState` dataclass plus
  the four typed probe results. No LiveKit, no I/O. Everything downstream
  depends on these types being correct and stable.
- **Outcome:** `agent/state.py` defines `InterviewState` (signal weights,
  progress counters, chosen_template, the four optional results, transcript
  log) and `IcebergResult` / `TwoButtonsResult` / `CompassResult` /
  `ArcResult` with per-field char limits matching the brief.
- **In-scope:** `agent/state.py`, `tests/test_state.py`.
- **Must not change:** nothing else exists yet.
- **Proving command:** `uv run pytest tests/test_state.py -v`
- **Completion:** proving command exits 0, output shown.

---

## Phase 2 — The interview

### G2 · Single LiveKit voice agent (walking skeleton) — `VERIFIED` · CC
- **Context:** The brief's step 1. One agent that simply talks, on the
  current LiveKit stack. No interview logic. Proves low-latency voice works.
- **Outcome:** `agent/main.py` runs an `AgentSession` + one `Agent`.
  STT/TTS via LiveKit Inference (Deepgram Flux STT, Cartesia Sonic-3 TTS);
  LLM via OpenRouter (OpenAI-compatible — the LiveKit OpenAI plugin pointed
  at `base_url=https://openrouter.ai/api/v1`). Native turn detector +
  interruption classifier, preemptive generation on. Measure and log
  end-of-user-speech -> start-of-agent-speech latency.
- **In-scope:** `agent/main.py`, `agent/config.py`.
- **Proving command:** `uv run python -m agent.main --dry-run` (validates
  session wiring without a live room; exits 0).
- **Completion:** proving command exits 0; config loads; missing keys
  degrade gracefully with a logged warning. Smoke-test confirms the chosen
  interviewer model token-streams through the OpenRouter route (streaming +
  custom base_url is the one fragile spot).

### G3 · `InterviewerAgent` persona + base 5 questions — `VERIFIED` · CC + HERMES
- **Context:** The brief's step 2. The persona prompt absorbs `prompter`'s
  craft (see `docs/borrowed-craft.md`): DRILL/ZOOM, banned phrasings, output
  discipline, DRIFT CHECK. Asks the 5 base questions in order, handles
  follow-ups, wraps up. HERMES reviews the prompt adversarially.
- **Outcome:** `agent/interviewer.py` + `prompts/persona.md` + the 5 base
  questions; a scripted-replay test proving question order and no banned
  phrasings.
- **In-scope:** `agent/interviewer.py`, `prompts/persona.md`,
  `tests/test_interviewer.py`.
- **Proving command:** `uv run pytest tests/test_interviewer.py -v`
- **Completion:** proving command exits 0; HERMES review notes addressed.

### G3.1 · Persona craft fix — add the CROSS move — `VERIFIED` · CC
- **Context:** The G3 adversarial review found the DRILL IN / ZOOM OUT
  binary is not exhaustive. It omits the **CROSS / lateral move** (pit one
  thing against another, ask the negative case, ask the source) — and
  CROSS is exactly how the tension and position shapes get elicited. The
  DRIFT CHECK currently tells the agent to treat a non-drill/non-zoom
  follow-up as noise, which structurally suppresses half the template
  space. Must be fixed before the G4–G7 probe swarm inherits the flaw.
- **Outcome:** `prompts/persona.md` and `docs/borrowed-craft.md` updated:
  add CROSS as a third follow-up move with worked examples; fix the DRIFT
  CHECK so a clean CROSS is not flagged as noise; remove the "usually
  drill in before zoom out" default (the prompt already half-contradicts
  it). `tests/test_interviewer.py` gains a test asserting CROSS is present.
- **In-scope:** `prompts/persona.md`, `docs/borrowed-craft.md`,
  `tests/test_interviewer.py`.
- **Proving command:** `uv run pytest tests/test_interviewer.py -v`

### G4 · Iceberg probe `AgentTask` — `VERIFIED` · CC + HERMES
### G5 · Two-Buttons probe `AgentTask` — `VERIFIED` · CC + HERMES
### G6 · Compass probe `AgentTask` — `VERIFIED` · CC + HERMES
### G7 · Arc probe `AgentTask` — `VERIFIED` · CC + HERMES
- **Context:** The brief's step 3. Four parallel goals (dispatched as a
  swarm once G3 verifies). Each is a focused sub-interview returning the
  matching typed result; deepens into its template; pivots if results
  come back thin/evasive.
- **Outcome (each):** `agent/probes/<template>.py` + `prompts/probe_<t>.md`
  + isolation test.
- **In-scope (each):** that probe file, that prompt, that test only.
- **Proving command (each):** `uv run pytest tests/test_probe_<t>.py -v`
- **Completion:** proving command exits 0; produces a fully-populated typed
  result from a scripted willing user.

### G8 · Supervisor routing — `VERIFIED` · CC
- **Context:** The brief's step 4. Wires `InterviewerAgent` to read signal
  weights, invoke the winning probe, pivot on thin results, close gracefully
  when `chosen_template` + result are set.
- **In-scope:** `agent/interviewer.py`, `agent/main.py`,
  `tests/test_routing.py`.
- **Proving command:** `uv run pytest tests/test_routing.py -v`
- **Completion:** full scripted interview lands a template and closes clean.

---

## Phase 3 — Offline analysis (runs parallel to Phase 2; needs only G1)

### G9 · `classify(transcript_xml) -> label` — `VERIFIED` · CX
- **Outcome:** `pipeline/classify.py` + `prompts/classify.md`. Authoritative
  classification: template + confidence + reasoning. Transcript passed as
  escaped XML. Importable and standalone. LLM call via OpenRouter (the
  OpenAI SDK pointed at the OpenRouter base URL).
- **In-scope:** `pipeline/classify.py`, `prompts/classify.md`,
  `tests/test_classify.py`, `tests/fixtures/`.
- **Proving command:** `uv run pytest tests/test_classify.py -v`

### G10 · `fill(label, transcript_xml, probe_result) -> typed_result` — `VERIFIED` · CX
- **Outcome:** `pipeline/fill.py` + `prompts/fill.md`. Structured-output
  slot-filling, char limits enforced per template. LLM call via OpenRouter
  (the OpenAI SDK pointed at the OpenRouter base URL).
- **In-scope:** `pipeline/fill.py`, `prompts/fill.md`, `tests/test_fill.py`.
- **Proving command:** `uv run pytest tests/test_fill.py -v`

### G11 · `render(typed_result) -> png` — `VERIFIED` · CC
- **Outcome:** `pipeline/render.py` + the four base images in
  `assets/templates/`. Pillow compositing, per-template layout.
- **In-scope:** `pipeline/render.py`, `assets/templates/*`,
  `tests/test_render.py`.
- **Proving command:** `uv run pytest tests/test_render.py -v` (asserts a
  non-empty PNG is produced for each of the four templates).

### G12 · `deliver(png, session)` — `VERIFIED` · CC
- **Outcome:** `pipeline/deliver.py`. QR code + per-session folder write;
  email behind a flag (degrades if no SendGrid key).
- **In-scope:** `pipeline/deliver.py`, `tests/test_deliver.py`.
- **Proving command:** `uv run pytest tests/test_deliver.py -v`

### G13 · Test harness — scripted personas — `VERIFIED` · CX + HERMES
- **Context:** The brief says this matters more than unit tests. Scripted
  transcripts for the 4 clean templates PLUS deliberate hybrid personas.
  Asserts `classify` lands; defines explicit behavior for low-confidence
  sorts. HERMES stress-tests the rubric.
- **In-scope:** `tests/harness/`, `tests/test_harness.py`.
- **Proving command:** `uv run pytest tests/test_harness.py -v`
- **Completion:** 4 clean personas classify correctly; hybrid behavior is
  explicitly asserted, not accidental.

---

## Phase 4 — The clever part, last

### G14 · Background classifier (observer pattern) — `VERIFIED` · CC
- **Context:** The brief's step 6. `asyncio.create_task` after each user
  turn; a fast small model via OpenRouter (Llama/Qwen-class, routed to a
  fast provider); off the critical path; timeout-guarded so failure never
  stalls the interview; writes signal weights to `userdata`.
- **In-scope:** `agent/classifier.py`, `agent/interviewer.py`,
  `tests/test_classifier.py`.
- **Proving command:** `uv run pytest tests/test_classifier.py -v`
- **Completion:** signal weights update mid-interview; a forced classifier
  timeout does not stall the conversation (asserted).

---

## Phase 5 — Kiosk + delivery

### G15 · Next.js kiosk frontend — `VERIFIED` · CC
- **Outcome:** `kiosk/` — three screens (Idle / Active / Complete), black
  background, single accent, `BarVisualizer`, fullscreen.
- **Proving command:** `cd kiosk && npm run build` succeeds.

### G16 · Kiosk hardware integration — `VERIFIED` · CC
- **Outcome:** audio I/O on real hardware, LiveKit noise cancellation,
  start-button -> room -> agent dispatch lifecycle.
- **Proving command:** `cd kiosk && npm run build`; lifecycle smoke test
  documented.

### G17 · Live test pass with 10 humans — `PENDING` · ORCH + HERMES
- **Context:** The brief's step 9. The tuning pass. Run 10 real sessions,
  tune the two prompts against real transcripts. HERMES runs adversarial
  review of the resulting transcripts and portraits.
- **Completion:** 10 sessions complete kiosk-to-PNG; portraits judged
  uncomfortably accurate, not horoscope-generic.
- **Tuning backlog (from the G3 adversarial review — fix against live
  transcripts, not before):**
  1. Define "something real" structurally — a thread is done when its
     answer is structurally legible, not merely emotionally vivid.
  2. Add a "difficult registers" section: one concrete tactic each for
     the evasive / one-word / rambling / performative / emotional
     interviewee. The persona currently only offers a vibe.
  3. The closing is a subtle blind-sort leak — five probing questions then
     a pointed no-reflection exit feels extractive. Give one genuine
     non-diagnostic human reaction back at the close.
  4. Add 2–3 prepared deflections for pointed meta-questions ("are you an
     AI?", "is this a personality test?").
  5. Add a hard per-thread turn cap (~4) so a thread that never lands
     cannot loop forever.

---

## Post-build work

- **G16.1 — model selection applied to config:** interviewer moved from
  Haiku to Sonnet 4.6; related model choices were applied across the runtime
  config for the post-build live-test path.
- **Live-test fix series:** PR #25 fixed kiosk premature completion; PR #26
  switched STT to direct Deepgram; PR #28 added session-close handling,
  transcript persistence, and the offline-pipeline trigger; PR #29 wired the
  classifier-driven supervisor path; PR #34 fixed render glyph and overlap
  regressions.
- **Render rebuild:** `feat/genai-render` rebuilt the render stage on
  `gpt-image-2` and landed in PR #35.
- **Issue status:** #30 and #31 were render regressions fixed by PR #34; #32
  was superseded by the `feat/genai-render` rebuild; #36 remains the
  replay-harness fixture follow-up.

---

## Dependency graph

```
G0 ──> G1 ──┬──> G2 ──> G3 ──┬──> G4 ┐
            │                ├──> G5 ┤
            │                ├──> G6 ┼──> G8 ──┬──> G14
            │                └──> G7 ┘         │
            │                                  ├──> G15 ──> G16 ──> G17
            └──> G9 ──> G10 ──> G13 ───────────┘
                  │
                  └──> G11 ──> G12 ──────────────────────> G17
```

- **Wave A (sequential):** G1 -> G2 -> G3
- **Wave B (parallel, triggered by G3):** G4, G5, G6, G7 as a swarm;
  G9 -> G10 -> G11 -> G12 run concurrently (need only G1).
- **Wave C:** G8 (after G3–G7); G13 (after G9).
- **Wave D:** G14 (after G8); G15 -> G16 (after G8 + G12).
- **Wave E:** G17 (after G16 + G12).
