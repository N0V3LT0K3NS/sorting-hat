# Decision 0001 — Model selection (PROPOSAL — open for review)

**Status:** PROPOSED · awaiting review
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
| 3 | Offline classification | No (batch) | Strong structural reasoning — the authoritative sort |
| 4 | Offline slot-filling | No (batch) | Vivid, sharp, non-generic writing |
| 5 | STT | Yes | Conversational, agent-grade, fast endpointing |
| 6 | TTS | Yes | **Warmth** above all, at conversational latency |

---

## Proposed selection

| Job | Proposed primary | Fallback | Rationale |
|-----|------------------|----------|-----------|
| 1 · Interviewer brain | `anthropic/claude-haiku-4.5` (reasoning OFF) | `anthropic/claude-sonnet-4.6` | ~0.6–0.85s TTFT — the one capable model that fits the sub-800ms voice budget; follows long persona prompts literally. |
| 2 · Background classifier | `openai/gpt-oss-120b` **pinned to Groq** | `google/gemini-3.1-flash-lite` | 0.6–0.9s TTFT on Groq's LPU; cheap; the quality bar is just 4 signal floats. |
| 3 · Offline classification | `z-ai/glm-5.1` | `anthropic/claude-opus-4.7` | ~5× cheaper than Opus ($0.98/$3.08 vs $5/$25 per 1M); #1 SWE-Bench Pro; latency is free offline. |
| 4 · Offline slot-filling | `anthropic/claude-opus-4.7` | `z-ai/glm-5.1` (A/B in G17) | Best at the "uncomfortably accurate, not horoscope" register. GLM-5.1's proven strength is reasoning, not prose — so it is the challenger, decided by real output in G17. |
| 5 · STT | `deepgram/flux-general-en` | `assemblyai/universal-3-pro-streaming` | Purpose-built conversational STT; ASR + turn detection fused, sub-400ms endpointing. Already correct. |
| 6 · TTS | `inworld/inworld-tts-1.5-max` | `cartesia/sonic-3` (pinned) | #1 on blind-preference for emotional realism; ~250ms TTFA still keeps total sub-second. **See open question.** |

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

## Open questions for reviewers

1. **TTS warmth — Inworld vs Sonic-3.** The #1 ranking for
   `inworld-tts-1.5-max` is aggregate blind-preference ELO, which
   correlates with but is not identical to "warmth." Sonic-3 is the
   latency leader (~40ms vs ~250ms TTFA). Proposal: Inworld primary,
   Sonic-3 pinned fallback, **final call made by ear in G17** with the
   real interviewer script. Reviewers: agree, or pick Sonic-3 outright?
2. **Slot-filling — Opus vs GLM-5.1.** Proposal makes Opus 4.7 primary
   and GLM the A/B challenger. Reviewers: is that the right default, or
   should GLM lead given the cost difference?
3. **Sonnet 4.8 watch.** If Sonnet 4.8 ships before G17 and benchmarks
   show genuinely sub-second TTFT, the Job 1 choice should be revisited.

---

## If approved

A follow-up goal **G16.1 — model selection** applies this to
`agent/config.py`, `agent/classifier.py`, and the `pipeline/` LLM calls,
with the provider-routing config and the fallbacks wired in. No code
changes here — this PR is the decision record only.

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
