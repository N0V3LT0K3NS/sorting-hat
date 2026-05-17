# Borrowed interview craft

Reference notes mined from `prompter` (the codebase behind sayinterviews.com).
We borrow **prompt craft only** — zero infrastructure. `prompter` was built
mid-2025; its engineering workarounds are obsoleted by the current LiveKit
Agents stack. What survives is the human-discovered interviewing craft below.

This file is a reference for whoever writes the persona and probe prompts
(goals G3–G7). It is not code.

---

## 1. The DRILL IN / ZOOM OUT rule

Every follow-up question must do one of exactly two things:

- **DRILL IN** — seize a thrown-away concrete detail and go closer.
  > Respondent: "We went on this walk in McCarren Park and I just felt
  > really inspired."
  > Weak follow-up: "What was inspiring?"
  > Strong follow-up: "That walk in McCarren Park — what were you two
  > talking about?"

- **ZOOM OUT** — get more abstract: what it means, what it says about them,
  the pattern.
  > "How rare is it for you to feel that kind of alignment with someone?"

This is the engine of the conditional probes. The base questions are fixed;
the follow-ups live or die on this rule.

## 2. Banned phrasings

Sounding like an interviewer is the #1 failure mode. Hard-ban:

- "in what way" — a dead giveaway you're interviewing
- asking about "the process" or "the approach" — too abstract
- compound questions (two questions in one)
- anything that "sounds like it came from an interview guide"

## 3. Question output discipline

- Output ONLY the question. No preamble.
- Under ~200 characters.
- One question only.
- Make it sound like something you'd actually say out loud.

## 4. DRIFT CHECK (a thinking-block scaffold)

Inject self-interrogation into the model's reasoning each turn:

- "Am I about to ask something conversationally interesting that doesn't
  serve the interview goal?"
- "What have I learned so far?"
- "What am I still uncertain about?"

## 5. Rhythm rules

- After 2–3 questions on one thread, either go deeper or shift.
- Vary question length — some should be just a few words.
- Echo their exact words back sometimes.
- Circular recall: "You mentioned earlier that you felt inspired — I want
  to stay there."

## 6. Transcript handoff format

When passing the transcript to the offline analysis prompts (classify, fill),
wrap Q&A pairs in **escaped XML**, not JSON — it preserves verbatim quotes
unambiguously and avoids escaping fragility. The meme content must feel
uncomfortably accurate, so quote fidelity matters.
