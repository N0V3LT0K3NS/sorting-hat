# Scripted-persona test harness

The project brief says this harness matters **more than unit tests** — it is
the real proof the sort works. `tests/test_classify.py` proves the plumbing
of `pipeline/classify.py` (XML escaping, parsing, clamping, error typing).
This harness proves the harder thing: that realistic, multi-turn interview
transcripts of recognisable *people* land on the right shape, and that
genuinely in-between people are handled by an **explicit, asserted policy**
rather than an accidental coin-flip.

Runner: [`tests/test_harness.py`](../test_harness.py) ·
Prove with: `uv run pytest tests/test_harness.py -v`

## How it runs offline

`classify()` calls OpenRouter. The harness **never** does. It injects a
*simulated classifier client* in place of the OpenAI-compatible SDK client.
The simulation is not a canned answer — it is a small transparent classifier:
it reads the transcript, scores it against the four locked shape
vocabularies, picks the dominant shape, and derives a confidence from *how
far ahead* the winner is. A persona that leans evenly on two shapes produces
two close scores → low confidence. So the harness tests the genuine
classification **contract** (transcript in, validated `ClassificationResult`
out, confidence reflecting real ambiguity) deterministically and with no
network.

## The four locked shapes

| Template      | Shape      | Signature |
|---------------|------------|-----------|
| `iceberg`     | depth      | vertical layering, a hidden bottom |
| `two_buttons` | tension    | an unresolved pull between two options |
| `compass`     | position   | coordinates on axes, settled |
| `arc`         | trajectory | a before-and-after transformation |

## The ambiguity policy

`CLEAN_THRESHOLD = 0.75` (defined once in `test_harness.py`).

- A **clean** sort returns `confidence >= 0.75` **and** the correct template.
- A sort with `confidence < 0.75` is **ambiguous** (`is_ambiguous()`):
  downstream it would route to a tie-break / human review, never straight to
  a portrait.
- A two-template **hybrid** is asserted on **both**: (a) `confidence < 0.75`
  — no false certainty about an in-between person; **and** (b) the chosen
  template is **one of the two plausible** templates for that blend — never
  a third, off-axis shape.
- The **genuinely ambiguous** persona is asserted only on low confidence;
  its label may be any of the four — the contract is "the classifier admits
  it isn't sure", not "it guesses a particular box".

## Clean personas (4) — reused from `tests/fixtures/`

Per the goal, the clean set reuses the existing fixture transcripts rather
than duplicating them. Each is a realistic multi-turn interview (base
questions + answers + probe) landing on exactly one shape.

| Persona | File | Shape it represents |
|---------|------|---------------------|
| The capable one | `fixtures/iceberg.xml` | **iceberg / depth** — a calm surface over exhaustion, over fear, over a self no one has met. Talks only in floors and what is below them. |
| The two-city dilemma | `fixtures/two_buttons.xml` | **two_buttons / tension** — stay vs. move home, two years circling. Both options equally good and impossible; choosing one kills the other. |
| The settled one | `fixtures/compass.xml` | **compass / position** — describes the self as coordinates on axes, "more this than that", a stable spot, not torn, not moving. |
| The transformed one | `fixtures/arc.xml` | **arc / trajectory** — a clear before-and-after, a catalyst that cracked the old self open, in the messy middle of becoming. |

## Hybrid personas (3) — `tests/harness/`

| Persona | File | Sits between | Why ambiguous / what the harness expects |
|---------|------|--------------|-------------------------------------------|
| iceberg/arc blend | `hybrid_iceberg_arc.xml` | iceberg ↔ arc | Describes a real transformation (before-me / after-me) **whose destination is downward** — the old self is *buried*, a bottom layer. Depth and trajectory are the same motion. Expect: `confidence < 0.75` **and** template ∈ {iceberg, arc}. |
| compass/two_buttons blend | `hybrid_compass_two_buttons.xml` | compass ↔ two_buttons | Knows roughly *where* it sits on an axis (compass) but the needle "won't hold still" — a position that is also an unresolved pull. Expect: `confidence < 0.75` **and** template ∈ {compass, two_buttons}. |
| genuinely ambiguous | `hybrid_ambiguous.xml` | none / all | An "average person in an average stretch": no hidden depth, no turning point, no fork, no strong axis position. No shape dominates. Expect: `confidence < 0.75`; label **not** asserted — the point is the classifier admits no shape fits. |

A cross-cutting test (`test_clean_personas_outrank_hybrids_on_confidence`)
asserts every clean sort is strictly more confident than every hybrid sort,
so the clean/hybrid split can never drift into being accidental.
