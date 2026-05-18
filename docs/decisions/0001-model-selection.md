# Decision 0001 — Model selection (PROPOSAL — open for review)

**Status:** PROPOSED · reviewed (Codex + Hermes) · amended v2 · awaiting approval
**Date:** 2026-05-17
**Scope:** which models each of sorting-hat's six model-jobs should use.

This document is a **proposal**, not a merged decision. It is opened as a PR
specifically so other agents and the operator can comment, challenge, and
amend before any config changes land. Nothing in `agent/` or `pipeline/`
changes in this PR — it is the decision doc only. A follow-up goal (G16.1)
applies the agreed config once this is approved.

---

## Why this exists

The repo's current defaults were set early and are partly stale:

- The live interviewer LLM defaults to `openai/gpt-4o-mini` — a generic,
  ~year-old choice, never deliberately selected for a persona-driven
  conversational interviewer.
- TTS is `cartesia/sonic-3`; STT is `deepgram/flux-general-en`.

The model landscape moves fast. This proposal is grounded in research
conducted 2026-05-17 (sources at the bottom), and was deliberately
pressure-tested against three operator challenges:

1. "What about the new OpenAI `gpt-realtime-2`?"
2. "What about cheaper powerful models like GLM-5.1?"
3. "Why Haiku when Sonnet is faster and smarter?"

The research findings on each are recorded below so reviewers can audit
the reasoning, not just the conclusion.

---

## The six model-jobs

sorting-hat makes model calls in six distinct places. They have genuinely
different requirements, so they should not all use the same model.

| # | Job | Latency-sensitive? | Primary requirement |
|---|-----|--------------------|---------------------|
| 1 | Live interviewer brain | **Yes — critical** | Sub-second TTFT; warm, persona-faithful conversation |
| 2 | Background classifier | Yes — ~1s budget, off critical path | Fast + cheap; modest quality bar (4 floats) |
| 3 | Offline classification | No (batch) | **Accuracy** — the authoritative once-per-session sort |
| 4 | Offline slot-filling | No (batch) | Vivid, sharp, non-generic writing |
| 5 | STT | Yes | Conversational, agent-grade, fast endpointing |
| 6 | TTS | Yes | **Warmth** above all, at conversational latency |

---

## Proposed selection

| Job | Proposed primary | Fallback | Rationale |
|-----|------------------|----------|-----------|
| 1 · Interviewer brain | `anthropic/claude-haiku-4.5` (reasoning OFF) | `anthropic/claude-sonnet-4.6` | ~0.6–0.85s TTFT — the one capable model that fits the sub-800ms voice budget; follows long persona prompts literally. |
| 2 · Background classifier | `openai/gpt-oss-120b` **pinned to Groq** | `google/gemini-3.1-flash-lite` | 0.6–0.9s TTFT on Groq's LPU; cheap; the quality bar is just 4 signal floats. |
| 3 · Offline classification | `anthropic/claude-opus-4.7` | `z-ai/glm-5.1` (challenger) | The authoritative sort runs **once per session** — accuracy, not cost, is the right axis. A cheap wrong sort ruins the whole portrait. GLM is the challenger, promoted only if G17 shows accuracy parity (see Amendment A). |
| 4 · Offline slot-filling | `anthropic/claude-opus-4.7` | `z-ai/glm-5.1` (A/B in G17) | Best at the "uncomfortably accurate, not horoscope" register. GLM-5.1's proven strength is reasoning, not prose — so it is the challenger, decided by real output in G17. |
| 5 · STT | `deepgram/flux-general-en` | `assemblyai/universal-3-pro-streaming` | Purpose-built conversational STT; ASR + turn detection fused, sub-400ms endpointing. Already correct. |
| 6 · TTS | `inworld/inworld-tts-1.5-max` | `cartesia/sonic-3` (pinned) | #1 on blind-preference for emotional realism. Latency cost is real — see the latency budget below — but acceptable for a seated interview. Final call by ear in G17. |

### Provider routing

- Job 1: `provider: { sort: "latency" }` — prefer lowest-p50 endpoint.
- Job 2: `provider: { only: ["Groq"] }` — pinning is the difference between
  hitting and blowing the 1s budget (~10× backend throughput spread on
  open models). Keep an unpinned fallback for graceful degradation.
- Jobs 3 & 4: latency-irrelevant — default or price-sorted routing.

---

## The three challenges, and what research found

### Challenge 1 — "What about `gpt-realtime-2`?"
**Real.** Released 2026-05-11 — GPT-5-class reasoning, speech-to-speech.
**But the wrong architecture here.** A speech-to-speech model emits no
reliable interim transcripts (user transcriptions arrive delayed, often
after the agent has replied). sorting-hat's offline pipeline depends on a
clean transcript, and the supervisor routes on what the user just said.
LiveKit's own docs steer structured agent logic (ordered questions, typed
sub-task probes, persona adherence) to the cascaded STT→LLM→TTS pipeline.
Also expensive ($32/$64 per 1M audio tokens). **Verdict: keep the pipeline.**

### Challenge 2 — "What about GLM-5.1?"
**Real, and it changes the offline recommendation.** `z-ai/glm-5.1` on
OpenRouter at ~$0.98/$3.08 per 1M — ~5× cheaper than Opus 4.7, and #1 on
SWE-Bench Pro. Moved into the offline classification job as primary.
Caveat: its proven strength is long-horizon reasoning, not the vivid-prose
register slot-filling needs — so for Job 4 it is the A/B challenger, not
the assumed winner. **The operator's instinct was correct.**

### Challenge 3 — "Why Haiku, not faster/smarter Sonnet?"
**There is no Sonnet 4.7** (current Sonnet is 4.6; a 4.8 is rumored,
unverified as shipped). On real numbers: Sonnet 4.6 is ~1.0–1.24s TTFT vs
Haiku 4.5's ~0.6–0.85s — roughly **2× slower to first token**. "Super
fast" is not true of Sonnet for a voice loop. The interviewer's in-call
job is bounded (persona + ordered questions + warm follow-ups), not
frontier reasoning — so the dead air from a 2× slower first token costs
more rapport than the intelligence gain buys. **Haiku for the live loop
holds up.** The operator's underlying instinct — put intelligence where it
matters — is honored: the offline jobs (3, 4) run the strong models.

---

## Review amendments (v2)

This doc was reviewed by two independent agents (Codex CLI, Hermes) on
PR #22. Both returned "approve with amendments" and **independently
converged** on the same central flaw. The amendments are folded in above;
recorded here for the trail.

### Amendment A — Job 3 flipped to Opus primary
The v1 proposal made GLM-5.1 the authoritative-classification primary on
cost. Both reviewers rejected this: the authoritative sort runs **once per
session**, so cost is the wrong axis — a confidently wrong sort ruins the
whole portrait, and GLM's evidence (SWE-Bench Pro) measures code reasoning,
not persona classification. **Fixed:** Job 3 is now Opus primary, GLM
challenger — matching Job 4. GLM is promoted to primary only if the G17
test harness shows classification-accuracy parity on real transcripts.

### Amendment B — the "sub-second" latency claim was wrong
v1 claimed the voice loop stays "total sub-second." It does not. Honest
budget: STT endpointing ~400ms + Haiku TTFT ~700ms + Inworld TTFA ~250ms
≈ **~1.35s** end-of-speech to start-of-audio. That is still a good
conversational latency for a seated 10–15 min interview — but it is not
sub-second, and the doc should not claim otherwise. The metric that most
affects *felt* responsiveness in a cascaded pipeline is **streaming
throughput** (tokens pipe to TTS before the response finishes), not TTFT
alone — which is a further reason Haiku wins Job 1. The G2 latency
instrumentation already in `agent/main.py` measures the real number; G17
confirms it against live calls.

### Amendment C — acknowledged gaps, deferred to G17 (not built now)
Reviewers flagged several things the proposal did not cover. Per YAGNI,
these are **acknowledged here, not solved with new machinery now** —
they belong to the G17 live-validation pass:
- **G17 TTS ear-test needs an exit condition** — decide it in G17: a
  small set of script excerpts, the operator listens, picks Inworld or
  Sonic-3. No elaborate rubric needed; it is a taste call.
- **PII / provider data retention** — a kiosk records real voices.
  Before any public deployment, confirm Deepgram / Inworld / OpenRouter
  retention terms and state the data policy. Tracked as a G17 gate, not
  a model-selection question.
- **Classifier divergence** — the background classifier (Job 2) only
  *nudges* signal weights; Job 3 is authoritative and overrides. That is
  the reconciliation — no separate logic required. Job 2 informs the
  live probe choice; Job 3 decides the final sort. Documented, done.
- **Provider failure** — the fallbacks named per job are the policy.
  LiveKit/OpenRouter handle retry; if a fallback is ever needed
  mid-interview the call simply uses the fallback model. No circuit
  breaker is warranted at this scale (one kiosk, one session at a time).

### Still open
- **Sonnet 4.8 watch.** If it ships before G17 with genuinely sub-second
  TTFT, revisit the Job 1 choice. Until then, Haiku 4.5.

---

## If approved

A follow-up goal **G16.1 — model selection** applies the amended table to
`agent/config.py`, `agent/classifier.py`, and the `pipeline/` LLM calls,
with the per-job fallbacks and provider routing wired in. No code changes
in this PR — it is the decision record only.

---

## Sources (research, 2026-05-17)

- OpenAI — Advancing voice intelligence with new models in the API
- LiveKit Docs — Realtime models overview; Voice Agent Architecture;
  Multi-Agent Handoffs
- OpenRouter — GLM-5.1, Anthropic models, Provider Routing docs
- Anthropic — Claude Sonnet 4.6; Claude release notes
- Artificial Analysis — Claude 4.5 Haiku / Sonnet 4.6 provider latency
- Inworld 2026 TTS benchmarks; Cekura best-TTS-for-voice-agents 2026
- Deepgram — Introducing Flux; Flux configuration
- Daily.co / Softcery — benchmarking LLMs for voice-agent use cases
