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
**Real.** Announced 2026-05-07 — GPT-5-class reasoning, speech-to-speech.
**But the wrong architecture here.** A speech-to-speech realtime model
*does* emit transcription events, but with async-timing caveats: the
user-audio transcript is produced asynchronously and can arrive after the
model has already responded, so it is not a reliable *interim* transcript
for turn-by-turn routing. sorting-hat's supervisor routes on what the user
just said, and the offline pipeline depends on a clean ordered transcript;
the cascaded pipeline gives both directly. LiveKit's own docs also steer
structured agent logic (ordered questions, typed sub-task probes, persona
adherence) to the cascaded STT→LLM→TTS pipeline. Realtime audio is also
expensive ($32/$64 per 1M audio tokens). **Verdict: keep the pipeline** —
not because realtime is incapable, but because the cascade is the better
fit for this app's structured, transcript-dependent design.

### Challenge 2 — "What about GLM-5.1?"
**Real, and worth including — as a challenger, not the primary.** `z-ai/glm-5.1`
on OpenRouter at ~$0.98/$3.08 per 1M — ~5× cheaper than Opus 4.7, and #1
on SWE-Bench Pro. The v1 draft made it the offline-classification primary
on cost; review (Amendment A) corrected that — the once-per-session
authoritative sort optimizes for accuracy, not cost. GLM-5.1 is therefore
the **challenger** for both offline jobs (3 and 4), promoted to primary
only if the G17 harness shows accuracy parity on real transcripts. Its
proven strength is long-horizon reasoning, not the vivid-prose register
slot-filling needs — another reason it earns its place by measured output,
not assumption. **The operator's instinct — that GLM belongs in the
picture — was correct; review placed it correctly.**

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

Model facts in this doc change fast — every claim is linked to a primary
or dated secondary source so a future reviewer can re-verify.

**OpenAI realtime (Challenge 1)**
- OpenAI — Advancing voice intelligence with new models in the API:
  https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/
- TechCrunch — OpenAI launches new voice intelligence features (2026-05-07):
  https://techcrunch.com/2026/05/07/openai-launches-new-voice-intelligence-features-in-its-api/

**LiveKit — pipeline vs realtime, voice architecture**
- LiveKit Docs — Realtime models overview:
  https://docs.livekit.io/agents/models/realtime/
- LiveKit — Voice Agent Architecture: STT/LLM/TTS pipelines explained:
  https://livekit.com/blog/voice-agent-architecture-stt-llm-tts-pipelines-explained
- LiveKit Docs — Workflows / Multi-Agent Handoffs:
  https://docs.livekit.io/agents/build/workflows/

**LLMs — OpenRouter, Anthropic, GLM-5.1**
- OpenRouter — GLM-5.1: https://openrouter.ai/z-ai/glm-5.1
- OpenRouter — Provider Routing docs:
  https://openrouter.ai/docs/features/provider-routing
- OpenRouter — Anthropic models: https://openrouter.ai/anthropic
- Anthropic — Claude Sonnet 4.6: https://www.anthropic.com/news/claude-sonnet-4-6
- Anthropic — Claude release notes:
  https://support.claude.com/en/articles/12138966-release-notes
- Artificial Analysis — Claude 4.5 Haiku provider latency:
  https://artificialanalysis.ai/models/claude-4-5-haiku/providers
- Artificial Analysis — Claude Sonnet 4.6 provider latency:
  https://artificialanalysis.ai/models/claude-sonnet-4-6/providers

**STT / TTS**
- LiveKit Docs — STT models: https://docs.livekit.io/agents/models/stt/
- LiveKit Docs — TTS models: https://docs.livekit.io/agents/models/tts/
- Deepgram — Introducing Flux:
  https://deepgram.com/learn/introducing-flux-conversational-speech-recognition
- Deepgram — Flux configuration:
  https://developers.deepgram.com/docs/flux/configuration
- Inworld — 2026 TTS benchmarks:
  https://inworld.ai/resources/best-voice-ai-tts-apis-for-real-time-voice-agents-2026-benchmarks
- Cekura — Best TTS for voice agents 2026:
  https://www.cekura.ai/blogs/best-tts-for-ai-voice-agents

**Voice-agent LLM latency**
- Daily.co — Benchmarking LLMs for voice-agent use cases:
  https://www.daily.co/blog/benchmarking-llms-for-voice-agent-use-cases/
- Softcery — Choosing an LLM for voice agents:
  https://softcery.com/lab/ai-voice-agents-choosing-the-right-llm
- Daily.co / Softcery — benchmarking LLMs for voice-agent use cases
