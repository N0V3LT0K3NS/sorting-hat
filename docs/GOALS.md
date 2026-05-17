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

### G1 · `InterviewState` + typed result schemas — `PENDING` · CC
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

### G2 · Single LiveKit voice agent (walking skeleton) — `PENDING` · CC
- **Context:** The brief's step 1. One agent that simply talks, on the
  current LiveKit stack. No interview logic. Proves low-latency voice works.
- **Outcome:** `agent/main.py` runs an `AgentSession` + one `Agent` using
  LiveKit Inference (Deepgram Flux STT, Cartesia Sonic-3 TTS), native turn
  detector + interruption classifier, preemptive generation on.
- **In-scope:** `agent/main.py`, `agent/config.py`.
- **Proving command:** `uv run python -m agent.main --dry-run` (validates
  session wiring without a live room; exits 0).
- **Completion:** proving command exits 0; config loads; missing keys
  degrade gracefully with a logged warning.

### G3 · `InterviewerAgent` persona + base 5 questions — `PENDING` · CC + HERMES
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

### G4 · Iceberg probe `AgentTask` — `PENDING` · CC + HERMES
### G5 · Two-Buttons probe `AgentTask` — `PENDING` · CC + HERMES
### G6 · Compass probe `AgentTask` — `PENDING` · CC + HERMES
### G7 · Arc probe `AgentTask` — `PENDING` · CC + HERMES
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

### G8 · Supervisor routing — `PENDING` · CC
- **Context:** The brief's step 4. Wires `InterviewerAgent` to read signal
  weights, invoke the winning probe, pivot on thin results, close gracefully
  when `chosen_template` + result are set.
- **In-scope:** `agent/interviewer.py`, `agent/main.py`,
  `tests/test_routing.py`.
- **Proving command:** `uv run pytest tests/test_routing.py -v`
- **Completion:** full scripted interview lands a template and closes clean.

---

## Phase 3 — Offline analysis (runs parallel to Phase 2; needs only G1)

### G9 · `classify(transcript_xml) -> label` — `PENDING` · CX
- **Outcome:** `pipeline/classify.py` + `prompts/classify.md`. Authoritative
  classification: template + confidence + reasoning. Transcript passed as
  escaped XML. Importable and standalone.
- **In-scope:** `pipeline/classify.py`, `prompts/classify.md`,
  `tests/test_classify.py`, `tests/fixtures/`.
- **Proving command:** `uv run pytest tests/test_classify.py -v`

### G10 · `fill(label, transcript_xml, probe_result) -> typed_result` — `PENDING` · CX
- **Outcome:** `pipeline/fill.py` + `prompts/fill.md`. Structured-output
  slot-filling, char limits enforced per template.
- **In-scope:** `pipeline/fill.py`, `prompts/fill.md`, `tests/test_fill.py`.
- **Proving command:** `uv run pytest tests/test_fill.py -v`

### G11 · `render(typed_result) -> png` — `PENDING` · CC
- **Outcome:** `pipeline/render.py` + the four base images in
  `assets/templates/`. Pillow compositing, per-template layout.
- **In-scope:** `pipeline/render.py`, `assets/templates/*`,
  `tests/test_render.py`.
- **Proving command:** `uv run pytest tests/test_render.py -v` (asserts a
  non-empty PNG is produced for each of the four templates).

### G12 · `deliver(png, session)` — `PENDING` · CC
- **Outcome:** `pipeline/deliver.py`. QR code + per-session folder write;
  email behind a flag (degrades if no SendGrid key).
- **In-scope:** `pipeline/deliver.py`, `tests/test_deliver.py`.
- **Proving command:** `uv run pytest tests/test_deliver.py -v`

### G13 · Test harness — scripted personas — `PENDING` · CX + HERMES
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

### G14 · Background classifier (observer pattern) — `PENDING` · CC
- **Context:** The brief's step 6. `asyncio.create_task` after each user
  turn; Groq-hosted fast model; sub-200ms; timeout-guarded so failure never
  stalls the interview; writes signal weights to `userdata`.
- **In-scope:** `agent/classifier.py`, `agent/interviewer.py`,
  `tests/test_classifier.py`.
- **Proving command:** `uv run pytest tests/test_classifier.py -v`
- **Completion:** signal weights update mid-interview; a forced classifier
  timeout does not stall the conversation (asserted).

---

## Phase 5 — Kiosk + delivery

### G15 · Next.js kiosk frontend — `PENDING` · CC
- **Outcome:** `kiosk/` — three screens (Idle / Active / Complete), black
  background, single accent, `BarVisualizer`, fullscreen.
- **Proving command:** `cd kiosk && npm run build` succeeds.

### G16 · Kiosk hardware integration — `PENDING` · CC
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
